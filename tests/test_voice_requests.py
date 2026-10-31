import asyncio
from collections.abc import AsyncIterator, Sequence

import numpy as np
import pytest

from aiglasses.domain.models import GlassesEvent
from aiglasses.protocol.voice import DecodedAudio
from aiglasses.services.voice import EndReason, VoiceUtterance, voice_requests

PACKET = bytes(40)


class FakeDecoder:
    name = "fake"

    def decode(self, packets: Sequence[bytes]) -> DecodedAudio:
        return DecodedAudio(np.zeros(320 * len(packets), dtype=np.int16), 16000)


def ev(kind: str, at: float) -> GlassesEvent:
    return GlassesEvent(kind=kind, raw=PACKET if kind == "voice_frame" else b"", timestamp=at)


def frames(start: float, count: int) -> list[GlassesEvent]:
    return [ev("voice_frame", start + i * 0.02) for i in range(count)]


class Feed:
    """Scripted event source; stays open (never ends) like the real stream."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[GlassesEvent] = asyncio.Queue()

    def push(self, *events: GlassesEvent) -> None:
        for e in events:
            self.queue.put_nowait(e)

    async def events(self) -> AsyncIterator[GlassesEvent]:
        while True:
            yield await self.queue.get()


async def collect(
    feed: Feed, count: int, *, disconnected: asyncio.Event | None = None, idle: float = 0.2
) -> list[VoiceUtterance]:
    out: list[VoiceUtterance] = []

    async def run() -> None:
        async for u in voice_requests(
            feed.events(), FakeDecoder(), disconnected=disconnected, idle_seconds=idle
        ):
            out.append(u)
            if len(out) == count:
                break

    await asyncio.wait_for(run(), 3)
    return out


async def test_one_utterance_ended_by_the_marker() -> None:
    feed = Feed()
    feed.push(ev("ai_voice", 1.0), *frames(1.1, 288), ev("ai_voice_end", 6.9))
    (u,) = await collect(feed, 1)
    assert u.end_reason is EndReason.END_MARKER and u.frame_count == 288
    assert u.seconds == pytest.approx(5.76) and u.sample_rate == 16000
    assert (u.start_marker_at, u.started_at, u.end_marker_at) == (1.0, 1.1, 6.9)


async def test_frames_before_the_start_marker_belong_to_the_same_session() -> None:
    feed = Feed()
    feed.push(*frames(1.0, 3), ev("ai_voice", 1.07), *frames(1.1, 47), ev("ai_voice_end", 2.2))
    (u,) = await collect(feed, 1)
    assert u.frame_count == 50 and u.start_marker_at == 1.07 and u.started_at == 1.0


async def test_missing_start_marker_still_works() -> None:
    feed = Feed()
    feed.push(*frames(1.0, 20), ev("ai_voice_end", 1.5))
    (u,) = await collect(feed, 1)
    assert u.start_marker_at is None and u.frame_count == 20


async def test_timeout_when_frames_stop_without_an_end_marker() -> None:
    feed = Feed()
    feed.push(ev("ai_voice", 1.0), *frames(1.1, 30))
    (u,) = await collect(feed, 1, idle=0.1)
    assert u.end_reason is EndReason.TIMEOUT and u.end_marker_at is None
    assert u.ended_at == pytest.approx(1.1 + 29 * 0.02)


async def test_disconnect_mid_utterance_still_yields_what_arrived() -> None:
    feed, gone = Feed(), asyncio.Event()
    feed.push(ev("ai_voice", 1.0), *frames(1.1, 30))
    task = asyncio.create_task(collect(feed, 1, disconnected=gone, idle=10))
    await asyncio.sleep(0.05)
    gone.set()
    (u,) = await task
    assert u.end_reason is EndReason.DISCONNECT and u.frame_count == 30


async def test_two_clicks_give_two_utterances_and_a_second_marker_restarts() -> None:
    feed = Feed()
    feed.push(ev("ai_voice", 1.0), *frames(1.1, 20))  # no end marker
    feed.push(ev("ai_voice", 5.0), *frames(5.1, 20), ev("ai_voice_end", 5.6))
    first, second = await collect(feed, 2)
    assert first.end_reason is EndReason.RESTARTED and second.end_reason is EndReason.END_MARKER


async def test_stray_fragments_and_other_events_are_ignored() -> None:
    feed = Feed()
    feed.push(ev("battery", 0.5), *frames(1.0, 2), ev("ai_voice_end", 1.1))
    feed.push(ev("ai_voice", 2.0), *frames(2.1, 10), ev("ai_voice_end", 2.4))
    (u,) = await collect(feed, 1)
    assert u.started_at == 2.1 and u.frame_count == 10


async def test_no_task_is_left_behind_after_closing_the_generator() -> None:
    feed = Feed()
    feed.push(ev("ai_voice", 1.0), *frames(1.1, 10), ev("ai_voice_end", 1.4))
    before = len(asyncio.all_tasks())
    await collect(feed, 1)
    await asyncio.sleep(0.05)
    assert len(asyncio.all_tasks()) <= before


async def test_on_start_fires_once_per_session_at_the_marker_or_first_frame() -> None:
    calls: list[int] = []
    feed = Feed()
    feed.push(ev("ai_voice", 1.0), *frames(1.1, 10), ev("ai_voice_end", 1.4))
    feed.push(*frames(5.0, 10), ev("ai_voice_end", 5.3))  # no marker: first frame opens it
    out: list[VoiceUtterance] = []

    async def run() -> None:
        async for u in voice_requests(
            feed.events(), FakeDecoder(), idle_seconds=0.2, on_start=lambda: calls.append(1)
        ):
            out.append(u)
            if len(out) == 2:
                break

    await asyncio.wait_for(run(), 3)
    assert len(calls) == 2
