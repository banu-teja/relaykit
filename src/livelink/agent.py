"""LiveAgent: declarative agent configuration (frozen dataclass).

An agent is pure data — model, instructions, tools, hooks, config.
It carries no runtime state and is reusable across sessions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, TYPE_CHECKING

from livelink.tools import Tool, ToolRegistry

if TYPE_CHECKING:
    from livelink.delegation import DelegatedBackend
    from livelink.guardrails import Guardrail
    from livelink.handoff import Handoff
    from livelink.hooks import AgentHooks
    from livelink.session import RealtimeSession


@dataclass(frozen=True)
class AgentConfig:
    """Provider and runtime configuration for a LiveAgent."""

    max_history: int = 100
    session_ttl: float | None = None
    max_tool_concurrency: int = 10
    max_tool_rounds: int = 10
    tool_limit_action: Literal["close", "warn"] = "close"
    provider_options: dict[str, Any] = field(default_factory=dict)


class LiveAgent:
    """Declarative agent definition.

    Separates configuration (what the agent IS) from runtime (how it executes).
    Create sessions via ``agent.session()`` to get a runtime instance.

    Usage::

        agent = LiveAgent(
            model="gemini/gemini-2.5-flash-native-audio",
            instructions="You are a helpful assistant.",
            voice="Puck",
        )

        @agent.tool
        async def get_weather(city: str) -> str:
            \"\"\"Get current weather for a city.\"\"\"
            return "sunny"

        session = agent.session(deps=my_deps)
        await session.run(transport)
    """

    __slots__ = (
        "_model",
        "_instructions",
        "_voice",
        "_tools",
        "_hooks",
        "_config",
        "_handoffs",
        "_delegations",
        "_input_guardrails",
        "_output_guardrails",
        "_frozen",
    )

    def __init__(
        self,
        model: str,
        *,
        instructions: str | Callable[..., str] = "",
        voice: str | None = None,
        tools: list[Tool] | None = None,
        hooks: AgentHooks | None = None,
        config: AgentConfig | dict[str, Any] | None = None,
        handoffs: list[Handoff] | None = None,
        delegations: list[DelegatedBackend] | None = None,
        input_guardrails: list[Guardrail] | None = None,
        output_guardrails: list[Guardrail] | None = None,
    ) -> None:
        if isinstance(config, dict):
            config = AgentConfig(provider_options=config)
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_instructions", instructions)
        object.__setattr__(self, "_voice", voice)
        object.__setattr__(self, "_tools", ToolRegistry(tools or []))
        object.__setattr__(self, "_hooks", hooks)
        object.__setattr__(self, "_config", config or AgentConfig())
        object.__setattr__(self, "_handoffs", list(handoffs or []))
        object.__setattr__(self, "_delegations", list(delegations or []))
        object.__setattr__(self, "_input_guardrails", list(input_guardrails or []))
        object.__setattr__(self, "_output_guardrails", list(output_guardrails or []))
        object.__setattr__(self, "_frozen", False)

        for h in self._handoffs:
            self._tools.add(h.to_tool())

        for d in self._delegations:
            from livelink.delegation import generate_delegation_tool

            self._tools.add(generate_delegation_tool(d))

    @property
    def model(self) -> str:
        return self._model

    @property
    def instructions(self) -> str | Callable[..., str]:
        return self._instructions

    @property
    def voice(self) -> str | None:
        return self._voice

    @property
    def tools(self) -> ToolRegistry:
        return self._tools

    @property
    def hooks(self) -> AgentHooks | None:
        return self._hooks

    @property
    def config(self) -> AgentConfig:
        return self._config

    @property
    def handoffs(self) -> list[Handoff]:
        return self._handoffs

    @property
    def delegations(self) -> list[DelegatedBackend]:
        return self._delegations

    @property
    def input_guardrails(self) -> list[Guardrail]:
        return self._input_guardrails

    @property
    def output_guardrails(self) -> list[Guardrail]:
        return self._output_guardrails

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        requires_approval: bool = False,
    ) -> Any:
        """Decorator to register a tool on this agent.

        Can be used bare or with parameters::

            @agent.tool
            async def place_order(item: str) -> str:
                \"\"\"Place an order.\"\"\"
                return f"Ordered {item}"

            @agent.tool(requires_approval=True)
            async def delete_account(user_id: str) -> str:
                \"\"\"Delete account. Requires approval.\"\"\"
                ...
        """
        if self._frozen:
            raise RuntimeError(
                "Cannot register tools after session() has been called. "
                "Register all tools before creating sessions."
            )

        if fn is not None:
            self._tools.register_function(fn, requires_approval=requires_approval)
            return fn

        def _decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            self._tools.register_function(f, requires_approval=requires_approval)
            return f

        return _decorator

    def session(self, *, deps: Any = None, config: Any = None) -> RealtimeSession:
        """Create a new runtime session for this agent.

        Each session is an independent conversation instance.
        The agent becomes frozen after the first session is created.

        Args:
            deps: Dependency injection object passed to tool functions.
            config: Optional SessionConfig for supervision features.
        """
        object.__setattr__(self, "_frozen", True)
        from livelink.session import RealtimeSession

        return RealtimeSession(agent=self, deps=deps, config=config)

    def as_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        """Create a Tool that delegates execution to this agent.

        Unlike a handoff (control transfer), as_tool runs this agent as a
        sub-task and returns the result to the calling agent.

        Args:
            name: Tool name (default: derived from model name).
            description: Tool description.

        Returns:
            A Tool that, when called, runs this agent with the given input.
        """
        model_slug = self._model.split("/")[-1].replace("-", "_").replace(".", "_")
        tool_name = name or f"ask_{model_slug}"
        tool_desc = description or f"Delegate a task to the {model_slug} agent and get a response."

        agent_ref = self

        async def _delegate(query: str) -> str:
            from livelink.runner import Runner

            result = await Runner.run(agent_ref, input=query)
            if result.history:
                last_turn = result.history[-1]
                if hasattr(last_turn, "text") and last_turn.text:
                    return last_turn.text
            return f"Agent completed (stopped: {result.stopped_reason})"

        return Tool(
            name=tool_name,
            description=tool_desc,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The question or task to delegate.",
                    }
                },
                "required": ["query"],
            },
            fn=_delegate,
            takes_context=False,
            requires_approval=False,
        )

    def __repr__(self) -> str:
        return f"LiveAgent(model={self._model!r}, voice={self._voice!r}, tools={len(self._tools)})"

    def serve(
        self,
        *,
        host: str = "localhost",
        port: int = 8000,
        ui: bool = True,
        ui_path: str | Path | None = None,
        deps: Any = None,
        cors: bool | str | list[str] = False,
    ) -> None:
        """Start a WebSocket server for this agent with browser UI.

        Encapsulates all server boilerplate: WebSocket handling, session
        wiring, and static file serving. Uses Runner internally.

        Args:
            host: Bind address.
            port: Bind port.
            ui: Serve built-in audio client at /.
            ui_path: Serve custom HTML file instead of built-in UI.
            deps: Dependency injection passed to tools.
            cors: Enable CORS headers.
        """
        from livelink.serve import serve

        asyncio.run(serve(self, host=host, port=port, ui=ui, ui_path=ui_path, deps=deps, cors=cors))
