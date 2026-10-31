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


async def test_start_and_end_markers_are_noted_even_without_a_burst() -> None:
    from aiglasses.services.voice import VoiceWatch

    watch = VoiceWatch()
    events = stream(event("ai_voice", at=7.0), event("ai_voice_end", at=9.0))
    with pytest.raises(GlassesTimeoutError):
        await record_burst(events, wait_seconds=0.1, watch=watch)
    assert (watch.start_marker_at, watch.end_marker_at) == (7.0, 9.0)


async def test_burst_starts_on_the_first_frame_when_the_start_marker_is_missing() -> None:
    from aiglasses.services.voice import VoiceWatch

    watch = VoiceWatch()
    events = stream(event("voice_frame", PACKET, 3.0), event("ai_voice_end", at=4.0))
    burst = await record_burst(events, wait_seconds=1, watch=watch)
    assert len(burst.packets) == 1 and watch.start_marker_at is None and watch.end_marker_at == 4.0
