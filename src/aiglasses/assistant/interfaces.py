"""The four seams of the voice assistant. Every cloud, audio and BLE interaction sits behind one."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import numpy.typing as npt

Pcm = npt.NDArray[np.int16]


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    audio_seconds: float
    wall_seconds: float

    @property
    def real_time_factor(self) -> float:
        return self.wall_seconds / self.audio_seconds if self.audio_seconds else 0.0


@dataclass(frozen=True)
class AudioChunk:
    samples: Pcm
    sample_rate: int
    elapsed: float = 0.0  # seconds since synthesis started; the first chunk gives time-to-audio

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.sample_rate


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class TtsOptions:
    voice: str = "bf_isabella"
    speed: float = 1.05
    language: str = "en"
    extra: dict[str, str] = field(default_factory=dict)


class SpeechToText(Protocol):
    name: str

    async def transcribe(
        self, pcm: Pcm, sample_rate: int = 16000, *, language: str = "en"
    ) -> Transcript: ...


class TextToSpeech(Protocol):
    name: str

    def synthesize_stream(self, text: str, options: TtsOptions) -> AsyncIterator[AudioChunk]: ...


class LlmClient(Protocol):
    name: str

    def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]: ...


class AudioSink(Protocol):
    on_playing: Callable[[], None] | None  # called when the first sound is actually written

    async def play(self, chunks: AsyncIterator[AudioChunk]) -> None:
        """Play until the iterator ends. Must return promptly after `stop()`."""
        ...

    async def stop(self) -> None: ...
