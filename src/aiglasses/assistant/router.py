"""Pick, per turn, how a request is answered: locally (tier 0), by the fast model (tier 1) or the
main model (tier 2). The router is itself an `LlmClient`, so the conversation core is unaware of it.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from aiglasses.assistant.interfaces import LlmClient, Message
from aiglasses.assistant.spoken import spoken_date, spoken_time
from aiglasses.settings import AssistantSettings

CLASSIFIER_PROMPT = (
    "You sort requests for a voice assistant. Answer with exactly one word: EASY if a small, "
    "fast model can answer it (greetings, simple facts, short chat, small tasks), or HARD if it "
    "needs a strong model (current events, searching, comparing, long explanations, reasoning)."
)
CLASSIFIER_TOKENS = 4
STOP_WORDS = {"stop", "cancel", "never mind", "nevermind", "be quiet", "stop talking", "quiet"}


@dataclass
class LocalState:
    """What tier-0 handlers may read. They never call out."""

    last_heard: str = ""
    last_reply: str = ""
    timezone: str = "UTC"
    clock: Callable[[ZoneInfo], datetime] = field(default=lambda tz: datetime.now(tz))

    def now(self) -> datetime:
        return self.clock(ZoneInfo(self.timezone))


Handler = Callable[[str, LocalState], str | None]


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip()


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text) for p in patterns)


def handle_time(text: str, state: LocalState) -> str | None:
    patterns = (r"\bwhat(?:'s| is)? the time\b", r"\bwhat time is it\b", r"\bthe time now\b")
    return f"It is {spoken_time(state.now())}." if _matches(text, patterns) else None


def handle_date(text: str, state: LocalState) -> str | None:
    patterns = (r"\bwhat(?:'s| is)? the date\b", r"\bwhat day is it\b", r"\bwhat(?:'s| is) today\b")
    return f"Today is {spoken_date(state.now())}." if _matches(text, patterns) else None


def handle_stop(text: str, state: LocalState) -> str | None:
    return "Okay." if text in STOP_WORDS else None


def handle_repeat(text: str, state: LocalState) -> str | None:
    if _matches(text, (r"\brepeat that\b", r"\bsay that again\b", r"\bwhat did you say\b")):
        return state.last_reply or "I have not said anything yet."
    return None


def handle_heard(text: str, state: LocalState) -> str | None:
    if _matches(text, (r"\bwhat did you hear\b", r"\bwhat did i say\b")):
        if state.last_heard:
            return f"I heard: {state.last_heard}."
        return "I have not heard anything yet."
    return None


DEFAULT_HANDLERS: tuple[Handler, ...] = (
    handle_stop, handle_repeat, handle_heard, handle_time, handle_date,
)  # fmt: skip


@dataclass(frozen=True)
class RouteDecision:
    tier: int  # 0 local, 1 fast model, 2 main model
    role: str  # "local" | "fast" | "main"
    reason: str
    router_s: float = 0.0


class RoutedLlmClient:
    name = "router"

    def __init__(
        self,
        settings: AssistantSettings,
        main: LlmClient,
        *,
        fast: LlmClient | None = None,
        classifier: LlmClient | None = None,
        state: LocalState | None = None,
        handlers: tuple[Handler, ...] = DEFAULT_HANDLERS,
        on_decision: Callable[[RouteDecision], None] | None = None,
    ) -> None:
        self.settings = settings
        self.main, self.fast, self.classifier = main, fast, classifier
        self.state = state or LocalState(timezone=settings.assistant_timezone)
        self.handlers = handlers
        self.on_decision = on_decision
        self.last_decision: RouteDecision | None = None

    def hard_keyword(self, text: str) -> str | None:
        for keyword in self.settings.hard_keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return keyword
        return None

    def local_answer(self, text: str) -> str | None:
        for handler in self.handlers:
            answer = handler(text, self.state)
            if answer is not None:
                return answer
        return None

    def _fallback(self, why: str) -> RouteDecision:
        if self.settings.router_fallback == "fast" and self.fast is not None:
            return RouteDecision(1, "fast", f"{why}; fallback fast")
        return RouteDecision(2, "main", f"{why}; fallback strong")

    async def _classify(self, classifier: LlmClient, text: str) -> RouteDecision:
        messages = [Message("system", CLASSIFIER_PROMPT), Message("user", text)]

        async def ask() -> str:
            reply = ""
            async for delta in classifier.stream(messages, max_tokens=CLASSIFIER_TOKENS):
                reply += delta
            return reply

        try:
            answer = await asyncio.wait_for(ask(), self.settings.router_timeout_s)
        except asyncio.TimeoutError:
            return self._fallback("classifier timed out")
        except Exception as exc:
            return self._fallback(f"classifier failed ({type(exc).__name__})")
        word = answer.strip().upper().strip(".")
        if word == "EASY" and self.fast is not None:
            return RouteDecision(1, "fast", "classifier: easy")
        if word in ("EASY", "HARD"):
            return RouteDecision(2, "main", f"classifier: {word.lower()}")
        return self._fallback("classifier gave an unusable answer")

    def _by_rules(self, local: str) -> RouteDecision:
        keyword = self.hard_keyword(local)
        words = len(local.split())
        if keyword is not None:
            return RouteDecision(2, "main", f"hard keyword {keyword!r}")
        if words > self.settings.router_max_easy_words:
            return RouteDecision(2, "main", f"{words} words is not short")
        return RouteDecision(1, "fast", f"short ({words} words), no hard keyword")

    async def decide(self, text: str) -> RouteDecision:
        started = time.perf_counter()
        local = normalise(text)
        mode = self.settings.router_mode
        if self.local_answer(local) is not None:
            decision = RouteDecision(0, "local", "local handler")
        elif mode == "off":
            decision = RouteDecision(2, "main", "router off")
        elif mode == "model":
            if self.classifier is None:
                decision = self._fallback("no classifier model")
            else:
                decision = await self._classify(self.classifier, text)
        elif self.fast is None:
            decision = RouteDecision(2, "main", "no fast model configured")
        else:
            decision = self._by_rules(local)
        decision = RouteDecision(
            decision.tier, decision.role, decision.reason, time.perf_counter() - started
        )
        self.last_decision = decision
        if self.on_decision is not None:
            self.on_decision(decision)
        return decision

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
        text = next((m.content for m in reversed(messages) if m.role == "user"), "")
        decision = await self.decide(text)
        if decision.tier == 0:
            yield self.local_answer(normalise(text)) or ""
            return
        client = self.fast if decision.role == "fast" and self.fast is not None else self.main
        async for delta in client.stream(messages, max_tokens=max_tokens):
            yield delta
