"""The brain, as one `LlmClient`: rules first, then an agent that works with the local model and
hands the hard parts to the cloud. It narrates while it works and speaks as soon as text streams."""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage

from aiglasses.assistant.agent.fastpath import fast_path
from aiglasses.assistant.agent.hints import Hints
from aiglasses.assistant.agent.routing import ModelRouter
from aiglasses.assistant.agent.tools import to_langchain_tools
from aiglasses.assistant.agent.turn import Turn, TurnReport, current_turn
from aiglasses.assistant.conversation.escalation import EscalationRules
from aiglasses.assistant.conversation.intents import Intents
from aiglasses.assistant.conversation.interfaces import TIME_MARKER, Message
from aiglasses.assistant.conversation.memory import Memory
from aiglasses.assistant.conversation.router import DEFAULT_HANDLERS, Handler, LocalState
from aiglasses.assistant.llm.llm_client import map_error, to_langchain
from aiglasses.assistant.speech.clauses import FLUSH
from aiglasses.assistant.tools.base import ToolContext, ToolRegistry
from aiglasses.settings import AssistantSettings

log = logging.getLogger("aiglasses.agent")
END = object()
STILL_WORKING_S = 6.0  # silence this long during a tool or model step: say it is still going
MAX_REASSURANCES = 2
LEAK_CHECK_CHARS = 28  # how much of a model message is read before it may be spoken
MARKERS = r"<\||\|>|tool_call|<tool|^\s*\[\w+\s*:"
AGENT_RULES = (
    "You can use tools. Before a tool call say ONE short sentence about what you are about to do. "
    "Look with the camera when the user asks about something in front of them, search the web "
    "for current facts or product details, and use what the tools return. Several tools in a row "
    "are fine for a bigger request. After a tool answers, use its result: it is what the camera "
    "or the web really returned, so say what it shows and never say you cannot see it. Never claim "
    "you did something you did not do with a tool. "
    "Reply in at most {target} short spoken sentences. Call think_harder for research, health or "
    "money advice, or when you are not sure."
)


class AgentClient:
    name = "agent"

    def __init__(
        self,
        settings: AssistantSettings,
        *,
        local_model: Any,
        cloud_model: Any,
        registry: ToolRegistry,
        intents: Intents,
        rules: EscalationRules,
        memory: Memory,
        state: LocalState | None = None,
        handlers: tuple[Handler, ...] = DEFAULT_HANDLERS,
        hints: Hints | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings, self.registry, self.intents, self.rules = settings, registry, intents, rules
        self.memory, self.handlers, self._clock = memory, handlers, clock
        self.hints = hints or Hints.load()
        self.state = state or LocalState(timezone=settings.assistant_timezone)
        self.report = TurnReport()
        self.last_finish_reason: str | None = None
        self._local = local_model
        self._tools = to_langchain_tools(registry, settings)
        names = "|".join(re.escape(t.name) for t in self._tools)
        self._leak = re.compile(rf"{MARKERS}|^\s*(?:{names})\b", re.IGNORECASE)
        middleware: list[Any] = [
            ModelRouter(local_model, cloud_model, settings.local_step_timeout_s),
            ModelCallLimitMiddleware(run_limit=settings.agent_max_steps, exit_behavior="end"),
        ]
        self._agent = create_agent(model=local_model, tools=self._tools, middleware=middleware)

    def _system(self, content: str) -> str:
        """The persona without its changing time line (so the server's prompt cache keeps hitting)
        plus the agent's rules."""
        stable = content.partition("\n\n" + TIME_MARKER)[0].strip()
        return f"{stable}\n\n{AGENT_RULES.format(target=self.settings.reply_target_sentences)}"

    def _wants_cloud(self, utterance: str) -> str:
        """A reason to skip the local model, or ''."""
        earlier = [e.user for e in self.memory.exchanges()]
        if self.rules.research(utterance):
            return "research"
        if self.rules.repeated(utterance, earlier):
            return "dissatisfied"
        if earlier and self.rules.dissatisfied(utterance):
            return "dissatisfied"
        return ""

    async def prime(self, system_content: str) -> None:
        """Process the stable prompt once so the first real turn reuses the server's cache."""
        try:
            bound = self._local.bind_tools(self._tools)
            await bound.ainvoke(
                [SystemMessage(self._system(system_content)), HumanMessage("Hello")], max_tokens=1
            )
        except Exception:  # priming is a courtesy
            return

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
        self.report = report = TurnReport()
        utterance = messages[-1].content
        queue: asyncio.Queue[Any] = asyncio.Queue()
        said: set[str] = set()

        async def announce(text: str) -> None:
            if text not in said:  # each narration line once per turn
                said.add(text)
                queue.put_nowait(text + " " + FLUSH)

        enabled = self.settings.enabled_tools
        turn = Turn(ToolContext(utterance=utterance, announce=announce), report, enabled)
        token = current_turn.set(turn)
        try:
            spoke = False
            async for text in fast_path(
                utterance, turn, registry=self.registry, intents=self.intents,
                handlers=self.handlers, state=self.state, settings=self.settings,
                drain=lambda: _drain(queue),
            ):  # fmt: skip
                spoke = True
                yield text
            if spoke:
                return
            if reason := self._wants_cloud(utterance):
                turn.escalate(reason)
            turn.plan = self.hints.plan(utterance, enabled)
            turn.ctx.requested.update(turn.plan)  # the user's own words asked for these
            if turn.plan:  # say what is about to happen now, not after the model has answered
                first = turn.plan[0]
                line = self.registry.fillers.pick(first)
                if line:
                    turn.ctx.narrated.add(first)
                    await announce(line)
            deadline = self._clock() + self.settings.agent_time_budget_s
            for _attempt_number in range(2):
                async for text in self._attempt(messages, queue, turn, deadline):
                    spoke = spoke or bool(text.strip())
                    yield text
                if not turn.leaked or spoke:
                    break
                turn.escalate("bad_tool_call")  # the local model wrote a tool call as speech
                turn.leaked = False
            report.tools = list(turn.ctx.log)
            report.action = "cloud" if turn.use_cloud else "answer"
            report.category = report.category or "answered"
            if not spoke:
                yield self.settings.fallback_llm_error
        finally:
            current_turn.reset(token)

    async def _attempt(
        self, messages: list[Message], queue: asyncio.Queue[Any], turn: Turn, deadline: float
    ) -> AsyncIterator[str]:
        """One run of the agent: its narration and text, in order, until it ends or time is up."""
        runner = asyncio.create_task(self._run(messages, queue, turn))
        reassured = 0
        try:
            while True:
                left = deadline - self._clock()
                if left <= 0:
                    turn.escalate("time_budget")
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), min(left, STILL_WORKING_S))
                except asyncio.TimeoutError:
                    if left <= STILL_WORKING_S or reassured >= MAX_REASSURANCES:
                        raise
                    reassured += 1  # a long silence: say that the work goes on
                    yield (self.registry.fillers.pick("waiting") or "Still working.") + " " + FLUSH
                    continue
                if item is END:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        except asyncio.TimeoutError:
            turn.escalate("time_budget")
        finally:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)

    async def _run(self, messages: list[Message], queue: asyncio.Queue[Any], turn: Turn) -> None:
        """Run the agent and put its spoken text on the queue as it streams."""
        convo = to_langchain([Message("system", self._system(messages[0].content)), *messages[1:]])
        started = self._clock()
        held: dict[str, str] = {}  # message id -> text not yet released
        dropped: set[str] = set()
        try:
            async for chunk, meta in self._agent.astream(
                {"messages": convo}, stream_mode="messages"
            ):
                node = meta.get("langgraph_node") if isinstance(meta, dict) else None
                if node != "model" or not isinstance(chunk, AIMessageChunk):
                    continue
                text = chunk.content if isinstance(chunk.content, str) else ""
                key = str(chunk.id)
                if not text or key in dropped:
                    continue
                if key in held or len(text) < LEAK_CHECK_CHARS:
                    held[key] = held.get(key, "") + text
                    if len(held[key]) < LEAK_CHECK_CHARS and not held[key].rstrip().endswith("."):
                        continue
                    text = held.pop(key)
                if self._leak.search(text[: LEAK_CHECK_CHARS * 2]):
                    turn.leaked = True
                    dropped.add(key)
                    continue
                first = self._clock() - started
                if turn.use_cloud and turn.report.cloud_first_token_s is None:
                    turn.report.cloud_first_token_s = first
                if not turn.use_cloud and turn.report.local_first_token_s is None:
                    turn.report.local_first_token_s = first
                queue.put_nowait(text)
            for text in held.values():  # short messages that ended before the check size
                if self._leak.search(text):
                    turn.leaked = True
                elif text.strip():
                    queue.put_nowait(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = map_error(exc)
            log.warning("agent failed: %s", error)
            queue.put_nowait(error)
            return
        queue.put_nowait(END)


def _drain(queue: asyncio.Queue[Any]) -> list[str]:
    """Narration lines waiting on the queue (the fast path speaks them itself)."""
    kept: list[Any] = []
    lines: list[str] = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, str):
            lines.append(item.strip())
        else:
            kept.append(item)
    for item in kept:
        queue.put_nowait(item)
    return lines
