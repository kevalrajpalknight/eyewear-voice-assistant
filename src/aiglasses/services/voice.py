"""Collect one AI-voice burst from the event stream. Read-only: nothing is sent."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
import numpy.typing as npt

from aiglasses.domain.errors import GlassesTimeoutError
from aiglasses.domain.models import GlassesEvent
from aiglasses.protocol.voice import VoiceDecoder

log = logging.getLogger("aiglasses.voice")
MIN_FRAMES = 5  # 100 ms; anything shorter is not an utterance

VOICE_PACKET_BYTES = 40
IDLE_END_SECONDS = 2.0


@dataclass
class VoiceWatch:
    """Markers seen while waiting, filled in even when no burst arrives."""

    start_marker_at: float | None = None  # event 03 01
    end_marker_at: float | None = None  # event 0a 01


@dataclass(frozen=True)
class VoiceBurst:
    packets: list[bytes]
    started_at: float
    ended_by: str  # "end event" | "idle" | "max length"

    @property
    def seconds(self) -> float:
        return len(self.packets) * 0.02


async def record_burst(
    events: AsyncIterator[GlassesEvent],
    *,
    wait_seconds: float,
    max_seconds: float = 60.0,
    idle_seconds: float = IDLE_END_SECONDS,
    watch: VoiceWatch | None = None,
) -> VoiceBurst:
    """Start on the first voice frame (marker 03 01 only noted); stop at 0a 01, silence or cap."""
    watch = watch if watch is not None else VoiceWatch()
    packets: list[bytes] = []
    started_at = 0.0
    deadline = time.monotonic() + wait_seconds

    async def next_event(limit: float) -> GlassesEvent | None:
        try:
            return await asyncio.wait_for(anext(events), max(limit, 0.0))
        except asyncio.TimeoutError:
            return None

    while not packets:
        event = await next_event(deadline - time.monotonic())
        if event is None:
            raise GlassesTimeoutError(f"no AI voice burst within {wait_seconds:.0f}s")
        if event.kind == "ai_voice":
            watch.start_marker_at = event.timestamp
        elif event.kind == "ai_voice_end":
            watch.end_marker_at = event.timestamp
        elif event.kind == "voice_frame":
            packets.append(event.raw)
            started_at = event.timestamp

    cap = time.monotonic() + max_seconds
    while True:
        if time.monotonic() >= cap:
            return VoiceBurst(packets, started_at, "max length")
        event = await next_event(min(idle_seconds, cap - time.monotonic()))
        if event is None:
            return VoiceBurst(packets, started_at, "idle")
        if event.kind == "voice_frame":
            packets.append(event.raw)
        elif event.kind == "ai_voice_end":
            watch.end_marker_at = event.timestamp
            return VoiceBurst(packets, started_at, "end event")


class EndReason(str, Enum):
    END_MARKER = "end_marker"  # the glasses sent 0a 01
    TIMEOUT = "timeout"  # frames stopped without an end marker
    DISCONNECT = "disconnect"  # the link dropped mid-utterance
    RESTARTED = "restarted"  # a second start marker arrived: a new rear click


@dataclass(frozen=True)
class VoiceUtterance:
    pcm: npt.NDArray[np.int16]
    sample_rate: int
    started_at: float  # first voice frame
    ended_at: float  # end marker, or the last frame when there was none
    frame_count: int
    end_reason: EndReason
    start_marker_at: float | None
    end_marker_at: float | None

    @property
    def seconds(self) -> float:
        return len(self.pcm) / self.sample_rate


class _Open:
    def __init__(self, first: GlassesEvent | None) -> None:
        self.packets: list[bytes] = []
        self.started_at = 0.0
        self.last_at = 0.0
        self.start_marker_at: float | None = None
        if first is not None:
            self.add(first)

    def add(self, event: GlassesEvent) -> None:
        if not self.packets:
            self.started_at = event.timestamp
        self.packets.append(event.raw)
        self.last_at = event.timestamp


async def voice_requests(
    events: AsyncIterator[GlassesEvent],
    decoder: VoiceDecoder,
    *,
    disconnected: asyncio.Event | None = None,
    idle_seconds: float = IDLE_END_SECONDS,
    min_frames: int = MIN_FRAMES,
    on_start: Callable[[], None] | None = None,
) -> AsyncIterator[VoiceUtterance]:
    """Yield one decoded utterance per rear-click session. Read-only.

    Frames that arrive before the 03 01 marker belong to the session that the marker then opens.
    Sessions shorter than `min_frames` are dropped with a warning (stray frames, not speech).
    `on_start` is called once when a session opens (first marker or frame), before it finishes,
    so a caller can interrupt whatever it is doing when the user clicks again.
    """
    gone = asyncio.ensure_future(disconnected.wait()) if disconnected is not None else None
    pending: asyncio.Future[GlassesEvent] | None = None
    current: _Open | None = None
    marker_only: float | None = None  # a start marker with no frames yet

    async def finish(
        session: _Open, reason: EndReason, end_marker_at: float | None
    ) -> VoiceUtterance | None:
        if len(session.packets) < min_frames:
            log.warning(
                "dropping a %d-frame voice fragment (%s)", len(session.packets), reason.value
            )
            return None
        audio = await asyncio.to_thread(decoder.decode, session.packets)
        return VoiceUtterance(
            audio.samples, audio.sample_rate, session.started_at,
            end_marker_at if end_marker_at is not None else session.last_at,
            len(session.packets), reason, session.start_marker_at, end_marker_at,
        )  # fmt: skip

    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events))
            waiting: set[asyncio.Future[Any]] = {pending}
            if gone is not None:
                waiting.add(gone)
            done, _ = await asyncio.wait(
                waiting,
                timeout=idle_seconds if current is not None else None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if gone is not None and gone in done and pending not in done:
                if current is not None and (u := await finish(current, EndReason.DISCONNECT, None)):
                    yield u
                return
            if not done:
                assert current is not None
                if u := await finish(current, EndReason.TIMEOUT, None):
                    yield u
                current = None
                continue
            event = pending.result()
            pending = None
            if event.kind == "voice_frame":
                if current is None:
                    if marker_only is None and on_start is not None:
                        on_start()
                    current = _Open(None)
                    current.start_marker_at = marker_only
                    marker_only = None
                current.add(event)
            elif event.kind == "ai_voice":
                if current is not None and current.start_marker_at is not None:
                    if u := await finish(current, EndReason.RESTARTED, None):
                        yield u
                    current = None
                if current is None:
                    if marker_only is None and on_start is not None:
                        on_start()
                    marker_only = event.timestamp
                else:
                    current.start_marker_at = event.timestamp
            elif event.kind == "ai_voice_end":
                if current is not None:
                    if u := await finish(current, EndReason.END_MARKER, event.timestamp):
                        yield u
                    current = None
                marker_only = None
    finally:
        for task in (pending, gone):
            if task is not None and not task.done():
                task.cancel()
