"""AI-photo thumbnail over Bluetooth: the glasses send chunks of a small JPEG on command 0xFD.

The official app does this after an image-recognition capture (event type 2): it asks for chunk 0,
then for chunk index+1 until index+1 equals the total. Reply payload (INFERRED from the app code):
[marker][total u16 LE][index u16 LE][JPEG bytes]. The user approved the exact request frame for
index 0 (bcfd03002000010000) and the same frame with a higher index, nothing else. Each chunk is
requested once; there are no retries.
"""

import asyncio
import time
from dataclasses import dataclass, field

from aiglasses.domain.errors import GlassesError, GlassesTimeoutError, ProtocolError
from aiglasses.domain.models import Confidence
from aiglasses.protocol.bcframe import START_BYTE, BcFrame, crc16_modbus
from aiglasses.protocol.registry import Command
from aiglasses.protocol.uuids import GLASSES_WRITE
from aiglasses.safety import WriteGuard
from aiglasses.services.channel import FrameChannel

THUMBNAIL_CMD = 0xFD
APPROVED_FIRST_REQUEST = "bcfd03002000010000"
CHUNK_TIMEOUT_S = 5.0
MAX_CHUNKS = 512
JPEG_START = bytes([0xFF, 0xD8])
JPEG_END = bytes([0xFF, 0xD9])
SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


class ThumbnailError(GlassesError):
    """The thumbnail transfer failed or the glasses sent something unexpected."""


def request_frame(index: int) -> bytes:
    """The full BC frame asking for chunk `index` (payload 01, index u16 LE)."""
    if not 0 <= index < MAX_CHUNKS:
        raise ThumbnailError(f"chunk index {index} is outside 0..{MAX_CHUNKS - 1}")
    payload = bytes([1, index & 0xFF, index >> 8])
    crc = crc16_modbus(payload).to_bytes(2, "little")
    return bytes([START_BYTE, THUMBNAIL_CMD]) + len(payload).to_bytes(2, "little") + crc + payload


@dataclass(frozen=True)
class ThumbnailChunk:
    total: int
    index: int
    data: bytes


def parse_chunk(frame: BcFrame) -> ThumbnailChunk:
    payload = frame.payload
    if frame.cmd != THUMBNAIL_CMD or len(payload) < 5:
        raise ProtocolError(f"not a thumbnail chunk: cmd 0x{frame.cmd:02x}, {len(payload)} bytes")
    return ThumbnailChunk(
        total=int.from_bytes(payload[1:3], "little"),
        index=int.from_bytes(payload[3:5], "little"),
        data=payload[5:],
    )


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the first start-of-frame marker, or None when it cannot be found."""
    position = 2
    while position + 9 < len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        if marker in SOF_MARKERS:
            return (
                int.from_bytes(data[position + 7 : position + 9], "big"),
                int.from_bytes(data[position + 5 : position + 7], "big"),
            )
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            position += 1 if marker == 0xFF else 2
            continue
        position += 2 + int.from_bytes(data[position + 2 : position + 4], "big")
    return None


@dataclass
class ThumbnailResult:
    jpeg: bytes
    chunks: int
    seconds: float
    chunk_seconds: list[float] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.jpeg.startswith(JPEG_START) and self.jpeg.endswith(JPEG_END)


class ThumbnailService:
    def __init__(self, guard: WriteGuard, channel: FrameChannel) -> None:
        self._guard = guard
        self._channel = channel

    def _command(self, index: int) -> Command:
        return Command(
            name=f"thumbnail_chunk_{index}",
            characteristic=GLASSES_WRITE,
            payload=request_frame(index),
            direction="write",
            status=Confidence.INFERRED,
            source="apk-static (LargeDataHandler.getPictureThumbnails); user-approved frame",
            safe_to_send=False,
        )

    async def fetch(self, *, chunk_timeout: float = CHUNK_TIMEOUT_S) -> ThumbnailResult:
        """Read every chunk of the glasses' latest thumbnail, one request per chunk."""
        if self._guard.dry_run:
            raise GlassesError("dry run: connect with dry_run=False to send requests")
        if request_frame(0).hex() != APPROVED_FIRST_REQUEST:
            raise ThumbnailError("the first request frame differs from the approved one")
        started = time.perf_counter()
        pieces: list[bytes] = []
        timings: list[float] = []
        index, total = 0, 1
        while index < total:
            command = self._command(index)
            self._guard.approve(command.name, command.characteristic, command.payload)
            moment = time.perf_counter()
            try:
                reply = await self._channel.request(
                    self._guard, command, lambda f: f.cmd == THUMBNAIL_CMD, chunk_timeout
                )
            except GlassesTimeoutError as exc:
                raise ThumbnailError(f"no chunk {index} within {chunk_timeout:.0f} s") from exc
            timings.append(time.perf_counter() - moment)
            chunk = parse_chunk(reply)
            if chunk.total <= 0 or chunk.total > MAX_CHUNKS:
                raise ThumbnailError(f"implausible chunk total {chunk.total}")
            if chunk.index != index:
                raise ThumbnailError(f"asked for chunk {index}, got {chunk.index}")
            total = chunk.total
            pieces.append(chunk.data)
            index += 1
            await asyncio.sleep(0)
        return ThumbnailResult(
            b"".join(pieces), len(pieces), time.perf_counter() - started, timings
        )
