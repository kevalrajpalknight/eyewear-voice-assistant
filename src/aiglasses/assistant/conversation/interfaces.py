"""The four seams of the voice assistant. Every cloud, audio and BLE interaction sits behind one."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

Pcm = npt.NDArray[np.int16]
TIME_MARKER = "Current date and time:"  # starts the last paragraph of the system prompt


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
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: tuple[ToolCall, ...] = ()  # on an assistant message that called tools
    tool_call_id: str | None = None  # on the "tool" message that answers one


@dataclass
class TtsOptions:
    voice: str
    speed: float
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


class ToolLlmClient(LlmClient, Protocol):
    """An LLM that can also call tools natively (the cloud lane)."""

    def stream_with_tools(
        self, messages: list[Message], tools: list[dict[str, Any]], *, max_tokens: int
    ) -> AsyncIterator[str | ToolCall]: ...


class AudioSink(Protocol):
    on_playing: Callable[[], None] | None  # called when the first sound is actually written

    async def play(self, chunks: AsyncIterator[AudioChunk]) -> None:
        """Play until the iterator ends. Must return promptly after `stop()`."""
        ...

    async def stop(self) -> None: ...
