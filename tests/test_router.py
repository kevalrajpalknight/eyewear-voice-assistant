import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aiglasses.assistant.fakes import FakeLlm
from aiglasses.assistant.interfaces import Message
from aiglasses.assistant.router import LocalState, RouteDecision, RoutedLlmClient, normalise
from aiglasses.assistant.spoken import number_words, ordinal_words, spoken_date, spoken_time
from aiglasses.settings import AssistantSettings


def settings(**values: object) -> AssistantSettings:
    return AssistantSettings(_env_file=None, llm_model="m", llm_api_key="k", **values)  # type: ignore[arg-type]


def clock(_tz: ZoneInfo) -> datetime:
    return datetime(2026, 10, 31, 16, 35, tzinfo=ZoneInfo("Asia/Kolkata"))


def build(
    mode: str = "rules",
    *,
    fast: bool = True,
    classifier: object | None = None,
    **values: object,
) -> tuple[RoutedLlmClient, FakeLlm, FakeLlm, list[RouteDecision]]:
    main, small = FakeLlm([("main reply",)]), FakeLlm([("fast reply",)])
    seen: list[RouteDecision] = []
    router = RoutedLlmClient(
        settings(router_mode=mode, **values), main, fast=small if fast else None,
        classifier=classifier,  # type: ignore[arg-type]
        state=LocalState(timezone="Asia/Kolkata", clock=clock), on_decision=seen.append,
    )  # fmt: skip
    return router, main, small, seen


async def ask(router: RoutedLlmClient, text: str) -> str:
    messages = [Message("system", "persona"), Message("user", text)]
    return "".join([d async for d in router.stream(messages, max_tokens=50)])


@pytest.mark.parametrize(
    ("text", "role", "why"),
    [
        ("Hello there", "fast", "short"),
        ("What is the capital of France", "fast", "short"),
        ("Search for the best pizza near me", "main", "hard keyword"),
        ("What is the weather like", "main", "hard keyword"),
        ("Tell me a long story about a dragon and a very brave knight", "main", "not short"),
        ("compare these two phones", "main", "hard keyword"),
        ("Explain in detail how engines work", "main", "hard keyword"),
        ("I have seen it", "fast", "short"),  # "see" must not match inside "seen"
    ],
)
async def test_rules_decision_table(text: str, role: str, why: str) -> None:
    router, _main, _fast, seen = build()
    await ask(router, text)
    assert seen[-1].role == role and why in seen[-1].reason


async def test_rules_send_everything_to_main_when_no_fast_model_is_configured() -> None:
    router, main, _fast, seen = build(fast=False)
    assert await ask(router, "Hello there") == "main reply"
    assert (
        seen[-1].role == "main" and "no fast model" in seen[-1].reason and len(main.requests) == 1
    )


async def test_router_off_always_uses_the_main_model() -> None:
    router, main, fast, _seen = build("off")
    await ask(router, "Hello")
    assert len(main.requests) == 1 and fast.requests == []


async def test_the_chosen_model_gets_the_whole_conversation_and_token_limit() -> None:
    router, _main, fast, _seen = build()
    await ask(router, "Hello")
    assert fast.requests[0][0].content == "persona" and fast.max_tokens == [50]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What time is it?", "It is four thirty five in the afternoon."),
        ("what's the time", "It is four thirty five in the afternoon."),
        (
            "What is the date today",
            "Today is Saturday, the thirty first of October, twenty twenty six.",
        ),
        ("Stop!", "Okay."),
        ("never mind", "Okay."),
    ],
)
async def test_tier_zero_answers_without_any_llm_call(text: str, expected: str) -> None:
    router, main, fast, seen = build()
    assert await ask(router, text) == expected
    assert seen[-1].tier == 0 and main.requests == [] and fast.requests == []


async def test_repeat_and_what_did_you_hear_read_local_state_only() -> None:
    router, main, _fast, _seen = build()
    assert "not said anything" in await ask(router, "repeat that")
    router.state.last_reply = "The answer is blue."
    router.state.last_heard = "what colour is the sky"
    assert await ask(router, "Repeat that please") == "The answer is blue."
    assert await ask(router, "what did you hear") == "I heard: what colour is the sky."
    assert main.requests == []


async def test_handlers_are_pluggable() -> None:
    main = FakeLlm()
    router = RoutedLlmClient(
        settings(), main, handlers=(lambda text, state: "pong" if text == "ping" else None,)
    )
    assert await ask(router, "ping") == "pong" and main.requests == []


class Classifier:
    name = "classifier"

    def __init__(self, answer: str = "EASY", *, delay: float = 0.0, fail: bool = False) -> None:
        self.answer, self.delay, self.fail = answer, delay, fail
        self.calls = 0

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("down")
        yield self.answer


@pytest.mark.parametrize(
    ("answer", "role"), [("EASY", "fast"), ("easy.", "fast"), ("HARD", "main"), ("HARD\n", "main")]
)
async def test_model_mode_follows_the_classifier(answer: str, role: str) -> None:
    router, _main, _fast, seen = build("model", classifier=Classifier(answer))
    await ask(router, "Anything at all")
    assert seen[-1].role == role and seen[-1].router_s >= 0.0


@pytest.mark.parametrize(
    ("classifier", "fallback", "role", "why"),
    [
        (Classifier(fail=True), "strong", "main", "failed"),
        (Classifier(fail=True), "fast", "fast", "fallback fast"),
        (Classifier("maybe"), "strong", "main", "unusable"),
        (Classifier(delay=1.0), "strong", "main", "timed out"),
        (Classifier(delay=1.0), "fast", "fast", "timed out"),
        (None, "strong", "main", "no classifier"),
    ],
)
async def test_model_mode_falls_back_on_failure_timeout_or_garbage(
    classifier: Classifier | None, fallback: str, role: str, why: str
) -> None:
    router, _main, _fast, seen = build(
        "model", classifier=classifier, router_fallback=fallback, router_timeout_s=0.05
    )
    assert await ask(router, "Anything at all") in ("main reply", "fast reply")
    assert seen[-1].role == role and why in seen[-1].reason


async def test_a_local_answer_never_calls_the_classifier() -> None:
    classifier = Classifier()
    router, *_ = build("model", classifier=classifier)
    await ask(router, "what time is it")
    assert classifier.calls == 0


def test_normalise_and_spoken_forms() -> None:
    assert normalise("  What's the TIME?! ") == "what's the time"
    assert (number_words(0), number_words(21), number_words(115)) == (
        "zero", "twenty one", "one hundred and fifteen",
    )  # fmt: skip
    assert ordinal_words(31) == "thirty first" and ordinal_words(12) == "twelfth"
    morning = datetime(2026, 1, 2, 7, 5)
    assert spoken_time(morning) == "seven oh five in the morning"
    assert spoken_time(datetime(2026, 1, 2, 21, 0)) == "nine o'clock in the evening"
    assert spoken_date(morning) == "Friday, the second of January, twenty twenty six"
