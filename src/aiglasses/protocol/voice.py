from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from aiglasses.protocol.bcframe import BcFrame

VOICE_CMD = 0x59
CONTROL_CMD = 0x73
# INFERRED from the rear-click sessions: 0x73 messages that bracket a 0x59 burst.
VOICE_START_PAYLOAD = b"\x03\x01"
VOICE_END_PAYLOAD = b"\x0a\x01"
MAX_GAP_SECONDS = 1.0


@dataclass(frozen=True)
class DecodedAudio:
    samples: npt.NDArray[np.int16]
    sample_rate: int


class VoiceDecoder(Protocol):
    """Turns the 40-byte voice packets of one burst into PCM audio."""

    name: str

    def decode(self, packets: Sequence[bytes]) -> DecodedAudio: ...


def voice_bursts(frames: Iterable[tuple[float, BcFrame]]) -> list[list[bytes]]:
    """Group 0x59 payloads into bursts using the start/end markers and time gaps (seconds)."""
    bursts: list[list[bytes]] = []
    current: list[bytes] | None = None
    last_ts = 0.0
    for ts, frame in frames:
        if frame.cmd == CONTROL_CMD and frame.payload in (VOICE_START_PAYLOAD, VOICE_END_PAYLOAD):
            current = None
        elif frame.cmd == VOICE_CMD:
            if current is None or ts - last_ts > MAX_GAP_SECONDS:
                current = []
                bursts.append(current)
            current.append(frame.payload)
            last_ts = ts
    return bursts
