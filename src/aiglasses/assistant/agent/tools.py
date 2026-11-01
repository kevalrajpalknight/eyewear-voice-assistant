"""Our tools as LangChain tools. The registry still enforces every rule (risk class, taint,
"you must have asked", caps, timeouts); this layer only adapts the call shape."""

import logging
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from aiglasses.assistant.agent.turn import current_turn
from aiglasses.assistant.tools.base import Risk, ToolRegistry
from aiglasses.settings import AssistantSettings

ESCALATE_TOOL = "think_harder"
log = logging.getLogger("aiglasses.agent")


class _Why(BaseModel):
    reason: str = ""


def to_langchain_tools(registry: ToolRegistry, settings: AssistantSettings) -> list[BaseTool]:
    """A StructuredTool per usable registry tool, plus `think_harder` (hand over to the cloud)."""
    tools: list[BaseTool] = []
    for name in registry.names():
        if registry.risk(name) in (None, Risk.BLOCKED) or name not in settings.enabled_tools:
            continue
        tools.append(_adapt(registry, settings, name))
    tools.append(_think_harder())
    return tools


def _adapt(registry: ToolRegistry, settings: AssistantSettings, name: str) -> BaseTool:
    tool = registry.get(name)

    async def run(**kwargs: Any) -> str:
        turn = current_turn.get()
        outcome = await registry.call(
            name, kwargs, turn.ctx, enabled=turn.enabled,
            max_calls=settings.tool_max_calls_per_turn, timeout_s=settings.tool_timeout_s,
        )  # fmt: skip
        turn.report.tools = list(turn.ctx.log)
        turn.report.notes.append(f"{name}: {outcome.summary}")
        if not outcome.ok:
            log.warning("tool %s failed (%s): %s", name, outcome.category, outcome.summary)
        if name == "forget_all_that" and outcome.ok:
            turn.report.skip_memory = True
        return outcome.summary if outcome.ok else f"Failed: {outcome.summary}"

    return StructuredTool.from_function(
        coroutine=run, name=name, description=tool.description, args_schema=tool.args_model
    )


def _think_harder() -> BaseTool:
    async def run(reason: str = "") -> str:
        current_turn.get().escalate(reason or "asked for a stronger model")
        return "A stronger assistant will answer next."

    return StructuredTool.from_function(
        coroutine=run,
        name=ESCALATE_TOOL,
        description=(
            "Hand the request to a stronger assistant: for research, comparisons, health or "
            "money advice, or anything you are not sure about. Never guess."
        ),
        args_schema=_Why,
    )
