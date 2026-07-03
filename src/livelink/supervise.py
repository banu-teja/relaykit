"""Supervision WebSocket channel: /supervise/{session_id}.

Provides a bidirectional event stream for external supervisors to observe
and interact with running agent sessions in real-time.

Capabilities:
- Observe: Stream all session events (via EventBus subscribe_all)
- Approve/Reject: Resolve pending approval gates (via InputManager.resolve)
- Cancel: Trigger cooperative shutdown (via CancellationToken.cancel)
- Inspect: Get point-in-time session state snapshot
- Reconnect: Replay missed events from EventBus history buffer
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from typing import Any

logger = logging.getLogger(__name__)

_SESSION_REGISTRY: dict[str, Any] = {}
_SUBSCRIBE_TIMEOUT: float = 5.0


def register_session(session_id: str, session: Any) -> None:
    _SESSION_REGISTRY[session_id] = session


def unregister_session(session_id: str) -> None:
    _SESSION_REGISTRY.pop(session_id, None)


def get_session(session_id: str) -> Any | None:
    return _SESSION_REGISTRY.get(session_id)


async def handle_supervision(ws: Any, session_id: str) -> None:
    """Handle a supervision WebSocket connection for a given session."""
    session = get_session(session_id)
    if session is None:
        await ws.close(4404, "session_not_found")
        return

    event_bus = session.event_bus
    if event_bus is None:
        await ws.close(4404, "supervision_not_enabled")
        return

    input_manager = session.input_manager
    cancellation_token = session.cancellation_token

    try:
        subscribe_msg = await asyncio.wait_for(ws.recv(), timeout=_SUBSCRIBE_TIMEOUT)
    except (asyncio.TimeoutError, Exception):
        await ws.close(4408, "subscribe_timeout")
        return

    cmd = _parse(subscribe_msg)
    if cmd is None or cmd.get("type") != "subscribe":
        await _send(
            ws,
            {
                "type": "error",
                "cmd_id": "",
                "code": "invalid_command",
                "message": "First message must be subscribe",
            },
        )
        await ws.close(4408, "subscribe_timeout")
        return

    supervision_token = os.environ.get("LIVELINK_SUPERVISION_TOKEN")
    if supervision_token:
        token = cmd.get("token")
        if not token or token != supervision_token:
            await _send(
                ws,
                {
                    "type": "error",
                    "cmd_id": cmd.get("cmd_id", ""),
                    "code": "unauthorized",
                    "message": "Invalid or missing supervision token",
                },
            )
            await ws.close(4401, "unauthorized")
            return

    pending = _get_pending_approvals(input_manager)
    await _send(
        ws,
        {
            "type": "connected",
            "session_id": session_id,
            "model": session.agent.model,
            "state": "ended" if not session.is_connected else "running",
            "pending_approvals": pending,
            "replay_from": None,
        },
    )

    cmd_id = cmd.get("cmd_id", "")
    after_event_id = cmd.get("after_event_id")

    if after_event_id:
        history = event_bus.history(limit=1000)
        found_idx = None
        for i, ev in enumerate(history):
            if ev.event_id == after_event_id:
                found_idx = i
                break
        if found_idx is None:
            await _send(
                ws,
                {
                    "type": "error",
                    "cmd_id": cmd_id,
                    "code": "replay_gap",
                    "message": "Event not in history buffer",
                },
            )
            return
        for ev in history[found_idx + 1 :]:
            await _send(ws, _event_to_wire(ev))

    await _send(ws, {"type": "ack", "cmd_id": cmd_id, "detail": {}})

    send_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

    async def _event_handler(event: Any) -> None:
        try:
            send_queue.put_nowait(_event_to_wire(event))
        except asyncio.QueueFull:
            logger.warning("Supervision send queue full, dropping event")

    sub_id = event_bus.subscribe_all(_event_handler)
    try:
        sender_task = asyncio.create_task(_sender_loop(ws, send_queue))
        receiver_task = asyncio.create_task(
            _receiver_loop(ws, send_queue, session, input_manager, cancellation_token)
        )

        done, pending_tasks = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending_tasks:
            t.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)
    finally:
        event_bus.unsubscribe(sub_id)


async def _sender_loop(ws: Any, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Forward queued events to the supervisor WebSocket."""
    while True:
        msg = await queue.get()
        await _send(ws, msg)


async def _receiver_loop(
    ws: Any,
    send_queue: asyncio.Queue[dict[str, Any]],
    session: Any,
    input_manager: Any,
    cancellation_token: Any,
) -> None:
    """Process incoming commands from the supervisor."""
    async for raw in ws:
        cmd = _parse(raw)
        if cmd is None:
            send_queue.put_nowait(
                {
                    "type": "error",
                    "cmd_id": "",
                    "code": "invalid_command",
                    "message": "Malformed JSON",
                }
            )
            continue

        cmd_type = cmd.get("type", "")
        cmd_id = cmd.get("cmd_id", "")

        if cmd_type == "resolve":
            _handle_resolve(cmd, cmd_id, input_manager, send_queue)
        elif cmd_type == "cancel":
            _handle_cancel(cmd, cmd_id, cancellation_token, send_queue)
        elif cmd_type == "inspect":
            _handle_inspect(cmd_id, session, input_manager, cancellation_token, send_queue)
        else:
            send_queue.put_nowait(
                {
                    "type": "error",
                    "cmd_id": cmd_id,
                    "code": "invalid_command",
                    "message": f"Unknown type: {cmd_type}",
                }
            )


def _handle_resolve(
    cmd: dict[str, Any], cmd_id: str, input_manager: Any, queue: asyncio.Queue[dict[str, Any]]
) -> None:
    request_id = cmd.get("request_id", "")
    answer = cmd.get("answer", "")

    if not input_manager:
        queue.put_nowait(
            {
                "type": "error",
                "cmd_id": cmd_id,
                "code": "session_ended",
                "message": "No input manager",
            }
        )
        return

    from livelink.supervision.hitl import InputStatus

    try:
        status = input_manager.get_status(request_id)
    except KeyError:
        queue.put_nowait(
            {
                "type": "error",
                "cmd_id": cmd_id,
                "code": "unknown_request",
                "message": f"No pending request: {request_id}",
            }
        )
        return

    if status == InputStatus.ANSWERED:
        queue.put_nowait({"type": "ack", "cmd_id": cmd_id, "detail": {"already_resolved": True}})
        return
    if status is None:
        queue.put_nowait(
            {
                "type": "error",
                "cmd_id": cmd_id,
                "code": "unknown_request",
                "message": f"No pending request: {request_id}",
            }
        )
        return

    try:
        input_manager.resolve(request_id, answer, source="supervisor")
    except KeyError:
        queue.put_nowait({"type": "ack", "cmd_id": cmd_id, "detail": {"already_resolved": True}})
        return

    queue.put_nowait({"type": "ack", "cmd_id": cmd_id, "detail": {}})


def _handle_cancel(
    cmd: dict[str, Any], cmd_id: str, cancellation_token: Any, queue: asyncio.Queue[dict[str, Any]]
) -> None:
    reason = cmd.get("reason", "supervisor_cancelled")

    if cancellation_token is None:
        queue.put_nowait(
            {
                "type": "error",
                "cmd_id": cmd_id,
                "code": "session_ended",
                "message": "No cancellation token",
            }
        )
        return

    if cancellation_token.is_cancelled:
        queue.put_nowait({"type": "ack", "cmd_id": cmd_id, "detail": {"already_cancelled": True}})
        return

    cancellation_token.cancel(reason=reason)
    queue.put_nowait({"type": "ack", "cmd_id": cmd_id, "detail": {}})


def _handle_inspect(
    cmd_id: str,
    session: Any,
    input_manager: Any,
    cancellation_token: Any,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    detail = {
        "session_id": session.session_id,
        "model": session.agent.model,
        "turn_count": session.turn_count,
        "state": "running" if session.is_connected else "ended",
        "is_cancelled": cancellation_token.is_cancelled if cancellation_token else False,
        "pending_approvals": _get_pending_approvals(input_manager),
    }
    queue.put_nowait({"type": "ack", "cmd_id": cmd_id, "detail": detail})


def _get_pending_approvals(input_manager: Any) -> list[dict[str, Any]]:
    if input_manager is None:
        return []
    return [
        {
            "request_id": req.request_id,
            "question": req.question,
            "options": req.options,
            "created_at": req.created_at,
        }
        for req in input_manager.pending_requests()
    ]


def _event_to_wire(event: Any) -> dict[str, Any]:
    """Convert a SupervisionEvent to wire format."""
    payload = asdict(event)
    event_type = type(event).__name__
    event_id = payload.pop("event_id", "")
    timestamp = payload.pop("timestamp", 0.0)
    payload.pop("source", None)
    return {
        "type": "event",
        "event_type": event_type,
        "event_id": event_id,
        "timestamp": timestamp,
        "payload": payload,
    }


def _parse(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None


async def _send(ws: Any, msg: dict[str, Any]) -> None:
    await ws.send(json.dumps(msg))
