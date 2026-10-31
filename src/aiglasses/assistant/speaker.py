"""Audio sinks for the assistant. The PC speaker runs in its own process (ADR 0004)."""

import asyncio
import struct
import sys
from collections.abc import AsyncIterator, Callable

import numpy as np

from aiglasses.assistant.interfaces import AudioChunk
from aiglasses.domain.errors import GlassesError

HEADER = struct.Struct("<cI")


class SpeakerError(GlassesError):
    """The speaker worker failed or exited."""


class NullSink:
    """Consumes audio without playing it: for latency runs and tests. Makes no sound."""

    def __init__(self) -> None:
        self.on_playing: Callable[[], None] | None = None
        self.seconds_received = 0.0
        self.stops = 0

    async def play(self, chunks: AsyncIterator[AudioChunk]) -> None:
        first = True
        async for chunk in chunks:
            if first:
                first = False
                if self.on_playing is not None:
                    self.on_playing()
            self.seconds_received += chunk.seconds

    async def stop(self) -> None:
        self.stops += 1


class SubprocessSpeakerSink:
    """Plays through the default PC output in a worker process. Call `start()` before use."""

    def __init__(self, volume_limit: float = 1.0) -> None:
        self.on_playing: Callable[[], None] | None = None
        self._volume = volume_limit
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._done = asyncio.Event()
        self._acked = asyncio.Event()
        self._errors: list[str] = []
        self._ready = asyncio.Event()

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "aiglasses.assistant.audio_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        self._reader = asyncio.create_task(self._read())
        await asyncio.wait_for(self._ready.wait(), 15)

    async def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        try:
            while True:
                kind, length = HEADER.unpack(await stream.readexactly(HEADER.size))
                payload = await stream.readexactly(length) if length else b""
                if kind == b"R":
                    self._ready.set()
                elif kind == b"P" and self.on_playing is not None:
                    self.on_playing()
                elif kind == b"D":
                    self._done.set()
                elif kind == b"T":
                    self._acked.set()
                elif kind == b"X":
                    self._errors.append(payload.decode(errors="replace"))
        except asyncio.IncompleteReadError:
            self._done.set()
            self._acked.set()

    async def _send(self, kind: bytes, payload: bytes = b"") -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise SpeakerError("the speaker worker is not running")
        process.stdin.write(HEADER.pack(kind, len(payload)) + payload)
        await process.stdin.drain()

    async def play(self, chunks: AsyncIterator[AudioChunk]) -> None:
        self._done.clear()
        async for chunk in chunks:
            samples = chunk.samples if self._volume >= 1.0 else (chunk.samples * self._volume)
            payload = struct.pack("<I", chunk.sample_rate) + samples.astype(np.int16).tobytes()
            await self._send(b"C", payload)
        await self._send(b"E")
        await self._done.wait()
        if self._errors:
            raise SpeakerError("; ".join(self._errors))

    async def stop(self) -> None:
        if self._process is None or self._process.returncode is not None:
            return
        self._acked.clear()
        await self._send(b"S")
        await asyncio.wait_for(self._acked.wait(), 2)
        self._done.set()

    async def close(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            try:
                await self._send(b"Q")
                await asyncio.wait_for(process.wait(), 3)
            except (SpeakerError, asyncio.TimeoutError):
                process.kill()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
