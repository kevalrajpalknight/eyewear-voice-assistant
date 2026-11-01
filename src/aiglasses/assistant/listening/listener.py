"""Passive listening: a microphone, a voice-activity detector, wake-word gating and barge-in.

States: ASLEEP (only a request that starts with the wake phrase is answered) and AWAKE (every
request is answered, no wake phrase needed). The assistant goes back to sleep after
`listen_idle_sleep_s` without speech once it has finished talking. The microphone stays open while
the assistant speaks (full duplex): the user speaking over it stops the playback. The detector is
energy based and adapts to the room's noise (tune it with the VAD_* settings). Frames come from
`MicStream`, a worker process, so sounddevice stays out of the BLE process.
"""

import asyncio
import struct
import sys
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from aiglasses.domain.errors import GlassesError
from aiglasses.services.voice import EndReason, VoiceUtterance

HEADER = struct.Struct("<cI")
SAMPLE_RATE = 16000
START_FRAMES = 3  # consecutive loud frames that start an utterance
BARGE_IN_FRAMES = 15  # while the assistant talks the user must keep it up this long (300 ms)
MIN_SPEECH_S = 0.3  # shorter blips (a click, a cough) are dropped
MAX_UTTERANCE_S = 15.0
PREROLL_S = 0.3
HOLDOFF_S = 1.0  # ignore the room after the assistant stops: its own tail and echo
ECHO_REFERENCE_S = 2.0  # how far back the assistant's own echo level is remembered
ECHO_LAG_S = 0.32  # the newest frames are left out so a user's burst is not its own reference
NO_BARGE_IN = 1e6  # a threshold factor nobody can reach
ECHO_MARGIN = 1.5  # speech over the assistant must beat its echo peak by this much
FLOOR_ALPHA = 0.05
Pcm = npt.NDArray[np.int16]


class MicError(GlassesError):
    """The microphone worker failed or could not open the device."""


@dataclass(frozen=True)
class VadEvent:
    kind: str  # "start" | "end"
    pcm: Pcm | None = None
    started_at: float = 0.0
    ended_at: float = 0.0


def rms(frame: Pcm) -> float:
    return float(np.sqrt(np.mean(np.square(frame.astype(np.float32))))) if len(frame) else 0.0


class EnergyVad:
    """Speech starts after a few loud frames and ends after `end_silence_s` of quiet."""

    def __init__(
        self, *, min_rms: float, ratio: float, end_silence_s: float, rate: int = SAMPLE_RATE
    ) -> None:
        self.min_rms, self.ratio, self.end_silence_s, self.rate = (
            min_rms,
            ratio,
            end_silence_s,
            rate,
        )
        self.floor = min_rms / ratio
        self.in_speech = False
        self._loud = 0
        self._quiet_s = 0.0
        self._frames: list[Pcm] = []
        self._pre: deque[Pcm] = deque()
        self._pre_s = 0.0
        self._started_at = 0.0

    def threshold(self, factor: float = 1.0) -> float:
        return max(self.floor * self.ratio, self.min_rms) * factor

    def feed(self, frame: Pcm, now: float, *, factor: float = 1.0) -> list[VadEvent]:
        """Feed one frame; `factor` raises the bar (used while the assistant is speaking)."""
        seconds = len(frame) / self.rate
        level = rms(frame)
        loud = level > self.threshold(factor)
        events: list[VadEvent] = []
        if not self.in_speech:
            if not loud and factor <= 1.0:  # the assistant's own echo is not the room's noise
                self.floor = (1 - FLOOR_ALPHA) * self.floor + FLOOR_ALPHA * level
            self._pre.append(frame)
            self._pre_s += seconds
            while self._pre_s > PREROLL_S and len(self._pre) > 1:
                self._pre_s -= len(self._pre.popleft()) / self.rate
            self._loud = self._loud + 1 if loud else 0
            needed = BARGE_IN_FRAMES if factor > 1.0 else START_FRAMES
            if self._loud >= needed:
                self.in_speech = True
                self._frames = list(self._pre)
                self._started_at = now - self._pre_s
                self._quiet_s = 0.0
                self._pre.clear()
                self._pre_s = 0.0
                events.append(VadEvent("start", started_at=self._started_at))
            return events
        self._frames.append(frame)
        self._quiet_s = 0.0 if loud else self._quiet_s + seconds
        total = sum(len(f) for f in self._frames) / self.rate
        if self._quiet_s >= self.end_silence_s or total >= MAX_UTTERANCE_S:
            pcm = np.concatenate(self._frames)
            speech_s = total - self._quiet_s
            self.reset()
            if speech_s >= MIN_SPEECH_S:
                events.append(VadEvent("end", pcm, self._started_at, now))
            else:
                events.append(VadEvent("end", None, self._started_at, now))
        return events

    def reset(self) -> None:
        self.in_speech = False
        self._loud = 0
        self._quiet_s = 0.0
        self._frames = []
        self._pre.clear()
        self._pre_s = 0.0


class MicStream:
    """Frames of 16 kHz mono int16 from the worker process."""

    def __init__(self, device: str | None = None, block_ms: int = 20) -> None:
        self._device, self._block_ms = device, block_ms
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        args = [
            sys.executable,
            "-m",
            "aiglasses.assistant.listening.mic_worker",
            "--block-ms",
            str(self._block_ms),
        ]
        if self._device:
            args += ["--device", self._device]
        self._process = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
        )
        kind, payload = await asyncio.wait_for(self._read(), 15)
        if kind == b"X":
            raise MicError(f"microphone: {payload.decode(errors='replace')}")

    async def _read(self) -> tuple[bytes, bytes]:
        assert self._process is not None and self._process.stdout is not None
        stream = self._process.stdout
        try:
            kind, length = HEADER.unpack(await stream.readexactly(HEADER.size))
            return kind, await stream.readexactly(length) if length else b""
        except asyncio.IncompleteReadError as exc:
            raise MicError("the microphone worker stopped") from exc

    async def frames(self) -> AsyncIterator[Pcm]:
        while True:
            kind, payload = await self._read()
            if kind == b"F":
                yield np.frombuffer(payload, dtype=np.int16)
            elif kind == b"X":
                raise MicError(f"microphone: {payload.decode(errors='replace')}")

    async def close(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except asyncio.TimeoutError:
                process.kill()


class MicListener:
    """Turns frames into utterances, decides wake/sleep, and interrupts on barge-in."""

    def __init__(
        self,
        frames: AsyncIterator[Pcm],
        vad: EnergyVad,
        *,
        busy: Callable[[], bool],
        interrupt: Callable[[], None],
        set_require_wake: Callable[[bool], None],
        report: Callable[[str], None],
        idle_sleep_s: float,
        duplex: bool,
        barge_in_ratio: float,
        echo_settling: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._frames, self._vad = frames, vad
        self._busy, self._interrupt, self._set_require_wake = busy, interrupt, set_require_wake
        self._report = report
        self._idle_sleep_s, self._duplex, self._barge_in_ratio = (
            idle_sleep_s,
            duplex,
            barge_in_ratio,
        )
        self._clock = clock
        self._echo_settling = echo_settling
        self.awake = False
        self._last_activity = clock()
        self._holdoff_until = 0.0
        self._echo: deque[tuple[float, float]] = deque()
        set_require_wake(True)

    def wake(self) -> None:
        """A request began with the wake phrase (or the assistant was addressed): stay awake."""
        self._last_activity = self._clock()
        if not self.awake:
            self.awake = True
            self._set_require_wake(False)
            self._report("awake")

    def _sleep(self) -> None:
        self.awake = False
        self._set_require_wake(True)
        self._report("sleeping: say the wake phrase to start")

    def _barge_in_factor(self, frame: Pcm, now: float) -> float:
        """How much louder than normal speech must be: also beat the assistant's own echo."""
        self._echo.append((now, rms(frame)))
        if self._echo_settling():
            return NO_BARGE_IN
        while self._echo and now - self._echo[0][0] > ECHO_REFERENCE_S:
            self._echo.popleft()
        older = [level for at, level in self._echo if now - at > ECHO_LAG_S]
        reference = max(older, default=0.0) * ECHO_MARGIN
        return max(self._barge_in_ratio, reference / max(self._vad.threshold(), 1.0))

    async def utterances(self) -> AsyncIterator[VoiceUtterance]:
        async for frame in self._frames:
            now = self._clock()
            busy = self._busy()
            if busy:
                self._last_activity = now
                self._holdoff_until = now + HOLDOFF_S
                if not self._duplex:
                    self._vad.reset()
                    continue
            elif now < self._holdoff_until and not self._vad.in_speech:
                continue
            factor = self._barge_in_factor(frame, now) if busy else 1.0
            for event in self._vad.feed(frame, now, factor=factor):
                if event.kind == "start":
                    self._last_activity = now
                    if busy:
                        self._report("barge-in: stopping")
                        self._interrupt()
                elif event.pcm is not None:
                    self._last_activity = now
                    yield _utterance(event)
            if (
                self.awake
                and not busy
                and not self._vad.in_speech
                and now - self._last_activity > self._idle_sleep_s
            ):
                self._sleep()


def _utterance(event: VadEvent) -> VoiceUtterance:
    assert event.pcm is not None
    return VoiceUtterance(
        pcm=event.pcm,
        sample_rate=SAMPLE_RATE,
        started_at=event.started_at,
        ended_at=event.ended_at,
        frame_count=len(event.pcm) // (SAMPLE_RATE // 50),
        end_reason=EndReason.END_MARKER,
        start_marker_at=None,
        end_marker_at=event.ended_at,
    )
