import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aiglasses import Glasses
from aiglasses.cli import main as cli
from aiglasses.config import Settings
from aiglasses.domain.errors import (
    ConfirmationRequired,
    GlassesError,
    GlassesTimeoutError,
    WriteBlockedError,
)
from aiglasses.domain.models import MediaCounts
from aiglasses.protocol.bcframe import parse_bc_frame
from aiglasses.protocol.events import decode_event
from aiglasses.protocol.uuids import GLASSES_NOTIFY, GLASSES_WRITE
from aiglasses.transport.fakes import FakeBleTransport
from tests.helpers import build_bc_frame

FIXTURE = Path(__file__).parent / "fixtures" / "recorded_frames.jsonl"
RECORDED = [json.loads(line) for line in FIXTURE.read_text().splitlines()]

BATTERY_REQ = bytes.fromhex("bc42020001b00000")
INFO_REQ = bytes.fromhex("bc43020001b00000")
COUNTS_REQ = bytes.fromhex("bc41020001130204")
PHOTO_REQ = bytes.fromhex("bc4103001050020101")
BATTERY_REPLY = bytes.fromhex("bc4202002b706400")
CONTROL_REPLY = bytes.fromhex("bc4105000c4c020101ffff")


def recorded(note: str, index: int = 0) -> bytes:
    return bytes.fromhex([r for r in RECORDED if r["note"] == note][index]["hex"])


def counts_reply(photos: int, videos: int, audio: int) -> bytes:
    payload = bytes([2, 4]) + b"".join(n.to_bytes(2, "little") for n in (photos, videos, audio))
    return build_bc_frame(0x41, payload + bytes([1, 0, 0]))


def counts_event(photos: int, videos: int, audio: int) -> bytes:
    body = bytes([1]) + b"".join(n.to_bytes(2, "little") for n in (photos, videos, audio))
    return build_bc_frame(0x73, body + bytes([1]))


class Device(FakeBleTransport):
    """Fake glasses: answers the confirmed requests the way the real unit did."""

    def __init__(self, *, photos: int = 6, answer_counts: bool = True) -> None:
        super().__init__(reads={})
        self.photos = photos
        self.answer_counts = answer_counts

    def _later(self, delay: float, frame: bytes) -> None:
        asyncio.get_running_loop().call_later(delay, self.emit, GLASSES_NOTIFY, frame)

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        await super().write(char, data, response=response)
        if data == BATTERY_REQ:
            self._later(0.01, BATTERY_REPLY)
        elif data == INFO_REQ:
            self._later(0.01, recorded("reply 0x43"))
        elif data == COUNTS_REQ and self.answer_counts:
            self._later(0.01, counts_reply(self.photos, 0, 2))
        elif data == PHOTO_REQ:
            self.photos += 1
            self._later(0.01, CONTROL_REPLY)
            self._later(0.05, counts_event(self.photos, 0, 2))


def glasses(device: Device, tmp_path: Path, *, dry_run: bool = False) -> Glasses:
    g = Glasses(device, Settings(log_dir=tmp_path), dry_run=dry_run)
    g.channel.request_timeout = 0.2
    return g


async def test_reads_return_typed_values(tmp_path: Path) -> None:
    g = glasses(Device(), tmp_path)
    battery = await g.read_battery()
    assert (battery.percent, battery.charging) == (100, False)
    info = await g.read_device_info()
    assert info.firmware == "AM01G1_9.40.00_260114" and info.hardware == "AM01G1_V9.4"
    assert await g.read_media_counts() == MediaCounts(6, 0, 2, 1)


async def test_reads_can_repeat_but_not_the_photo(tmp_path: Path) -> None:
    device = Device()
    g = glasses(device, tmp_path)
    await g.read_battery()
    await g.read_battery()
    assert [w[1] for w in device.writes] == [BATTERY_REQ, BATTERY_REQ]


async def test_default_dry_run_sends_nothing(tmp_path: Path) -> None:
    device = Device()
    g = glasses(device, tmp_path, dry_run=True)
    with pytest.raises(GlassesError, match="dry run"):
        await g.read_battery()
    assert device.writes == []


async def test_take_photo_needs_explicit_confirmation_and_sends_nothing_without_it(
    tmp_path: Path,
) -> None:
    device = Device()
    g = glasses(device, tmp_path)
    with pytest.raises(ConfirmationRequired):
        await g.take_photo(confirm=False)
    assert device.writes == []


async def test_take_photo_reads_counts_first_then_writes_once(tmp_path: Path) -> None:
    device = Device(photos=6)
    g = glasses(device, tmp_path)
    result = await g.take_photo(confirm=True)
    assert [w[1] for w in device.writes] == [COUNTS_REQ, PHOTO_REQ]
    assert result.before.photos == 6 and result.after is not None and result.after.photos == 7
    assert result.photos_added == 1
    assert "function 1, error code 255" in result.reply and result.reply_hex == "020101ffff"
    with pytest.raises(WriteBlockedError, match="never repeated"):
        await g.take_photo(confirm=True)
    assert [w[1] for w in device.writes].count(PHOTO_REQ) == 1


async def test_take_photo_aborts_before_the_photo_if_the_count_read_fails(tmp_path: Path) -> None:
    device = Device(answer_counts=False)
    g = glasses(device, tmp_path)
    with pytest.raises(GlassesTimeoutError):
        await g.take_photo(confirm=True)
    assert [w[1] for w in device.writes] == [COUNTS_REQ]  # the photo was never written


async def test_missing_count_event_is_reported_as_unknown(tmp_path: Path) -> None:
    class NoEvent(Device):
        async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
            await FakeBleTransport.write(self, char, data, response=response)
            if data == COUNTS_REQ:
                self._later(0.01, counts_reply(6, 0, 2))
            elif data == PHOTO_REQ:
                self._later(0.01, CONTROL_REPLY)

    device = NoEvent()
    g = glasses(device, tmp_path)
    result = await g.control.take_photo(confirm=True, observe_seconds=0.1)
    assert result.after is None and result.photos_added is None


async def test_nothing_else_is_sendable(tmp_path: Path) -> None:
    device = Device()
    g = glasses(device, tmp_path)
    for blocked in ("bc410400935c02010401", "bc4103001196020109"):
        with pytest.raises(WriteBlockedError):
            await g.guard.write(GLASSES_WRITE, bytes.fromhex(blocked))
    with pytest.raises(WriteBlockedError):  # the photo is not allowlisted without confirm
        await g.guard.write(GLASSES_WRITE, PHOTO_REQ)
    assert device.writes == []


async def test_events_are_typed_and_include_the_voice_stream(tmp_path: Path) -> None:
    device = Device()
    g = glasses(device, tmp_path)
    frames = [bytes.fromhex(r["hex"]) for r in RECORDED if not r["note"].startswith("reply")]

    async def collect() -> list[Any]:
        out = []
        async for event in g.events():
            out.append(event)
            if len(out) == len(frames):
                break
        return out

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)
    for frame in frames:
        device.emit(GLASSES_NOTIFY, frame)
    events = await asyncio.wait_for(task, 2)
    kinds = [e.kind for e in events]
    assert kinds == [r["note"] for r in RECORDED if not r["note"].startswith("reply")]
    by_kind = {e.kind: e for e in events}
    assert by_kind["battery"].fields == {"percent": 99, "charging": False}
    assert by_kind["temperature"].fields == {"celsius": 42}
    assert by_kind["media_counts"].fields["photos"] == 5
    assert len(by_kind["voice_frame"].raw) == 40
    assert by_kind["ai_voice"].fields == {"on": True}


def test_recorded_frames_decode_to_their_noted_kind() -> None:
    for row in RECORDED:
        frame = parse_bc_frame(bytes.fromhex(row["hex"]))  # CRC verified
        if frame.cmd == 0x73:
            assert decode_event(frame.payload).kind == row["note"]
        elif frame.cmd == 0x59:
            assert len(frame.payload) == 40


def test_photo_cli_asks_first_and_never_answers_for_the_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    connected: list[bool] = []
    device = Device(photos=6)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_connect(config: Any, **kwargs: Any) -> Any:
        connected.append(True)
        yield glasses(device, tmp_path)

    monkeypatch.setattr(Glasses, "connect", staticmethod(fake_connect))
    runner = CliRunner()
    declined = runner.invoke(cli.app, ["photo"], input="n\n")
    assert declined.exit_code != 0 and connected == [] and device.writes == []
    accepted = runner.invoke(cli.app, ["photo"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert "before: photos 6" in accepted.output and "photos added: 1" in accepted.output


async def test_channel_counts_every_frame_type_with_first_and_last_time(tmp_path: Path) -> None:
    device = Device()
    g = glasses(device, tmp_path)
    await g.channel.start()
    assert g.channel.active
    device.emit(GLASSES_NOTIFY, recorded("temperature"))
    device.emit(GLASSES_NOTIFY, recorded("temperature"))
    device.emit(GLASSES_NOTIFY, BATTERY_REPLY)
    device.emit(GLASSES_NOTIFY, b"junk")
    stats = g.channel.stats
    assert (
        stats["temperature"].count == 2 and stats["temperature"].first <= stats["temperature"].last
    )
    assert stats["cmd 0x42"].count == 1 and stats["invalid frame"].count == 1
