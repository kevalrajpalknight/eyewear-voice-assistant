from pathlib import Path

import pytest

from aiglasses.domain.errors import WriteBlockedError
from aiglasses.domain.models import Confidence
from aiglasses.protocol.registry import Command
from aiglasses.safety import WriteGuard
from aiglasses.transport.fakes import FakeBleTransport

CHAR = "0000ae01-0000-1000-8000-00805f9b34fb"


def _cmd(name: str, payload: bytes) -> Command:
    return Command(name, CHAR, payload, "write", Confidence.CONFIRMED, "cap-1", True)


async def test_unlisted_write_is_blocked() -> None:
    transport = FakeBleTransport()
    guard = WriteGuard(transport, dry_run=False)
    with pytest.raises(WriteBlockedError):
        await guard.write(CHAR, b"\x01")
    assert transport.writes == []


async def test_allowlisted_write_is_sent(tmp_path: Path) -> None:
    transport = FakeBleTransport()
    audit = tmp_path / "audit.jsonl"
    guard = WriteGuard(transport, [_cmd("get_battery", b"\x01")], dry_run=False, audit_path=audit)
    await guard.write(CHAR.upper(), b"\x01")
    assert transport.writes == [(CHAR, b"\x01", False)]
    assert '"outcome": "sent"' in audit.read_text()


async def test_dry_run_does_not_send() -> None:
    transport = FakeBleTransport()
    guard = WriteGuard(transport, [_cmd("get_battery", b"\x01")])
    await guard.write(CHAR, b"\x01")
    assert transport.writes == []


async def test_payload_must_match_exactly() -> None:
    guard = WriteGuard(FakeBleTransport(), [_cmd("get_battery", b"\x01")], dry_run=False)
    with pytest.raises(WriteBlockedError):
        await guard.write(CHAR, b"\x01\x00")


@pytest.mark.parametrize("name", ["ota_start", "enter_upgrade_mode", "dfu", "flash_block"])
async def test_ota_names_blocked_even_when_approved(name: str) -> None:
    transport = FakeBleTransport()
    guard = WriteGuard(transport, dry_run=False)
    guard.approve(name, CHAR, b"\x02")
    with pytest.raises(WriteBlockedError):
        await guard.write(CHAR, b"\x02")
    assert transport.writes == []


async def test_session_approval_allows_write() -> None:
    transport = FakeBleTransport()
    guard = WriteGuard(transport, dry_run=False)
    guard.approve("take_photo", CHAR, b"\x05")
    await guard.write(CHAR, b"\x05")
    assert len(transport.writes) == 1


@pytest.mark.parametrize(
    ("cmd", "payload", "why"),
    [
        (0x01, b"", "firmware update"),
        (0x02, bytes(9), "firmware update"),
        (0x03, bytes(10), "firmware update"),
        (0x05, b"", "firmware update"),
        (0x31, b"\x01", "file transfer"),
        (0x81, b"\x02\x00", "file transfer"),
        (0xF2, b"\x02\x03\x00", "factory test"),
        (0xFC, b"192.168.0.2", "OTA"),
        (0x41, b"\x02\x01\x0a", "factory reset"),
        (0x41, b"\x02\x01\x0e", "restart"),
        (0x39, bytes([1]) + b"photo.jpg", "delete a file"),
        (0x41, bytes([2, 1, 9]), "import session"),
    ],
)
async def test_byte_level_denylist_beats_approval(cmd: int, payload: bytes, why: str) -> None:
    from tests.helpers import build_bc_frame

    frame = build_bc_frame(cmd, payload)
    transport = FakeBleTransport()
    guard = WriteGuard(transport, dry_run=False)
    guard.approve("innocent_name", CHAR, frame)
    with pytest.raises(WriteBlockedError, match=why):
        await guard.write(CHAR, frame)
    assert transport.writes == []


async def test_denylist_ignores_crc_and_allows_benign_frames() -> None:
    from aiglasses.protocol.denylist import denied_reason
    from tests.helpers import build_bc_frame

    assert denied_reason(bytes([0xBC, 0x05, 0, 0, 0xFF, 0xFF])) is not None  # bad CRC still blocked
    assert denied_reason(build_bc_frame(0x42, bytes(2))) is None  # battery read
    assert denied_reason(build_bc_frame(0x41, b"\x02\x01\x01")) is None  # take photo
    assert denied_reason(b"\x01\x02\x03") is None  # not a BC frame
