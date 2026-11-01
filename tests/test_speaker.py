import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator

import numpy as np
import pytest

from aiglasses.assistant.conversation.interfaces import AudioChunk
from aiglasses.assistant.speech.speaker import NullSink, SubprocessSpeakerSink


def silent_chunks(count: int, seconds: float = 0.1) -> AsyncIterator[AudioChunk]:
    async def gen() -> AsyncIterator[AudioChunk]:
        for _ in range(count):
            yield AudioChunk(np.zeros(int(24000 * seconds), dtype=np.int16), 24000)

    return gen()


async def test_null_sink_makes_no_sound_and_reports_playback_start() -> None:
    sink, started = NullSink(), []
    sink.on_playing = lambda: started.append(1)
    await sink.play(silent_chunks(3))
    assert started == [1] and sink.seconds_received == pytest.approx(0.3)


def has_output_device() -> bool:
    """Asked in a child process: sounddevice must never be imported into this one (ADR 0004)."""
    code = "import sounddevice as sd; sd.query_devices(kind='output')"
    return subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


@pytest.mark.skipif(not has_output_device(), reason="no audio output device")
async def test_worker_process_plays_silence_reports_start_and_end_and_stops() -> None:
    sink = SubprocessSpeakerSink()
    started: list[int] = []
    sink.on_playing = lambda: started.append(1)
    await sink.start()
    try:
        await asyncio.wait_for(sink.play(silent_chunks(3, 0.1)), 10)  # zeros: inaudible
        assert started == [1]
        long_play = asyncio.create_task(sink.play(silent_chunks(1, 5.0)))
        await asyncio.sleep(0.5)
        await sink.stop()
        await asyncio.wait_for(long_play, 5)  # returns promptly after stop
    finally:
        await sink.close()
