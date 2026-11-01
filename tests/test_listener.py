from collections.abc import AsyncIterator

import numpy as np

from aiglasses.assistant.listener import EnergyVad, MicListener, Pcm

RATE = 16000
FRAME = RATE // 50  # 20 ms
QUIET = np.zeros(FRAME, dtype=np.int16)
LOUD = (np.sin(np.arange(FRAME) / 3.0) * 6000).astype(np.int16)


def vad() -> EnergyVad:
    return EnergyVad(min_rms=300, ratio=3.0, end_silence_s=0.4)


def run(vad_: EnergyVad, frames: list[Pcm], factor: float = 1.0) -> list[str]:
    seen: list[str] = []
    for index, frame in enumerate(frames):
        for event in vad_.feed(frame, index * 0.02, factor=factor):
            seen.append(event.kind if event.pcm is not None or event.kind == "start" else "blip")
    return seen


def test_silence_makes_no_events_and_speech_gives_start_then_end() -> None:
    assert run(vad(), [QUIET] * 100) == []
    assert run(vad(), [QUIET] * 10 + [LOUD] * 25 + [QUIET] * 30) == ["start", "end"]


def test_a_short_blip_is_dropped() -> None:
    assert run(vad(), [QUIET] * 10 + [LOUD] * 4 + [QUIET] * 30) == ["start", "blip"]


def test_the_speaking_assistant_raises_the_bar() -> None:
    echo = (LOUD / 4).astype(np.int16)  # about 1500 RMS: speech for the plain bar, not for 2.5x
    assert run(vad(), [echo] * 30 + [QUIET] * 30, factor=1.0) != []
    assert run(vad(), [echo] * 30 + [QUIET] * 30, factor=6.0) == []


async def frames_of(frames: list[Pcm]) -> AsyncIterator[Pcm]:
    for frame in frames:
        yield frame


class Rig:
    def __init__(
        self, frames: list[Pcm], *, busy: list[bool] | None = None, duplex: bool = True
    ) -> None:
        self.now = 0.0
        self.require: list[bool] = []
        self.messages: list[str] = []
        self.interrupts = 0
        self.busy_flags = busy or []
        self.index = 0

        def tick() -> float:
            self.now += 0.02
            return self.now

        def busy_now() -> bool:
            flag = self.busy_flags[self.index] if self.index < len(self.busy_flags) else False
            self.index += 1
            return flag

        self.listener = MicListener(
            frames_of(frames), vad(), busy=busy_now,
            interrupt=lambda: setattr(self, "interrupts", self.interrupts + 1),
            set_require_wake=self.require.append, report=self.messages.append,
            idle_sleep_s=1.0, duplex=duplex, barge_in_ratio=2.5, clock=tick,
        )  # fmt: skip


async def collect(rig: Rig) -> int:
    return len([u async for u in rig.listener.utterances()])


async def test_it_starts_asleep_and_each_request_is_yielded() -> None:
    rig = Rig([QUIET] * 10 + [LOUD] * 25 + [QUIET] * 30)
    assert await collect(rig) == 1 and rig.require == [True] and not rig.listener.awake


async def test_waking_stays_awake_then_sleeps_after_the_idle_time() -> None:
    rig = Rig([QUIET] * 120)
    rig.listener.wake()
    assert rig.listener.awake and rig.require == [True, False]
    await collect(rig)
    assert not rig.listener.awake and rig.require == [True, False, True]
    assert rig.messages[0] == "awake" and rig.messages[-1].startswith("sleeping")


async def test_busy_time_does_not_count_as_idle() -> None:
    rig = Rig([QUIET] * 120, busy=[True] * 120)
    rig.listener.wake()
    await collect(rig)
    assert rig.listener.awake


async def test_speaking_over_the_assistant_interrupts_and_half_duplex_does_not() -> None:
    frames = [QUIET] * 5 + [LOUD] * 30 + [QUIET] * 30
    duplex = Rig(frames, busy=[True] * 35 + [False] * 30)
    await collect(duplex)
    assert duplex.interrupts == 1 and "barge-in: stopping" in duplex.messages
    half = Rig(frames, busy=[True] * 35 + [False] * 30, duplex=False)
    await collect(half)
    assert half.interrupts == 0
