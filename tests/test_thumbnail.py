from pathlib import Path

import pytest

from aiglasses.domain.errors import GlassesError
from aiglasses.protocol.bcframe import parse_bc_frame
from aiglasses.protocol.uuids import GLASSES_NOTIFY
from aiglasses.services.thumbnail import (
    APPROVED_FIRST_REQUEST,
    MAX_CHUNKS,
    ThumbnailError,
    jpeg_size,
    parse_chunk,
    request_frame,
)
from tests.helpers import build_bc_frame
from tests.test_facade import Device, glasses

JPEG = bytes.fromhex("ffd8ffc0000b080020004001011100") + b"\x00" * 20 + bytes([0xFF, 0xD9])


def chunk_reply(total: int, index: int, data: bytes) -> bytes:
    payload = bytes([1]) + total.to_bytes(2, "little") + index.to_bytes(2, "little") + data
    return build_bc_frame(0xFD, payload)


class ThumbnailDevice(Device):
    def __init__(self, parts: list[bytes], *, index_shift: int = 0, silent_after: int = 99) -> None:
        super().__init__()
        self.parts, self.index_shift, self.silent_after = parts, index_shift, silent_after

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        await super().write(char, data, response=response)
        if data[:2] == bytes([0xBC, 0xFD]):
            index = int.from_bytes(data[7:9], "little")
            if index < self.silent_after:
                reply = chunk_reply(len(self.parts), index + self.index_shift, self.parts[index])
                self._later(0.01, reply)


def test_the_first_request_is_exactly_the_approved_frame() -> None:
    assert request_frame(0).hex() == APPROVED_FIRST_REQUEST == "bcfd03002000010000"
    assert parse_bc_frame(request_frame(5)).payload == bytes([1, 5, 0])
    assert parse_bc_frame(request_frame(300)).payload == bytes([1, 44, 1])
    with pytest.raises(ThumbnailError):
        request_frame(MAX_CHUNKS)


def test_chunk_parsing_and_jpeg_size() -> None:
    chunk = parse_chunk(parse_bc_frame(chunk_reply(3, 1, b"abc")))
    assert (chunk.total, chunk.index, chunk.data) == (3, 1, b"abc")
    assert jpeg_size(JPEG) == (64, 32)


async def test_chunks_are_requested_one_by_one_and_joined(tmp_path: Path) -> None:
    device = ThumbnailDevice([JPEG[:12], JPEG[12:30], JPEG[30:]])
    result = await glasses(device, tmp_path).fetch_thumbnail()
    assert result.jpeg == JPEG and result.valid and result.chunks == 3
    sent = [w[1].hex() for w in device.writes]
    assert sent == [request_frame(i).hex() for i in range(3)]
    assert all(w[0] != GLASSES_NOTIFY for w in device.writes)


async def test_a_wrong_chunk_index_stops_the_transfer(tmp_path: Path) -> None:
    device = ThumbnailDevice([JPEG[:12], JPEG[12:]], index_shift=1)
    with pytest.raises(ThumbnailError, match="asked for chunk 0"):
        await glasses(device, tmp_path).fetch_thumbnail()
    assert len(device.writes) == 1


async def test_no_reply_stops_without_retrying(tmp_path: Path) -> None:
    device = ThumbnailDevice([JPEG], silent_after=0)
    with pytest.raises(ThumbnailError, match="no chunk 0"):
        await glasses(device, tmp_path).fetch_thumbnail()
    assert len(device.writes) == 1


async def test_dry_run_sends_nothing(tmp_path: Path) -> None:
    device = ThumbnailDevice([JPEG])
    with pytest.raises(GlassesError, match="dry run"):
        await glasses(device, tmp_path, dry_run=True).fetch_thumbnail()
    assert device.writes == []


async def test_a_chunk_split_over_several_notifications_is_reassembled(tmp_path: Path) -> None:
    class Splitting(ThumbnailDevice):
        def emit_split(self, frame: bytes) -> None:
            for start in range(0, len(frame), 7):
                self.emit(GLASSES_NOTIFY, frame[start : start + 7])

        def _later(self, delay: float, frame: bytes) -> None:
            import asyncio

            asyncio.get_running_loop().call_later(delay, self.emit_split, frame)

    device = Splitting([JPEG[:12], JPEG[12:]])
    result = await glasses(device, tmp_path).fetch_thumbnail()
    assert result.jpeg == JPEG and result.chunks == 2


async def test_a_stalled_partial_frame_is_dropped_and_does_not_swallow_the_next(
    tmp_path: Path,
) -> None:
    from aiglasses.services import channel as channel_module

    g = glasses(Device(), tmp_path)
    frame = chunk_reply(1, 0, b"abcdef")
    g.channel._on_data("u", frame[:8])
    channel_module.FRAGMENT_GAP_S, saved = -1.0, channel_module.FRAGMENT_GAP_S
    try:
        g.channel._on_data("u", frame)
    finally:
        channel_module.FRAGMENT_GAP_S = saved
    assert g.channel.stats["dropped partial frame"].count == 1
    assert "cmd 0xfd" in g.channel.stats
