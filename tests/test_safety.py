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
