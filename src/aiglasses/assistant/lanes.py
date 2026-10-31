"""Local-first lanes: rules, then ONE constrained local-model call per step, then the cloud.

Lane 0  deterministic: time/date/stop/repeat, and device or memory intents from intents.yaml.
Lane 1  the local model, forced JSON {action: answer|tool|escalate}, at most AGENT_MAX_STEPS steps
        within AGENT_TIME_BUDGET_S. It never speaks a guess: when unsure it must escalate.
Lane 2  the cloud model with the same history and native tool calling, after a short filler line.

The class is itself an `LlmClient`, so the conversation core does not know about lanes. Logs carry
the lane, action, reason category and timings, never the content.
"""

import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from aiglasses.assistant.escalation import EscalationRules
from aiglasses.assistant.intents import Intents, normalise
from aiglasses.assistant.interfaces import TIME_MARKER, LlmClient, Message, ToolCall
from aiglasses.assistant.local_llm import InvalidStep, LocalLlm, LocalUnavailable, Step
from aiglasses.assistant.memory import Memory, fit_tokens
from aiglasses.assistant.router import DEFAULT_HANDLERS, Handler, LocalState
from aiglasses.assistant.tools.base import Risk, ToolContext, ToolOutcome, ToolRegistry
from aiglasses.settings import AssistantSettings

CLOUD_NOTE = (
    "A smaller local assistant passed this request to you ({reason}). Answer it fully, but keep "
    "the spoken reply short."
)
LOCAL_RULES = (
    "Answer with ONE JSON object only. Its action is one of:\n"
    '- "answer": text holds a spoken reply of at most {target} short sentences.\n'
    '- "tool": tool and args name something to run; you will then see its result.\n'
    '- "escalate": hand the request to a stronger assistant. Use it for current or time-sensitive '
    "facts, research or detailed analysis, anything you are not sure about, and when the user "
    "seems unhappy with earlier answers. Never guess. reason is a short phrase.\n"
    "Tools you may use (JSON): {tools}"
)
REASON_CATEGORIES = (
    ("dissatisf", "dissatisfied"),
    ("unhappy", "dissatisfied"),
    ("not sure", "unsure"),
    ("unsure", "unsure"),
    ("uncertain", "unsure"),
    ("current", "current_facts"),
    ("latest", "current_facts"),
    ("news", "current_facts"),
    ("research", "research"),
    ("detail", "research"),
)


class Escalate(Exception):
    def __init__(self, category: str, reason: str = "") -> None:
        super().__init__(category)
        self.category, self.reason = category, reason or category


@dataclass
class TurnReport:
    lane: int = 0  # 0 rules, 1 local model, 2 cloud
    action: str = ""
    category: str = ""  # why it escalated, or what answered
    steps: int = 0
    local_first_token_s: float | None = None
    local_total_s: float | None = None
    cloud_first_token_s: float | None = None
    skip_memory: bool = False
    notes: list[str] = field(default_factory=list)


def categorize(reason: str) -> str:
    text = reason.lower()
    return next((cat for needle, cat in REASON_CATEGORIES if needle in text), "model")


class LocalFirstClient:
    name = "local_first"

    def __init__(
        self,
        settings: AssistantSettings,
        *,
        local: LocalLlm | None,
        cloud: LlmClient,
        registry: ToolRegistry,
        intents: Intents,
        rules: EscalationRules,
        memory: Memory,
        state: LocalState | None = None,
        handlers: tuple[Handler, ...] = DEFAULT_HANDLERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings, self.local, self.cloud = settings, local, cloud
        self.registry, self.intents, self.rules, self.memory = registry, intents, rules, memory
        self.state = state or LocalState(timezone=settings.assistant_timezone)
        self.handlers = handlers
        self._clock = clock
        self.report = TurnReport()

    # --- the entry point --------------------------------------------------------------------

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
        self.report = report = TurnReport()
        utterance = messages[-1].content
        pending: list[str] = []

        async def announce(text: str) -> None:
            pending.append(text)

        ctx = ToolContext(utterance=utterance, announce=announce)
        enabled = self.settings.enabled_tools

        def drain() -> list[str]:
            said, pending[:] = list(pending), []
            return said

        reason = ""
        try:
            async for text in self._rules_lane(utterance, ctx, report, enabled, drain):
                yield text
            if report.action:
                return
            async for text in self._local_lane(messages, utterance, ctx, report, enabled, drain):
                yield text
            return
        except Escalate as escalation:
            reason = escalation.reason
            report.category = escalation.category
        report.lane, report.action = 2, "cloud"
        yield self.settings.escalation_filler + " "
        async for text in self._cloud_lane(
            messages, ctx, reason, max_tokens, enabled, drain, report
        ):
            yield text

    # --- lane 0 -----------------------------------------------------------------------------

    async def _rules_lane(
        self,
        utterance: str,
        ctx: ToolContext,
        report: TurnReport,
        enabled: tuple[str, ...],
        drain: Callable[[], list[str]],
    ) -> AsyncIterator[str]:
        text = normalise(utterance)
        for handler in self.handlers:
            answer = handler(text, self.state)
            if answer is not None:
                report.lane, report.action, report.category = 0, "answer", "handler"
                yield answer
                return
        intent = self.intents.find(utterance)
        if intent is None or intent.tool not in enabled:
            return
        report.lane, report.action, report.category = 0, "tool", intent.tool
        outcome = await self._call(intent.tool, intent.args, ctx, enabled)
        for said in drain():
            yield said + " "
        report.steps = 1
        report.notes.append(f"{intent.tool}: {outcome.summary}")
        if intent.tool == "forget_all_that" and outcome.ok:
            report.skip_memory = True
        yield outcome.spoken or ("Done." if outcome.ok else "Sorry, I could not do that.")

    # --- lane 1 -----------------------------------------------------------------------------

    async def _call(
        self, name: str, args: dict[str, Any], ctx: ToolContext, enabled: tuple[str, ...]
    ) -> ToolOutcome:
        return await self.registry.call(
            name, args, ctx, enabled=enabled,
            max_calls=self.settings.tool_max_calls_per_turn, timeout_s=self.settings.tool_timeout_s,
        )  # fmt: skip

    def _precheck(self, utterance: str) -> None:
        """Deterministic reasons to skip the local model altogether."""
        if self.rules.research(utterance):
            raise Escalate("research")
        earlier = [e.user for e in self.memory.exchanges()]
        if self.rules.repeated(utterance, earlier):
            raise Escalate("dissatisfied", "the same question again")
        if earlier and self.rules.dissatisfied(utterance):
            raise Escalate("dissatisfied", "the user corrects the last answer")

    def _local_system(self, prefix: str, enabled: tuple[str, ...]) -> str:
        tools = json.dumps(self.registry.schema(enabled), separators=(",", ":"))
        rules = LOCAL_RULES.format(target=self.settings.reply_target_sentences, tools=tools)
        return f"{prefix.strip()}\n\n{rules}"

    async def _local_lane(
        self,
        messages: list[Message],
        utterance: str,
        ctx: ToolContext,
        report: TurnReport,
        enabled: tuple[str, ...],
        drain: Callable[[], list[str]],
    ) -> AsyncIterator[str]:
        self._precheck(utterance)
        time_sensitive = self.rules.time_sensitive(utterance)
        if time_sensitive and "search_web" not in enabled:
            raise Escalate("current_facts")
        if self.local is None or not await self.local.available():
            raise Escalate("local_down")
        prefix, _, time_line = messages[0].content.partition("\n\n" + TIME_MARKER)
        convo = fit_tokens(
            [
                Message("system", self._local_system(prefix, enabled)),
                *messages[1:-1],
                Message(
                    "user", f"{utterance}\n({TIME_MARKER}{time_line})" if time_line else utterance
                ),
            ],
            self.settings.memory_max_tokens,
        )
        report.lane = 1
        deadline = self._clock() + self.settings.agent_time_budget_s
        searched = False
        if time_sensitive:
            outcome = await self._call("search_web", {"query": utterance}, ctx, enabled)
            for said in drain():
                yield said + " "
            report.steps += 1
            report.notes.append(f"search_web: {outcome.category}")
            if not outcome.ok:
                report.action, report.category = "answer", "search_failed"
                yield outcome.spoken or "I can't search right now."
                return
            searched = True
            convo.insert(-1, Message("assistant", '{"action":"tool","tool":"search_web"}'))
            convo.insert(-1, Message("user", f"Tool result: {outcome.summary}"))
        tool_names = [t for t in enabled if self.registry.risk(t) not in (None, Risk.BLOCKED)]
        malformed = 0
        while report.steps < self.settings.agent_max_steps:
            left = deadline - self._clock()
            if left <= 0:
                raise Escalate("time_budget")
            step = await self._step(convo, tool_names, left, report)
            if step is None:
                malformed += 1
                if malformed >= 2:
                    raise Escalate("invalid_json")
                continue
            report.steps += 1
            report.action = step.action
            if step.action == "escalate":
                raise Escalate(categorize(step.reason), step.reason)
            if step.action == "answer":
                text = step.text.strip()
                if not text:
                    raise Escalate("empty_answer")
                if time_sensitive and not searched:
                    raise Escalate("current_facts", "answered a time-sensitive question unaided")
                report.category = "answered"
                yield text
                return
            if not step.tool:
                malformed += 1
                if malformed >= 2:
                    raise Escalate("invalid_json")
                continue
            outcome = await self._call(step.tool, step.args, ctx, enabled)
            for said in drain():
                yield said + " "
            report.notes.append(f"{step.tool}: {outcome.summary}")
            risk = self.registry.risk(step.tool)
            if not outcome.ok or (
                outcome.spoken and risk in (Risk.READ, Risk.DEVICE_SAFE, Risk.DEVICE_STATE)
            ):
                if step.tool == "forget_all_that" and outcome.ok:
                    report.skip_memory = True
                report.category = "tool_done" if outcome.ok else "tool_failed"
                yield outcome.spoken or ("Done." if outcome.ok else "Sorry, I could not do that.")
                return
            searched = searched or step.tool == "search_web"
            convo.append(Message("assistant", step.model_dump_json(exclude_none=True)))
            convo.append(Message("user", f"Tool result: {outcome.summary}"))
        raise Escalate("step_cap")

    async def _step(
        self, convo: list[Message], tools: list[str], left: float, report: TurnReport
    ) -> Step | None:
        """One constrained call; a malformed reply is retried once by the caller."""
        assert self.local is not None
        try:
            step, timing = await self.local.step(
                convo, tools, max_tokens=self.settings.local_llm_max_tokens, time_left_s=left
            )
        except InvalidStep:
            return None
        except LocalUnavailable as exc:
            raise Escalate("local_slow" if "first token" in str(exc) else "local_down") from exc
        if report.local_first_token_s is None:
            report.local_first_token_s = timing.first_token_s
        report.local_total_s = (report.local_total_s or 0.0) + timing.total_s
        return step

    # --- lane 2 -----------------------------------------------------------------------------

    async def _cloud_lane(
        self,
        messages: list[Message],
        ctx: ToolContext,
        reason: str,
        max_tokens: int,
        enabled: tuple[str, ...],
        drain: Callable[[], list[str]],
        report: TurnReport,
    ) -> AsyncIterator[str]:
        note = CLOUD_NOTE.format(reason=reason)
        convo = [Message("system", f"{messages[0].content}\n\n{note}"), *messages[1:]]
        tools = self.registry.schema(
            tuple(t for t in enabled if self.registry.risk(t) not in (None, Risk.BLOCKED))
        )
        native: Any = getattr(self.cloud, "stream_with_tools", None) if tools else None
        started = self._clock()
        for _ in range(self.settings.tool_max_calls_per_turn + 1):
            calls: list[ToolCall] = []
            if native is not None:
                async for event in native(convo, tools, max_tokens=max_tokens):
                    if isinstance(event, ToolCall):
                        calls.append(event)
                    else:
                        if report.cloud_first_token_s is None:
                            report.cloud_first_token_s = self._clock() - started
                        yield event
            else:
                async for delta in self.cloud.stream(convo, max_tokens=max_tokens):
                    if report.cloud_first_token_s is None:
                        report.cloud_first_token_s = self._clock() - started
                    yield delta
            if not calls:
                return
            convo.append(Message("assistant", "", tool_calls=tuple(calls)))
            for call in calls:
                outcome = await self._call(call.name, call.args, ctx, enabled)
                for said in drain():
                    yield said + " "
                report.steps += 1
                report.notes.append(f"{call.name}: {outcome.summary}")
                convo.append(Message("tool", outcome.summary, tool_call_id=call.id))
