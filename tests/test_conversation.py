import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from aiglasses.assistant.app import AssistantApp, SessionEnd, load_persona, run_forever
from aiglasses.assistant.clauses import ClauseStream
from aiglasses.assistant.cleaner import clean_reply
from aiglasses.assistant.conversation import (
    AssistantTurnRecord,
    ConversationPipeline,
    History,
    system_prompt,
)
from aiglasses.assistant.echo import TurnLog
from aiglasses.assistant.fakes import FakeLlm, FakeSink, FakeStt, FakeTts
from aiglasses.assistant.glossary import Glossary, parse_glossary
from aiglasses.assistant.interfaces import Message, TtsOptions
from aiglasses.assistant.joiner import TurnJoiner
from aiglasses.assistant.llm_client import LangChainLlmClient
from aiglasses.assistant.router import LocalState, RoutedLlmClient
from aiglasses.domain.errors import ConfigError, GlassesError
from aiglasses.logging_setup import RedactingFilter
from aiglasses.services.voice import EndReason, VoiceUtterance
from aiglasses.settings import AssistantSettings, LlmRole
from tests.test_llm_client import SECRET, HttpError, StubModel

FIXED_NOW = datetime(2026, 10, 31, 16, 35, tzinfo=ZoneInfo("Asia/Kolkata"))


def settings(**values: object) -> AssistantSettings:
    return AssistantSettings(_env_file=None, llm_model="m", llm_api_key="k", **values)  # type: ignore[arg-type]


def utterance(
    started: float | None = None, seconds: float = 2.0, *, loud_tail: bool = False
) -> VoiceUtterance:
    now = time.time() if started is None else started + seconds
    pcm = np.full(int(16000 * seconds), 6000 if loud_tail else 0, dtype=np.int16)
    if not loud_tail:
        pcm[: len(pcm) // 2] = 6000  # speech that stops before the end
    return VoiceUtterance(
        pcm, 16000, now - seconds, now, int(seconds * 50), EndReason.END_MARKER, None, now
    )


class Rig:
    def __init__(
        self,
        *,
        llm: FakeLlm | None = None,
        stt: FakeStt | None = None,
        tts: FakeTts | None = None,
        tmp_path: Path | None = None,
        router: bool = True,
        glossary: Glossary | None = None,
        **values: object,
    ) -> None:
        self.settings = settings(**values)
        self.llm = llm or FakeLlm([("The capital of France is Paris. ", "It is lovely.")])
        self.stt = stt or FakeStt("what is the capital of france")
        self.tts, self.sink = tts or FakeTts(), FakeSink()
        client = (
            RoutedLlmClient(
                self.settings, self.llm,
                state=LocalState(timezone="Asia/Kolkata", clock=lambda _tz: FIXED_NOW),
            )
            if router
            else self.llm
        )  # fmt: skip
        self.records: list[AssistantTurnRecord] = []
        self.pipeline = ConversationPipeline(
            stt=self.stt, tts=self.tts, sink=self.sink, llm=client,
            options=TtsOptions(voice="v", speed=1.0), settings=self.settings, persona="PERSONA",
            glossary=glossary, on_record=self.records.append, clock=lambda _tz: FIXED_NOW,
            turn_log=TurnLog(tmp_path / "turns.jsonl") if tmp_path else None,
        )  # fmt: skip
        self.state = client.state if isinstance(client, RoutedLlmClient) else LocalState()
        self.app = AssistantApp(self.pipeline, self.settings)


async def test_a_normal_turn_speaks_the_cleaned_reply_and_remembers_it(tmp_path: Path) -> None:
    rig = Rig(
        llm=FakeLlm([("**Paris** is the capital, ", "at 4:30 it is 50% sure.")]), tmp_path=tmp_path
    )
    record = await rig.pipeline.run_turn(utterance(), 1)
    spoken = " ".join(rig.tts.spoken)
    assert "*" not in spoken and "four thirty" in spoken and "fifty percent" in spoken
    assert record.outcome == "spoken" and len(rig.sink.played) == len(rig.tts.spoken)
    last = rig.pipeline.history.last
    assert len(rig.pipeline.history) == 1 and last is not None
    assert last.user == "what is the capital of france"
    assert record.t_end <= record.t_received <= record.t_stt  # type: ignore[operator]
    assert record.t_stt <= record.t_llm_first <= record.t_first_chunk <= record.t_playing  # type: ignore[operator]
    row = json.loads((tmp_path / "turns.jsonl").read_text())
    for key in ("stt_s", "router_s", "llm_first_token_s", "first_audio_s", "end_to_sound_s"):
        assert row[key] is not None
    assert "capital" not in json.dumps(row) and "Paris" not in json.dumps(row)


async def test_the_llm_sees_persona_date_glossary_note_and_history() -> None:
    glossary = Glossary(parse_glossary(["Zorvex Quill"]))
    rig = Rig(glossary=glossary, llm=FakeLlm([("One. ",), ("Two. ",)]))
    await rig.pipeline.run_turn(utterance(), 1)
    await rig.pipeline.run_turn(utterance(), 2)
    system, *rest = rig.llm.requests[1]
    assert system.role == "system" and system.content.startswith("PERSONA")
    assert "Zorvex Quill" in system.content and "speech recognition" in system.content
    assert "Saturday 31 October 2026, 16:35" in system.content
    assert [m.role for m in rest] == ["user", "assistant", "user"]
    assert rest[1].content == "One."


async def test_the_first_clause_is_spoken_while_the_llm_is_still_writing() -> None:
    release = asyncio.Event()

    class Slow:
        name = "slow"

        async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
            yield "Sure thing, I can do that for you "
            yield "right now and then some more. "
            await release.wait()
            yield "Done."

    rig = Rig(router=False)
    rig.pipeline.llm = Slow()
    task = asyncio.create_task(rig.pipeline.run_turn(utterance(), 1))
    await asyncio.wait_for(rig.sink.playing.wait(), 5)
    assert rig.tts.spoken and not task.done()
    release.set()
    assert (await task).outcome == "spoken"


async def test_a_local_question_makes_no_llm_call() -> None:
    rig = Rig(stt=FakeStt("what time is it"))
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert rig.llm.requests == [] and record.tier == 0
    assert "four thirty five" in " ".join(rig.tts.spoken)
    assert rig.state.last_heard == "what time is it"


async def test_the_router_failure_path_uses_the_main_model() -> None:
    rig = Rig(router_mode="model", llm=FakeLlm([("Fine. ",)]))
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.route_role == "main" and "no classifier" in record.route_reason


async def test_an_llm_failure_before_any_speech_speaks_the_fallback_line() -> None:
    rig = Rig(llm=FakeLlm([("x",)], fail_after=0))
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.outcome == "llm_error"
    assert rig.tts.spoken == [rig.settings.fallback_llm_error]
    follow = await rig.pipeline.run_turn(utterance(), 2)  # the loop is still alive
    assert follow.outcome == "llm_error" and len(rig.records) == 2


async def test_an_llm_failure_mid_reply_stops_the_speaker_cleanly() -> None:
    rig = Rig(
        llm=FakeLlm(
            [("Here is the first part of it, ", "and then more words arrive. ", "x")], fail_after=2
        )
    )
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.outcome == "llm_error" and rig.sink.stops >= 1
    assert rig.settings.fallback_llm_error not in rig.tts.spoken


async def test_a_tts_failure_is_survived() -> None:
    rig = Rig(tts=FakeTts(fail_on="Paris"))
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.outcome == "tts_error" and record.error


async def test_empty_speech_gets_the_no_speech_line() -> None:
    rig = Rig(stt=FakeStt("   "))
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.outcome == "empty" and rig.tts.spoken == [rig.settings.fallback_no_speech]
    assert rig.llm.requests == [] and len(rig.pipeline.history) == 0


async def test_history_keeps_only_the_configured_number_of_exchanges() -> None:
    rig = Rig(memory_max_turns=2, llm=FakeLlm([(f"Reply {i}. ",) for i in range(4)]))
    for turn in range(1, 5):
        await rig.pipeline.run_turn(utterance(), turn)
    assert len(rig.llm.requests[3]) == 1 + 2 * 2 + 1
    assert History(0).messages() == []


async def test_the_reply_length_cap_stops_generation_between_clauses() -> None:
    long_reply = tuple(f"Sentence number {i} goes here. " for i in range(10))
    rig = Rig(tts=FakeTts(chunk_seconds=1.0), max_reply_seconds=1.5, llm=FakeLlm([long_reply]))
    await rig.pipeline.run_turn(utterance(), 1)
    assert 1 <= len(rig.tts.spoken) <= 3


async def test_a_new_request_interrupts_and_the_interrupted_reply_is_remembered() -> None:
    rig = Rig(
        llm=FakeLlm(
            [("Let me think about that for a long while. ", "Still going. "), ("Ok. ",)], delay=0.3
        )
    )
    await rig.app.handle(utterance())
    await asyncio.wait_for(rig.sink.playing.wait(), 5)
    await rig.app.handle(utterance())
    await rig.app.runner.wait()
    second_messages = rig.llm.requests[1]
    assistant = [m for m in second_messages if m.role == "assistant"]
    assert assistant and "interrupted" in assistant[0].content
    assert rig.sink.stops >= 1
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert others == []


async def test_two_clicks_are_joined_when_the_first_was_cut_off() -> None:
    rig = Rig(
        stt=FakeStt(["first half", "first half and the rest"]), llm=FakeLlm([("A. ",), ("B. ",)])
    )
    base = time.time()
    await rig.app.handle(utterance(base, 4.8, loud_tail=True))
    await rig.app.runner.wait()
    await rig.app.handle(utterance(base + 5.5, 2.0))
    await rig.app.runner.wait()
    assert rig.stt.calls[1][0] == int(16000 * 4.8) + int(16000 * 2.0)
    users = [m.content for m in rig.llm.requests[1] if m.role == "user"]
    assert users == ["first half and the rest"]  # the cut-off exchange was replaced, not kept
    assert len(rig.pipeline.history) == 1


def test_the_joiner_leaves_complete_requests_and_late_clicks_alone() -> None:
    joiner = TurnJoiner(4.0)
    base = 1000.0
    assert joiner.feed(utterance(base, 2.0)).replaces_previous is False
    assert joiner.feed(utterance(base + 3.0, 2.0)).replaces_previous is False  # first was complete
    cut = joiner.feed(utterance(base + 10.0, 4.8, loud_tail=True))
    assert cut.replaces_previous is False
    late = joiner.feed(utterance(base + 40.0, 2.0))
    assert late.replaces_previous is False  # outside the window
    again = joiner.feed(utterance(base + 50.0, 4.8, loud_tail=True))
    joined = joiner.feed(utterance(base + 55.0, 1.0))
    assert again.replaces_previous is False and joined.replaces_previous is True
    assert len(joined.utterance.pcm) == int(16000 * 4.8) + 16000


async def test_the_session_ends_at_the_turn_limit() -> None:
    rig = Rig(max_turns_per_session=2)

    async def clicks() -> AsyncIterator[VoiceUtterance]:
        for i in range(5):
            yield utterance(1000.0 + 100 * i)

    end = await rig.app.consume(clicks())
    assert end == SessionEnd("turn_limit") and rig.app.turns == 2


async def test_reconnect_backs_off_and_resumes() -> None:
    rig = Rig(max_turns_per_session=1)
    attempts = {"n": 0}
    messages: list[str] = []
    delays: list[float] = []

    @asynccontextmanager
    async def opener(on_start):  # type: ignore[no-untyped-def]
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise GlassesError("not advertising")
        yield one_click()

    async def one_click() -> AsyncIterator[VoiceUtterance]:
        yield utterance(1000.0)

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    end = await run_forever(rig.app, opener, report=messages.append, sleep=fake_sleep)
    assert end.reason == "turn_limit" and delays == [2.0, 5.0]
    assert any("GlassesError" in m for m in messages)


async def test_a_dropped_connection_is_retried_not_fatal() -> None:
    rig = Rig(max_turns_per_session=9)
    opened = {"n": 0}
    delays: list[float] = []

    @asynccontextmanager
    async def opener(on_start):  # type: ignore[no-untyped-def]
        opened["n"] += 1
        yield empty()

    async def empty() -> AsyncIterator[VoiceUtterance]:
        return
        yield

    async def stop_after_three(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) == 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_forever(rig.app, opener, report=lambda m: None, sleep=stop_after_three)
    assert opened["n"] == 3 and delays == [2.0, 5.0, 10.0]


# --- secrets ---------------------------------------------------------------------------------


async def test_a_provider_error_carrying_the_key_never_reaches_logs_or_records(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    role = LlmRole(
        role="main", provider="openai", model="m", api_key=SECRET, base_url=None,
        temperature=0.1, max_tokens=20, timeout_s=1.0, max_retries=0,
    )  # fmt: skip
    client = LangChainLlmClient(role, model=StubModel([HttpError(401, f"invalid key {SECRET}")]))
    rig = Rig(router=False, tmp_path=tmp_path)
    rig.pipeline.llm = client
    with caplog.at_level(logging.DEBUG, logger="aiglasses"):
        record = await rig.pipeline.run_turn(utterance(), 1)
    everything = caplog.text + (tmp_path / "turns.jsonl").read_text() + record.error
    everything += " ".join(rig.tts.spoken)
    assert record.outcome == "llm_error" and SECRET not in everything


def test_the_logging_filter_redacts_configured_secrets_and_key_shapes() -> None:
    guard = RedactingFilter((SECRET,))
    entry = logging.LogRecord(
        "aiglasses", logging.INFO, "f", 1, "key %s and sk-%s", (SECRET, "a1" * 12), None
    )
    guard.filter(entry)
    assert SECRET not in entry.getMessage() and "sk-a1" not in entry.getMessage()


def test_transcripts_are_off_by_default_and_audio_is_not_saved() -> None:
    defaults = settings()
    assert defaults.log_transcripts is False and defaults.debug_save_audio is False


# --- helpers ---------------------------------------------------------------------------------


def test_the_cleaner_strips_markup_links_emoji_and_reads_numbers() -> None:
    text = "## Title\n- **Bold** [docs](http://a.b) https://x.y/z \U0001f600 3.5 and 1,200 in 2026"
    out = clean_reply(text)
    assert not any(ch in out for ch in "*#[]()_`") and "http" not in out and "\U0001f600" not in out
    assert "three point five" in out and "one thousand two hundred" in out
    assert "twenty twenty six" in out and "Bold docs" in out
    assert clean_reply("It is 21°C & 9:05") == "It is twenty one degrees Celsius and nine oh five"
    assert clean_reply("```code```") == ""


def test_the_clause_stream_emits_early_and_loses_nothing() -> None:
    stream = ClauseStream(6)
    text = "Sure, I can help with that today. Here is what I found for you, and more."
    emitted: list[str] = []
    for piece in [text[i : i + 5] for i in range(0, len(text), 5)]:
        emitted += stream.feed(piece)
    assert emitted  # something was ready before the text ended
    emitted += stream.finish()
    assert " ".join(emitted) == text


def test_system_prompt_without_glossary_has_persona_and_time_only() -> None:
    prompt = system_prompt(" P ", None, FIXED_NOW)
    assert prompt.startswith("P\n\nCurrent date and time:") and "speech recognition" not in prompt


def test_persona_loading_uses_the_packaged_default_or_a_given_file(tmp_path: Path) -> None:
    assert "spoken aloud" in load_persona(settings())
    custom = tmp_path / "persona.md"
    custom.write_text("Be a pirate.", encoding="utf-8")
    assert load_persona(settings(persona_path=custom)) == "Be a pirate."
    with pytest.raises(ConfigError):
        load_persona(settings(persona_path=tmp_path / "missing.md"))
