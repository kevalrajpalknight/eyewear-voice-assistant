"""Echo loop: utterance -> STT -> clauses -> TTS -> speaker. No LLM, no network.

Privacy: transcripts and audio are only kept when a `Debug` object is passed. The turn log holds
timings and counts, never text.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from aiglasses.assistant.chunker import split_clauses
from aiglasses.assistant.glossary import Correction
from aiglasses.assistant.interfaces import (
    AudioChunk,
    AudioSink,
    SpeechToText,
    TextToSpeech,
    TtsOptions,
)
from aiglasses.services.voice import VoiceUtterance
from aiglasses.timefmt import stamps

log = logging.getLogger("aiglasses.assistant")


class Loggable(Protocol):
    def to_json(self) -> dict[str, object]: ...


@dataclass
class TurnRecord:
    turn: int
    end_reason: str
    audio_seconds: float
    frames: int
    t_end: float  # the glasses' end marker (or the last frame when there was none)
    t_received: float  # the utterance was decoded and handed to the pipeline
    t_stt: float | None = None
    t_first_chunk: float | None = None
    t_playing: float | None = None
    t_done: float | None = None
    words: int = 0
    clauses: int = 0
    first_clause_words: int = 0
    outcome: str = "spoken"  # spoken | empty | cancelled | error
    error: str = ""
    text: str | None = None  # only with Debug
    raw_text: str | None = None  # console only, and only when a corrector is on
    corrected_text: str | None = None  # console only, with raw_text
    corrections: int = 0

    @staticmethod
    def _gap(later: float | None, earlier: float | None) -> float | None:
        return None if later is None or earlier is None else round(later - earlier, 3)

    def to_json(self) -> dict[str, object]:
        row: dict[str, object] = {
            **stamps(datetime.fromtimestamp(self.t_received, timezone.utc)),
            "turn": self.turn, "outcome": self.outcome, "end_reason": self.end_reason,
            "audio_s": round(self.audio_seconds, 2), "frames": self.frames,
            "words": self.words, "clauses": self.clauses,
            "first_clause_words": self.first_clause_words, "corrections": self.corrections,
            "decode_s": self._gap(self.t_received, self.t_end),
            "stt_s": self._gap(self.t_stt, self.t_received),
            "first_chunk_s": self._gap(self.t_first_chunk, self.t_stt),
            "playback_start_s": self._gap(self.t_playing, self.t_first_chunk),
            "end_to_sound_s": self._gap(self.t_playing, self.t_end),
            "spoken_s": self._gap(self.t_done, self.t_playing),
        }  # fmt: skip
        if self.error:
            row["error"] = self.error
        if self.text is not None:
            row["text"] = self.text
        return row


class TurnLog:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, record: Loggable) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_json()) + "\n")


class Debug:
    """Opt-in: keep the transcript in the log and save each input utterance as a WAV."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save_audio(self, turn: int, utterance: VoiceUtterance) -> None:
        import soundfile as sf

        self.directory.mkdir(parents=True, exist_ok=True)
        sf.write(self.directory / f"turn{turn:03d}.wav", utterance.pcm, utterance.sample_rate)


class EchoPipeline:
    def __init__(
        self,
        stt: SpeechToText,
        tts: TextToSpeech,
        sink: AudioSink,
        options: TtsOptions,
        *,
        turn_log: TurnLog | None = None,
        debug: Debug | None = None,
        on_record: Callable[[TurnRecord], None] | None = None,
        first_clause_max_words: int,
        corrector: Callable[[str], tuple[str, list[Correction]]] | None = None,
        on_correction: Callable[[Correction], None] | None = None,
    ) -> None:
        self.stt, self.tts, self.sink, self.options = stt, tts, sink, options
        self._first_max = first_clause_max_words
        self._corrector = corrector
        self._on_correction = on_correction
        self._turn_log = turn_log
        self._debug = debug
        self._on_record = on_record

    async def run_turn(self, utterance: VoiceUtterance, turn: int) -> TurnRecord:
        record = TurnRecord(
            turn, utterance.end_reason.value, utterance.seconds, utterance.frame_count,
            utterance.end_marker_at or utterance.ended_at, time.time(),
        )  # fmt: skip
        producer: asyncio.Task[None] | None = None
        try:
            if self._debug is not None:
                self._debug.save_audio(turn, utterance)
            transcript = await self.stt.transcribe(utterance.pcm, utterance.sample_rate)
            record.t_stt = time.time()
            text = transcript.text.strip()
            if self._corrector is not None and text:
                record.raw_text = text
                text, found = self._corrector(text)
                record.corrected_text = text
                record.corrections = len(found)
                for correction in found:
                    if self._on_correction is not None:
                        self._on_correction(correction)
            record.words = len(text.split())
            if self._debug is not None:
                record.text = text
            if not text:
                record.outcome = "empty"
                return record
            clauses = split_clauses(text, self._first_max)
            record.clauses = len(clauses)
            record.first_clause_words = len(clauses[0].split())
            queue: asyncio.Queue[AudioChunk | Exception | None] = asyncio.Queue(maxsize=3)

            async def produce() -> None:
                try:
                    for clause in clauses:
                        async for chunk in self.tts.synthesize_stream(clause, self.options):
                            if record.t_first_chunk is None:
                                record.t_first_chunk = time.time()
                            await queue.put(chunk)
                    await queue.put(None)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await queue.put(exc)

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
            producer = asyncio.create_task(produce())
            await self.sink.play(chunks())
            record.t_done = time.time()
        except asyncio.CancelledError:
            record.outcome = "cancelled"
            raise
        except Exception as exc:
            record.outcome = "error"
            record.error = f"{type(exc).__name__}: {str(exc)[:160]}"
            log.warning("turn %d failed: %s", turn, record.error)
        finally:
            if producer is not None:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
            if self._turn_log is not None:
                self._turn_log.write(record)
            if self._on_record is not None:
                self._on_record(record)
        return record


class TurnPipeline(Protocol):
    sink: AudioSink

    async def run_turn(self, utterance: VoiceUtterance, turn: int) -> object: ...


class TurnRunner:
    """One turn at a time. A new click cancels playback and generation, then a fresh turn starts."""

    def __init__(self, pipeline: TurnPipeline) -> None:
        self.pipeline = pipeline
        self._turn = 0
        self._task: asyncio.Task[object] | None = None
        self._interrupt: asyncio.Task[None] | None = None

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def interrupt(self) -> None:
        """Called (synchronously) when a new rear click starts: stop talking right away."""
        if self.busy and (self._interrupt is None or self._interrupt.done()):
            self._interrupt = asyncio.get_running_loop().create_task(self.cancel())

    async def cancel(self) -> None:
        task = self._task
        if task is not None and not task.done():
            await self.pipeline.sink.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def submit(self, utterance: VoiceUtterance) -> None:
        await self.cancel()
        self._turn += 1
        self._task = asyncio.create_task(self.pipeline.run_turn(utterance, self._turn))

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def close(self) -> None:
        await self.cancel()
        if self._interrupt is not None:
            await asyncio.gather(self._interrupt, return_exceptions=True)
