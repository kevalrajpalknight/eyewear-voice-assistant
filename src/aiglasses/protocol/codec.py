from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Frame:
    payload: bytes


class FrameCodec(Protocol):
    """Swappable frame format. The real codec is chosen in Phase 3 from captured traffic."""

    name: str

    def encode(self, payload: bytes) -> bytes: ...

    def decode(self, data: bytes) -> Frame: ...


class RawCodec:
    """Pass-through codec used until a framing is proven by capture."""

    name = "raw"

    def encode(self, payload: bytes) -> bytes:
        return payload

    def decode(self, data: bytes) -> Frame:
        return Frame(payload=data)
