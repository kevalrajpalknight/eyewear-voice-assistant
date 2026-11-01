"""Answers that need no model: built-in handlers (time, date, stop...) and the intent rules."""

from collections.abc import AsyncIterator, Callable

from aiglasses.assistant.agent.turn import Turn
from aiglasses.assistant.intents import Intents, normalise
from aiglasses.assistant.router import Handler, LocalState
from aiglasses.assistant.tools.base import ToolRegistry
from aiglasses.settings import AssistantSettings


async def fast_path(
    utterance: str,
    turn: Turn,
    *,
    registry: ToolRegistry,
    intents: Intents,
    handlers: tuple[Handler, ...],
    state: LocalState,
    settings: AssistantSettings,
    drain: Callable[[], list[str]],
) -> AsyncIterator[str]:
    """Yield the spoken answer when a rule covers the request; yield nothing when none does."""
    text = normalise(utterance)
    report = turn.report
    for handler in handlers:
        answer = handler(text, state)
        if answer is not None:
            report.lane, report.action, report.category = 0, "answer", "handler"
            yield answer
            return
    intent = intents.find(utterance)
    if intent is None or intent.tool not in turn.enabled or intent.tool == "search_web":
        return
    report.lane, report.action, report.category = 0, "tool", intent.tool
    outcome = await registry.call(
        intent.tool, intent.args_for(text), turn.ctx, enabled=turn.enabled,
        max_calls=settings.tool_max_calls_per_turn, timeout_s=settings.tool_timeout_s,
    )  # fmt: skip
    for said in drain():
        yield said + " "
    report.steps = 1
    report.tools = list(turn.ctx.log)
    report.notes.append(f"{intent.tool}: {outcome.summary}")
    if intent.tool == "forget_all_that" and outcome.ok:
        report.skip_memory = True
    yield outcome.spoken or ("Done." if outcome.ok else "Sorry, I could not do that.")
