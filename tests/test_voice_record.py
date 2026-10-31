import asyncio
from collections.abc import AsyncIterator

import pytest

from aiglasses.domain.errors import GlassesTimeoutError
from aiglasses.domain.models import GlassesEvent
from aiglasses.services.voice import record_burst

PACKET = bytes(range(40))


def event(kind: str, raw: bytes = b"", at: float = 100.0) -> GlassesEvent:
    return GlassesEvent(kind=kind, raw=raw, timestamp=at)


async def stream(*items: GlassesEvent | float) -> AsyncIterator[GlassesEvent]:
    for item in items:
        if isinstance(item, float):
            await asyncio.sleep(item)
        else:
            yield item
    await asyncio.sleep(10)


async def test_collects_frames_until_the_end_event() -> None:
    events = stream(
        event("ai_voice"), event("voice_frame", PACKET, 5.0), event("voice_frame", PACKET, 5.02),
        event("ai_voice_end"), event("voice_frame", PACKET),
    )  # fmt: skip
    burst = await record_burst(events, wait_seconds=1)
    assert len(burst.packets) == 2 and burst.ended_by == "end event"
    assert burst.started_at == 5.0 and burst.seconds == pytest.approx(0.04)


async def test_ends_on_silence_when_no_end_event_arrives() -> None:
    events = stream(event("voice_frame", PACKET))
    burst = await record_burst(events, wait_seconds=1, idle_seconds=0.1)
    assert burst.ended_by == "idle" and len(burst.packets) == 1


async def test_ignores_other_events_and_times_out_without_a_burst() -> None:
    events = stream(event("battery"), event("temperature"))
    with pytest.raises(GlassesTimeoutError, match="no AI voice burst"):
        await record_burst(events, wait_seconds=0.1)
