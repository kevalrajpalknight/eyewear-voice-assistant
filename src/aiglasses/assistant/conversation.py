"""The conversation core: utterance -> STT -> (glossary) -> router/LLM stream -> cleaner -> clauses
-> TTS -> speaker, speaking the first clause while the LLM is still writing.

Privacy: text and audio are kept only when LOG_TRANSCRIPTS / DEBUG_SAVE_AUDIO say so. The turn log
holds lengths and timings. Failures are spoken as short local lines and never end the loop.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from aiglasses.assistant.clauses import ClauseStream
from aiglasses.assistant.cleaner import clean_reply
from aiglasses.assistant.echo import Debug, TurnLog
from aiglasses.assistant.glossary import Correction, Glossary
from aiglasses.assistant.interfaces import (
    TIME_MARKER,
    AudioChunk,
    AudioSink,
    LlmClient,
    Message,
    SpeechToText,
    TextToSpeech,
    TtsOptions,
)
from aiglasses.assistant.lanes import TurnReport
from aiglasses.assistant.llm_client import LlmError
from aiglasses.assistant.memory import Exchange, Memory
from aiglasses.assistant.router import RouteDecision, RoutedLlmClient
from aiglasses.assistant.spoken import number_words
from aiglasses.assistant.wake import WakePhrases
from aiglasses.services.voice import VoiceUtterance
from aiglasses.settings import AssistantSettings
from aiglasses.timefmt import stamps

log = logging.getLogger("aiglasses.assistant")
QUEUE_DEPTH = 3
LENGTH_REASONS = ("length", "max_tokens", "MAX_TOKENS")  # the provider ran out of tokens


def silence(seconds: float, rate: int = 24000) -> AudioChunk:
    return AudioChunk(np.zeros(int(rate * seconds), dtype=np.int16), rate)


class StageError(Exception):
    """A failure tagged with where it happened: the language model or the speech engine."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(f"{stage}: {type(cause).__name__}")
        self.stage, self.cause = stage, cause


def system_prompt(
    persona: str,
    glossary: Glossary | None,
    now: datetime,
    target_sentences: int = 2,
    names: tuple[str, str] = ("", ""),
) -> str:
    """Persona + recognition-error note (from the glossary) + the current date and time."""
    text = persona.strip().replace("{target_sentences}", number_words(target_sentences))
    text = text.replace("{assistant_name_long}", names[1]).replace("{assistant_name}", names[0])
    parts = [text]
    if glossary:
        parts.append(glossary.prompt_note())
    parts.append(f"{TIME_MARKER} {now:%A %d %B %Y, %H:%M} ({now.tzinfo}).")
    return "\n\n".join(parts)


@dataclass
class AssistantTurnRecord:
    turn: int
    end_reason: str
    audio_seconds: float
    t_end: float
    t_received: float
    t_stt: float | None = None
    t_router: float | None = None
    t_llm_first: float | None = None
    t_first_chunk: float | None = None
    t_playing: float | None = None
    t_done: float | None = None
    words_in: int = 0
    chars_out: int = 0
    clauses: int = 0
    tier: int | None = None
    route_role: str = ""
    route_reason: str = ""
    router_s: float = 0.0
    action: str = ""
    steps: int = 0
    stt: str = ""
    tts: str = ""
    brain: str = ""  # who answered: rules, local <model>, cloud <provider:model>
    tools: str = ""  # tool names and outcomes, e.g. "read_battery:ok"
    local_first_token_s: float | None = None
    cloud_first_token_s: float | None = None
    corrections: int = 0
    outcome: str = "spoken"  # spoken | empty | ignored | wake_ack | cancelled | llm_error ...
    woke: bool = False  # the request started with a wake phrase
    truncation: str = ""  # why a reply ended early (provider_length, sentence_soft_cap, ...)
    error: str = ""
    heard: str | None = None  # only with LOG_TRANSCRIPTS
    reply: str | None = None  # only with LOG_TRANSCRIPTS

    @staticmethod
    def _gap(later: float | None, earlier: float | None) -> float | None:
        return None if later is None or earlier is None else round(later - earlier, 3)

    def to_json(self) -> dict[str, object]:
        row: dict[str, object] = {
            **stamps(datetime.fromtimestamp(self.t_received, timezone.utc)),
            "turn": self.turn, "outcome": self.outcome, "end_reason": self.end_reason,
            "audio_s": round(self.audio_seconds, 2), "words_in": self.words_in,
            "chars_out": self.chars_out, "clauses": self.clauses, "corrections": self.corrections,
            "tier": self.tier, "route_role": self.route_role, "route_reason": self.route_reason,
            "router_s": round(self.router_s, 3), "action": self.action, "steps": self.steps,
            "stt": self.stt, "tts": self.tts,
            "brain": self.brain, "tools": self.tools, "woke": self.woke,
            "truncation": self.truncation,
            "local_first_token_s": self.local_first_token_s,
            "cloud_first_token_s": self.cloud_first_token_s,
            "decode_s": self._gap(self.t_received, self.t_end),
            "stt_s": self._gap(self.t_stt, self.t_received),
            "llm_first_token_s": self._gap(self.t_llm_first, self.t_stt),
            "first_audio_s": self._gap(self.t_first_chunk, self.t_llm_first or self.t_stt),
            "playback_start_s": self._gap(self.t_playing, self.t_first_chunk),
            "end_to_sound_s": self._gap(self.t_playing, self.t_end),
            "spoken_s": self._gap(self.t_done, self.t_playing),
        }  # fmt: skip
        if self.error:
            row["error"] = self.error
        if self.heard is not None:
            row["heard"] = self.heard
        if self.reply is not None:
            row["reply"] = self.reply
        return row


class ConversationPipeline:
    """One turn per `run_turn` call. `TurnRunner` serialises turns and handles barge-in."""

    def __init__(
        self,
        *,
        stt: SpeechToText,
        tts: TextToSpeech,
        sink: AudioSink,
        llm: LlmClient,
        options: TtsOptions,
        settings: AssistantSettings,
        persona: str,
        glossary: Glossary | None = None,
        turn_log: TurnLog | None = None,
        debug: Debug | None = None,
        on_record: Callable[[AssistantTurnRecord], None] | None = None,
        on_correction: Callable[[Correction], None] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_heard: Callable[[str], None] | None = None,
        on_said: Callable[[str], None] | None = None,
        wake: WakePhrases | None = None,
        clock: Callable[[ZoneInfo], datetime] | None = None,
        memory_clock: Callable[[], float] = time.monotonic,
        memory: Memory | None = None,
    ) -> None:
        self.stt, self.tts, self.sink, self.llm = stt, tts, sink, llm
        self.options, self.settings, self.persona, self.glossary = (
            options,
            settings,
            persona,
            glossary,
        )
        self.history = memory or Memory(
            settings.memory_max_turns,
            settings.memory_ttl_s,
            settings.memory_max_tokens,
            memory_clock,
        )
        self._turn_log, self._debug = turn_log, debug
        self._on_record, self._on_correction = on_record, on_correction
        self.on_text = on_text
        self.on_heard, self.on_said = on_heard, on_said
        self.wake = wake
        self._clock = clock or (lambda tz: datetime.now(tz))
        self._current: AssistantTurnRecord | None = None
        if isinstance(llm, RoutedLlmClient):
            llm.on_decision = self._note_decision

    def _note_decision(self, decision: RouteDecision) -> None:
        record = self._current
        if record is not None:
            record.t_router = time.time()
            record.tier, record.route_role = decision.tier, decision.role
            if decision.role != "local":
                role = self.settings.llm_role("fast" if decision.role == "fast" else "main")
                record.brain = f"cloud {role.provider}:{role.model}"
            record.route_reason, record.router_s = decision.reason, decision.router_s

    def _brain(self, lane: int) -> str:
        if lane == 0:
            return "rules (no model)"
        if lane == 1:
            return f"local model {self.settings.local_llm_model}"
        role = self.settings.llm_role("main")
        return f"cloud {role.provider}:{role.model}"

    def _absorb_report(self, record: AssistantTurnRecord) -> TurnReport | None:
        """Lane details from a local-first client; other clients have none."""
        report = getattr(self.llm, "report", None)
        if not isinstance(report, TurnReport):
            return None
        record.tier, record.route_reason, record.action = (
            report.lane,
            report.category,
            report.action,
        )
        record.route_role = ("rules", "local", "cloud")[report.lane]
        record.steps = report.steps
        record.tools = ", ".join(f"{name}:{outcome}" for name, outcome in report.tools)
        record.brain = self._brain(report.lane)
        record.local_first_token_s = report.local_first_token_s
        record.cloud_first_token_s = report.cloud_first_token_s
        return report

    def _now(self) -> datetime:
        return self._clock(ZoneInfo(self.settings.assistant_timezone))

    def _messages(self, user_text: str) -> list[Message]:
        system = system_prompt(
            self.persona, self.glossary, self._now(), self.settings.reply_target_sentences,
            (self.settings.assistant_name, self.settings.assistant_name_long),
        )  # fmt: skip
        return [Message("system", system), *self.history.messages(), Message("user", user_text)]

    async def _transcribe(self, utterance: VoiceUtterance, record: AssistantTurnRecord) -> str:
        transcript = await self.stt.transcribe(
            utterance.pcm, utterance.sample_rate, language=self.settings.stt_language
        )
        record.t_stt = time.time()
        text = transcript.text.strip()
        if self.glossary is not None and text:
            text, found = self.glossary.correct(text)
            record.corrections = len(found)
            for correction in found:
                if self._on_correction is not None:
                    self._on_correction(correction)
        record.words_in = len(text.split())
        return text

    async def _say(self, text: str, record: AssistantTurnRecord) -> None:
        """Speak one fixed local line. If speech itself is broken there is nothing more to do."""
        try:
            await self.sink.play(self._one(text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record.error = record.error or f"fallback speech failed: {type(exc).__name__}"
            log.warning("turn %d: could not speak the fallback line", record.turn)

    async def say_line(self, text: str) -> None:
        """Speak one fixed line outside any turn (the startup line). Failures are ignored."""
        with contextlib.suppress(Exception):
            await self.sink.play(self._one(text))

    async def _one(self, text: str) -> AsyncIterator[AudioChunk]:
        async for chunk in self.tts.synthesize_stream(text, self.options):
            yield chunk

    async def _produce(
        self,
        messages: list[Message],
        record: AssistantTurnRecord,
        queue: "asyncio.Queue[AudioChunk | Exception | None]",
        spoken: list[str],
    ) -> None:
        """LLM text -> chunks -> cleaned -> TTS audio on the queue. Ends with None or an error.

        Soft limits (sentences, seconds) are checked only at a sentence end, so speech never stops
        mid-sentence; past a limit the reply is wrapped up with an offer to go on. If the provider
        cut the reply (finish reason length), the unfinished tail is dropped and the same offer
        is made. The cause of every early stop is recorded in `record.truncation`.
        """
        stream = ClauseStream(self.settings.first_chunk_min_words)
        audio_seconds = 0.0
        sentences = 0
        reply: list[str] = []
        deltas = self.llm.stream(messages, max_tokens=self.settings.llm_max_tokens)

        async def speak(chunk_text: str) -> bool:
            """Speak one chunk; False once a soft limit is reached at a sentence end."""
            nonlocal audio_seconds, sentences
            cleaned = clean_reply(chunk_text)
            if not cleaned:
                return True
            record.clauses += 1
            if spoken:  # a short silence between chunks, longer after a sentence
                gap = (
                    self.settings.tts_sentence_pause_s
                    if spoken[-1].endswith((".", "?", "!"))
                    else self.settings.tts_clause_pause_s
                )
                await queue.put(silence(gap))
            spoken.append(cleaned)
            if self.on_said is not None:
                self.on_said(cleaned)
            async for audio in self.tts.synthesize_stream(cleaned, self.options):
                if record.t_first_chunk is None:
                    record.t_first_chunk = time.time()
                audio_seconds += audio.seconds
                await queue.put(audio)
            if not cleaned.endswith((".", "?", "!")):
                return True
            sentences += 1
            if sentences >= self.settings.reply_soft_max_sentences:
                record.truncation = record.truncation or "sentence_soft_cap"
                return False
            if audio_seconds >= self.settings.max_reply_seconds:
                record.truncation = record.truncation or "time_soft_cap"
                return False
            return True

        stage = "llm"
        try:
            keep_going = True
            iterator = deltas.__aiter__()
            while True:
                stage = "llm"
                try:
                    delta = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                if record.t_llm_first is None:
                    record.t_llm_first = time.time()
                reply.append(delta)
                stage = "tts"
                for chunk_text in stream.feed(delta):
                    keep_going = keep_going and await speak(chunk_text)
                if not keep_going:
                    break
            reason = getattr(self.llm, "last_finish_reason", None)
            cut_by_provider = reason in LENGTH_REASONS
            if cut_by_provider:
                record.truncation = "provider_length"
            if keep_going:
                for chunk_text in stream.finish(drop_unfinished=cut_by_provider):
                    keep_going = keep_going and await speak(chunk_text)
            if record.truncation:
                stage = "tts"
                await speak(self.settings.wrap_up_line)
            record.chars_out = sum(len(p) for p in reply)
            if self.settings.log_transcripts:
                record.reply = "".join(reply)
            await queue.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record.chars_out = sum(len(p) for p in reply)
            record.truncation = record.truncation or f"{stage}_error"
            await queue.put(StageError(stage, exc))
        finally:
            close = getattr(deltas, "aclose", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await asyncio.shield(close())

    async def run_turn(self, utterance: VoiceUtterance, turn: int) -> AssistantTurnRecord:
        record = AssistantTurnRecord(
            turn, utterance.end_reason.value, utterance.seconds,
            utterance.end_marker_at or utterance.ended_at, time.time(),
        )  # fmt: skip
        self._current = record
        record.stt, record.tts = self.stt.name, self.tts.name
        producer: asyncio.Task[None] | None = None
        spoken: list[str] = []
        text = ""
        try:
            if self._debug is not None and self.settings.debug_save_audio:
                self._debug.save_audio(turn, utterance)
            text = await self._transcribe(utterance, record)
            if self.wake is not None and text:
                wake = self.wake.strip(text)
                record.woke, text = wake.woke, wake.text
                if self.settings.wake_required and not wake.woke:
                    record.outcome = "ignored"
                    return record
                if wake.only_wake:
                    record.outcome = "wake_ack"
                    await self._say(self.settings.wake_ack, record)
                    return record
            if self.on_text is not None:
                self.on_text(text)
            if self.on_heard is not None:
                self.on_heard(text)
            if self.settings.log_transcripts:
                record.heard = text
            if not text:
                record.outcome = "empty"
                await self._say(self.settings.fallback_no_speech, record)
                return record
            queue: asyncio.Queue[AudioChunk | Exception | None] = asyncio.Queue(QUEUE_DEPTH)

            async def chunks() -> AsyncIterator[AudioChunk]:
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    if isinstance(item, Exception):
                        raise item
                    yield item

            def mark_playing() -> None:
                if record.t_playing is None:
                    record.t_playing = time.time()

            self.sink.on_playing = mark_playing
            producer = asyncio.create_task(
                self._produce(self._messages(text), record, queue, spoken)
            )
            try:
                await self.sink.play(chunks())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._failed(exc, record, spoken)
            else:
                record.t_done = time.time()
            report = self._absorb_report(record)
            if record.outcome == "spoken" and not (report and report.skip_memory):
                notes = report.notes if report else []
                self.history.add(Exchange(text, " ".join(spoken), turn=turn, notes=notes))
        except asyncio.CancelledError:
            record.outcome = "cancelled"
            record.truncation = record.truncation or "barge_in"
            if text:
                report = self._absorb_report(record)
                notes = report.notes if report else []
                self.history.add(
                    Exchange(text, " ".join(spoken), interrupted=True, turn=turn, notes=notes)
                )
            raise
        except Exception as exc:
            record.outcome = "error"
            record.error = f"{type(exc).__name__}: {str(exc)[:160]}"
            log.warning("turn %d failed: %s", turn, type(exc).__name__)
        finally:
            if producer is not None:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
            self._finish(record, text, " ".join(spoken))
        return record

    async def _failed(self, exc: Exception, record: AssistantTurnRecord, spoken: list[str]) -> None:
        """The reply stream failed: say a short local line unless something was already said."""
        cause = exc.cause if isinstance(exc, StageError) else exc
        if (isinstance(exc, StageError) and exc.stage == "llm") or isinstance(cause, LlmError):
            record.outcome = "llm_error"
            line = self.settings.fallback_llm_error
        else:
            record.outcome = "tts_error"
            line = self.settings.fallback_tts_error
        record.error = f"{type(cause).__name__}: {str(cause)[:160]}"
        log.warning("turn %d: %s", record.turn, type(cause).__name__)
        if spoken:
            await self.sink.stop()  # leave the speaker clean after a partly spoken reply
        else:
            await self._say(line, record)

    def _finish(self, record: AssistantTurnRecord, heard: str, reply: str) -> None:
        if isinstance(self.llm, RoutedLlmClient):
            self.llm.state.last_heard = heard
            if reply:
                self.llm.state.last_reply = reply
        if self._turn_log is not None:
            self._turn_log.write(record)
        if self._on_record is not None:
            self._on_record(record)
