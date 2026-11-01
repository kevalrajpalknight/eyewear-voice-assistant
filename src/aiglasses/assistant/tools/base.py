"""Tool framework: what the model may call, under which rules.

The rules are enforced here, in code, not in the prompt:
- only registered and enabled tools run; unknown names and bad arguments are refused;
- a tool maps to ONE existing guarded facade call, never to raw bytes or the write guard;
- a state-changing tool runs only when the user's own words in this turn asked for it;
- once web or vision text has entered the turn (taint), every device and state-changing tool is
  refused for the rest of that turn;
- blocked tools never run; calls per turn and run time are capped; cancellation propagates.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from aiglasses.assistant.conversation.fillers import Fillers
from aiglasses.assistant.conversation.intents import Intents


class Risk(str, Enum):
    READ = "read"  # reads local state or the device; changes nothing
    DEVICE_SAFE = "device_safe"  # harmless local or device action (e.g. forget memory)
    DEVICE_STATE = "device_state"  # changes the device or its media (e.g. take a photo)
    NETWORK = "network"  # fetches outside content; its output is untrusted
    BLOCKED = "blocked"  # never runs


# Refused for the rest of a turn once untrusted text has entered it.
TAINT_BLOCKS = frozenset({Risk.DEVICE_SAFE, Risk.DEVICE_STATE, Risk.BLOCKED})
# Needs the user's own request in this turn.
NEEDS_REQUEST = frozenset({Risk.DEVICE_STATE})


class NoArgs(BaseModel):
    pass


@dataclass
class ToolResult:
    summary: str  # compact, one line, goes back to the model and into memory
    spoken: str | None = None  # a line to say directly, if the tool wants to
    tainted: bool = False  # content came from outside (web, camera)


@dataclass
class ToolContext:
    """Per-turn state shared by all tool calls of one turn."""

    utterance: str
    announce: Callable[[str], Awaitable[None]] = field(default=lambda text: _noop())
    tainted: bool = False
    calls: int = 0
    requested: set[str] = field(default_factory=set)  # tools the user's own words called for
    narrated: set[str] = field(default_factory=set)  # tools whose start was already announced
    log: list[tuple[str, str]] = field(default_factory=list)  # (tool, outcome category)


async def _noop() -> None:
    return None


class Tool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def risk(self) -> Risk: ...

    @property
    def args_model(self) -> type[BaseModel]: ...

    @property
    def announcement(self) -> str | None: ...

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...


@dataclass
class ToolOutcome:
    ok: bool
    summary: str  # what goes back to the model
    category: str  # ok unknown disabled blocked tainted not_requested cap args timeout error
    spoken: str | None = None


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None, *, intents: Intents | None = None) -> None:
        self.intents = intents or Intents([])
        self.fillers = Fillers.load()
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def risk(self, name: str) -> Risk | None:
        tool = self._tools.get(name)
        return tool.risk if tool is not None else None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schema(self, enabled: tuple[str, ...]) -> list[dict[str, Any]]:
        """Minimal schemas of the enabled, non-blocked tools, for the prompt."""
        found = []
        for name in enabled:
            tool = self._tools.get(name)
            if tool is None or tool.risk is Risk.BLOCKED:
                continue
            properties = tool.args_model.model_json_schema().get("properties", {})
            found.append(
                {
                    "tool": tool.name,
                    "does": tool.description,
                    "args": {k: v.get("type", "string") for k, v in properties.items()},
                }
            )
        return found

    async def call(
        self,
        name: str,
        raw_args: dict[str, Any] | None,
        ctx: ToolContext,
        *,
        enabled: tuple[str, ...],
        max_calls: int,
        timeout_s: float,
    ) -> ToolOutcome:
        outcome = await self._call(name, raw_args or {}, ctx, enabled, max_calls, timeout_s)
        ctx.log.append((name, outcome.category))
        return outcome

    async def _call(
        self,
        name: str,
        raw_args: dict[str, Any],
        ctx: ToolContext,
        enabled: tuple[str, ...],
        max_calls: int,
        timeout_s: float,
    ) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(False, f"unknown tool {name}", "unknown")
        if tool.risk is Risk.BLOCKED:
            return ToolOutcome(False, f"{name} is blocked", "blocked", "I can't do that.")
        if name not in enabled:
            return ToolOutcome(False, f"{name} is not enabled", "disabled", "That is switched off.")
        if ctx.tainted and tool.risk in TAINT_BLOCKS:
            return ToolOutcome(
                False, f"{name} refused: untrusted content is in this turn", "tainted",
                "I won't do that right now.",
            )  # fmt: skip
        asked = name in ctx.requested or self.intents.requests(tool.name, ctx.utterance)
        if tool.risk in NEEDS_REQUEST and not asked:
            return ToolOutcome(
                False, f"{name} refused: you did not ask for it", "not_requested",
                "Tell me directly if you want that.",
            )  # fmt: skip
        if ctx.calls >= max_calls:
            return ToolOutcome(False, "too many tool calls this turn", "cap")
        try:
            args = tool.args_model.model_validate(raw_args)
        except ValidationError:
            return ToolOutcome(False, f"bad arguments for {name}", "args")
        ctx.calls += 1
        if tool.announcement and name not in ctx.narrated:
            await ctx.announce(self.fillers.pick(tool.name) or tool.announcement)
        try:
            result = await asyncio.wait_for(tool.run(args, ctx), timeout_s)
        except asyncio.TimeoutError:
            return ToolOutcome(False, f"{name} timed out", "timeout", "That took too long.")
        except asyncio.CancelledError:
            raise
        except ToolRefused as exc:
            return ToolOutcome(False, str(exc), "error", exc.spoken)
        except Exception as exc:
            return ToolOutcome(
                False, f"{name} failed: {type(exc).__name__}", "error", "Something went wrong."
            )
        if result.tainted:
            ctx.tainted = True
        return ToolOutcome(True, result.summary, "ok", result.spoken)


class ToolRefused(Exception):
    """A tool declines to run (not connected, not supported, rate limit). Message is safe to log."""

    def __init__(self, message: str, spoken: str = "I can't do that right now.") -> None:
        super().__init__(message)
        self.spoken = spoken
