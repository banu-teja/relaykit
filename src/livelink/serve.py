"""agent.serve(): first-class runtime entrypoint.

Encapsulates WebSocket server, per-connection session wiring, and
default browser UI. Uses Runner internally — one runtime path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from livelink.agent import LiveAgent

logger = logging.getLogger(__name__)

_DEFAULT_DRAIN_TIMEOUT: float = 300.0
_DEFAULT_MAX_SESSIONS: int = 100


class _ServerState:
    __slots__ = (
        "shutdown_event",
        "active_sessions",
        "start_time",
        "draining",
        "max_sessions",
        "drain_timeout",
    )

    def __init__(self, *, max_sessions: int, drain_timeout: float) -> None:
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.active_sessions: int = 0
        self.start_time: float = time.monotonic()
        self.draining: bool = False
        self.max_sessions: int = max_sessions
        self.drain_timeout: float = drain_timeout


async def serve(
    agent: LiveAgent,
    *,
    host: str | None = None,
    port: int | None = None,
    ui: bool = True,
    ui_path: str | Path | None = None,
    deps: Any = None,
    cors: bool = False,
    max_sessions: int | None = None,
    drain_timeout: float | None = None,
) -> None:
    """Start a WebSocket server for the agent with optional browser UI.

    This is the runtime entry point. Each WebSocket connection gets its own
    session managed by Runner.

    Args:
        agent: The agent to serve.
        host: Bind address. Defaults to LIVELINK_HOST env var or "0.0.0.0".
        port: Bind port. Defaults to PORT env var or 8000.
        ui: Serve built-in audio client at /.
        ui_path: Path to custom static HTML file to serve instead of built-in UI.
        deps: Dependency injection object passed to tools via ToolContext.
        cors: Enable CORS headers for cross-origin requests.
        max_sessions: Maximum concurrent sessions. Defaults to LIVELINK_MAX_SESSIONS or 100.
        drain_timeout: Seconds to wait for sessions to finish on shutdown. Defaults to 300.
    """
    resolved_host = host or os.environ.get("LIVELINK_HOST", "0.0.0.0")
    resolved_port = port or int(os.environ.get("PORT", os.environ.get("LIVELINK_PORT", "8000")))
    resolved_max = max_sessions or int(
        os.environ.get("LIVELINK_MAX_SESSIONS", str(_DEFAULT_MAX_SESSIONS))
    )
    resolved_drain = drain_timeout if drain_timeout is not None else _DEFAULT_DRAIN_TIMEOUT
    try:
        import websockets
        import websockets.http11
        import websockets.datastructures
        from websockets.asyncio.server import serve as ws_serve
    except ImportError:
        raise ImportError(
            "websockets is required for agent.serve(). Install it with: pip install livelink[serve]"
        ) from None

    from livelink.runner import Runner
    from livelink.supervise import handle_supervision
    from livelink.transport import WebSocketTransport

    state = _ServerState(max_sessions=resolved_max, drain_timeout=resolved_drain)
    html_content = _load_ui(ui, ui_path, resolved_host, resolved_port)

    def _signal_handler() -> None:
        if state.draining:
            return
        state.draining = True
        logger.info("Shutdown signal received, draining %d session(s)…", state.active_sessions)
        state.shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    async def handle_connection(connection: Any) -> None:
        path = getattr(connection, "request", None)
        if path is not None:
            path = getattr(path, "path", None)
        path = path or ""

        if path.startswith("/supervise/"):
            session_id = path.removeprefix("/supervise/").strip("/")
            await handle_supervision(connection, session_id)
            return

        if state.draining:
            await connection.close(1001, "Server shutting down")
            return
        if state.active_sessions >= state.max_sessions:
            await connection.close(1013, "Server at capacity")
            return
        state.active_sessions += 1
        try:
            transport = WebSocketTransport(connection)
            await Runner.run(agent, transport, deps=deps)
        finally:
            state.active_sessions -= 1

    def process_request(connection: Any, request: Any) -> Any:
        if request.path in ("/", "") and html_content:
            headers = websockets.datastructures.Headers(
                {
                    "Content-Type": "text/html; charset=utf-8",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                }
            )
            if cors:
                headers["Access-Control-Allow-Origin"] = "*"
            return websockets.http11.Response(200, "OK", headers, html_content.encode())
        if request.path == "/health":
            status = "draining" if state.draining else "ok"
            code = 503 if state.draining else 200
            body = json.dumps(
                {
                    "status": status,
                    "sessions": state.active_sessions,
                    "max_sessions": state.max_sessions,
                    "uptime_seconds": round(time.monotonic() - state.start_time, 2),
                }
            )
            return websockets.http11.Response(
                code,
                "OK" if code == 200 else "Service Unavailable",
                websockets.datastructures.Headers(
                    {
                        "Content-Type": "application/json",
                        "X-Content-Type-Options": "nosniff",
                        "X-Frame-Options": "DENY",
                    }
                ),
                body.encode(),
            )
        return None

    url = f"http://{resolved_host}:{resolved_port}"
    logger.info("LiveLink agent serving at %s", url)
    print(f"LiveLink agent → {url}")

    async with ws_serve(
        handle_connection,
        resolved_host,
        resolved_port,
        process_request=process_request,
    ):
        await state.shutdown_event.wait()

    await _drain_sessions(state)
    logger.info("Shutdown complete")


async def _drain_sessions(state: _ServerState) -> None:
    if state.active_sessions == 0:
        return
    logger.info(
        "Waiting up to %.0fs for %d session(s) to finish…",
        state.drain_timeout,
        state.active_sessions,
    )
    deadline = time.monotonic() + state.drain_timeout
    while state.active_sessions > 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    if state.active_sessions > 0:
        logger.warning("Force-closing %d remaining session(s)", state.active_sessions)


def _load_ui(ui: bool, ui_path: str | Path | None, host: str, port: int) -> str | None:
    """Load HTML content for the browser UI."""
    if not ui and ui_path is None:
        return None

    if ui_path is not None:
        path = Path(ui_path)
        if not path.exists():
            raise FileNotFoundError(f"UI file not found: {path}")
        return path.read_text(encoding="utf-8")

    from livelink._ui import DEFAULT_HTML

    return DEFAULT_HTML.replace(
        "location.host",
        f"'{host}:{port}'" if host not in ("localhost", "0.0.0.0") else "location.host",
    )
