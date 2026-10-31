"""Collect one AI-voice burst from the event stream. Read-only: nothing is sent."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from aiglasses.domain.errors import GlassesTimeoutError
from aiglasses.domain.models import GlassesEvent

VOICE_PACKET_BYTES = 40
IDLE_END_SECONDS = 2.0


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
) -> VoiceBurst:
    """Wait for the first voice frame, then collect until the end event, silence or the cap."""
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
        if event.kind == "voice_frame":
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
            return VoiceBurst(packets, started_at, "end event")
