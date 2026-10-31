"""The conversation core: utterance -> STT -> (glossary) -> router/LLM stream -> cleaner -> clauses
-> TTS -> speaker, speaking the first clause while the LLM is still writing.

Privacy: text and audio are kept only when LOG_TRANSCRIPTS / DEBUG_SAVE_AUDIO say so. The turn log
holds lengths and timings. Failures are spoken as short local lines and never end the loop.
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiglasses.assistant.clauses import ClauseStream
from aiglasses.assistant.cleaner import clean_reply
from aiglasses.assistant.echo import Debug, TurnLog
from aiglasses.assistant.glossary import Correction, Glossary
from aiglasses.assistant.interfaces import (
    AudioChunk,
    AudioSink,
    LlmClient,
    Message,
    SpeechToText,
    TextToSpeech,
    TtsOptions,
)
from aiglasses.assistant.llm_client import LlmError
from aiglasses.assistant.router import RouteDecision, RoutedLlmClient
from aiglasses.services.voice import VoiceUtterance
from aiglasses.settings import AssistantSettings
from aiglasses.timefmt import stamps

log = logging.getLogger("aiglasses.assistant")
INTERRUPTED_NOTE = " [the user interrupted before the end]"
QUEUE_DEPTH = 3


class StageError(Exception):
    """A failure tagged with where it happened: the language model or the speech engine."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(f"{stage}: {type(cause).__name__}")
        self.stage, self.cause = stage, cause


@dataclass
class Exchange:
    user: str
    assistant: str
    interrupted: bool = False
    turn: int = 0


class History:
    """Rolling memory of recent exchanges, newest last."""

    def __init__(self, turns: int) -> None:
        self.turns = turns
        self._items: deque[Exchange] = deque(maxlen=max(turns, 1))

    def add(self, exchange: Exchange) -> None:
        if self.turns > 0:
            self._items.append(exchange)

    def drop_turn(self, turn: int) -> bool:
        """Remove the newest exchange if it belongs to `turn` (a request that gets re-asked)."""
        if self._items and self._items[-1].turn == turn:
            self._items.pop()
            return True
        return False

    @property
    def last(self) -> Exchange | None:
        return self._items[-1] if self._items else None

    def messages(self) -> list[Message]:
        found: list[Message] = []
        for item in self._items:
            found.append(Message("user", item.user))
            found.append(
                Message(
                    "assistant", item.assistant + (INTERRUPTED_NOTE if item.interrupted else "")
                )
            )
        return found

    def __len__(self) -> int:
        return len(self._items)


def system_prompt(persona: str, glossary: Glossary | None, now: datetime) -> str:
    """Persona + recognition-error note (from the glossary) + the current date and time."""
    parts = [persona.strip()]
    if glossary:
        parts.append(glossary.prompt_note())
    parts.append(f"Current date and time: {now:%A %d %B %Y, %H:%M} ({now.tzinfo}).")
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
    corrections: int = 0
    outcome: str = "spoken"  # spoken | empty | cancelled | llm_error | tts_error | error
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
            "router_s": round(self.router_s, 3),
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
        clock: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.stt, self.tts, self.sink, self.llm = stt, tts, sink, llm
        self.options, self.settings, self.persona, self.glossary = (
            options,
            settings,
            persona,
            glossary,
        )
        self.history = History(settings.history_turns)
        self._turn_log, self._debug = turn_log, debug
        self._on_record, self._on_correction = on_record, on_correction
        self._clock = clock or (lambda tz: datetime.now(tz))
        self._current: AssistantTurnRecord | None = None
        if isinstance(llm, RoutedLlmClient):
            llm.on_decision = self._note_decision

    def _note_decision(self, decision: RouteDecision) -> None:
        record = self._current
        if record is not None:
            record.t_router = time.time()
            record.tier, record.route_role = decision.tier, decision.role
            record.route_reason, record.router_s = decision.reason, decision.router_s

    def _now(self) -> datetime:
        return self._clock(ZoneInfo(self.settings.assistant_timezone))

    def _messages(self, user_text: str) -> list[Message]:
        system = system_prompt(self.persona, self.glossary, self._now())
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
        """LLM text -> clauses -> cleaned -> TTS chunks on the queue. Ends with None or an error."""
        stream = ClauseStream(self.settings.tts_first_clause_max_words)
        audio_seconds = 0.0
        reply: list[str] = []
        deltas = self.llm.stream(messages, max_tokens=self.settings.llm_max_tokens)

        async def speak(clause: str) -> bool:
            nonlocal audio_seconds
            cleaned = clean_reply(clause)
            if not cleaned:
                return True
            record.clauses += 1
            spoken.append(cleaned)
            async for chunk in self.tts.synthesize_stream(cleaned, self.options):
                if record.t_first_chunk is None:
                    record.t_first_chunk = time.time()
                audio_seconds += chunk.seconds
                await queue.put(chunk)
            return audio_seconds < self.settings.max_reply_seconds

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
                for clause in stream.feed(delta):
                    keep_going = keep_going and await speak(clause)
                if not keep_going:
                    break
            if keep_going:
                for clause in stream.finish():
                    keep_going = keep_going and await speak(clause)
            record.chars_out = sum(len(p) for p in reply)
            if self.settings.log_transcripts:
                record.reply = "".join(reply)
            await queue.put(None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record.chars_out = sum(len(p) for p in reply)
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
        producer: asyncio.Task[None] | None = None
        spoken: list[str] = []
        text = ""
        try:
            if self._debug is not None and self.settings.debug_save_audio:
                self._debug.save_audio(turn, utterance)
            text = await self._transcribe(utterance, record)
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
            if record.outcome == "spoken":
                self.history.add(Exchange(text, " ".join(spoken), turn=turn))
        except asyncio.CancelledError:
            record.outcome = "cancelled"
            if text:
                self.history.add(Exchange(text, " ".join(spoken), interrupted=True, turn=turn))
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
