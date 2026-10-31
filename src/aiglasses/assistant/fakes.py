"""Deterministic stand-ins for tests and demos. None of them touches the network or a device."""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence

import numpy as np

from aiglasses.assistant.interfaces import (
    AudioChunk,
    Message,
    Pcm,
    Transcript,
    TtsOptions,
)


class FakeStt:
    name = "fake-stt"

    def __init__(self, text: str | Sequence[str] = "hello there", *, delay: float = 0.0) -> None:
        self._texts = [text] if isinstance(text, str) else list(text)
        self._delay = delay
        self.calls: list[tuple[int, str]] = []

    async def transcribe(
        self, pcm: Pcm, sample_rate: int = 16000, *, language: str = "en"
    ) -> Transcript:
        self.calls.append((len(pcm), language))
        await asyncio.sleep(self._delay)
        text = self._texts[min(len(self.calls), len(self._texts)) - 1]
        return Transcript(text, language, len(pcm) / sample_rate, self._delay)


class FakeLlm:
    name = "fake-llm"

    def __init__(
        self,
        replies: Sequence[Sequence[str]] = (("Hello. ", "How can I help?"),),
        *,
        delay: float = 0.0,
        fail_after: int | None = None,
    ) -> None:
        self._replies = [list(r) for r in replies]
        self._delay = delay
        self._fail_after = fail_after
        self.requests: list[list[Message]] = []
        self.max_tokens: list[int] = []

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
        self.requests.append(list(messages))
        self.max_tokens.append(max_tokens)
        reply = self._replies[min(len(self.requests), len(self._replies)) - 1]
        for index, delta in enumerate(reply):
            if self._fail_after is not None and index >= self._fail_after:
                raise RuntimeError("fake LLM failure")
            await asyncio.sleep(self._delay)
            yield delta


class FakeTts:
    name = "fake-tts"

    def __init__(
        self, *, delay: float = 0.0, chunk_seconds: float = 0.1, fail_on: str | None = None
    ) -> None:
        self._delay = delay
        self._chunk_seconds = chunk_seconds
        self._fail_on = fail_on
        self.spoken: list[str] = []
        self.options: list[TtsOptions] = []
        self.rate = 24000

    async def synthesize_stream(self, text: str, options: TtsOptions) -> AsyncIterator[AudioChunk]:
        self.spoken.append(text)
        self.options.append(options)
        if self._fail_on is not None and self._fail_on in text:
            raise RuntimeError("fake TTS failure")
        started = time.monotonic()
        await asyncio.sleep(self._delay)
        samples = np.zeros(int(self.rate * self._chunk_seconds), dtype=np.int16)
        yield AudioChunk(samples, self.rate, time.monotonic() - started)


class FakeSink:
    def __init__(self, *, play_delay: float = 0.0) -> None:
        self.played: list[AudioChunk] = []
        self.stops = 0
        self._play_delay = play_delay
        self._stopped = asyncio.Event()

    async def play(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self._stopped.clear()
        async for chunk in chunks:
            if self._stopped.is_set():
                return
            self.played.append(chunk)
            await asyncio.sleep(self._play_delay)

    async def stop(self) -> None:
        self.stops += 1
        self._stopped.set()
