import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aiglasses import Glasses
from aiglasses.cli import main as cli
from aiglasses.domain.errors import PairingRequiredError, ProtocolError
from aiglasses.protocol.bcframe import parse_bc_frame
from aiglasses.protocol.registry import Command, load_commands
from aiglasses.research import probe
from aiglasses.research.probe import NOTIFY_CHAR, decode_reply, run_probe
from aiglasses.safety import WriteGuard
from aiglasses.transport.fakes import FakeBleTransport
from tests.helpers import build_bc_frame

WRITE_CHAR = "de5bf72a-d711-4e47-af26-65e3012a5dc7"


class Responding(FakeBleTransport):
    """Fake glasses that answers every write with a canned notification frame."""

    def __init__(self, reply: bytes | None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reply = reply

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        await super().write(char, data, response=response)
        if self.reply is not None:
            asyncio.get_running_loop().call_later(0.01, self.emit, NOTIFY_CHAR, self.reply)


def command(name: str) -> Command:
    return next(c for c in load_commands() if c.name == name)


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "REPLY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(probe, "GRACE_SECONDS", 0.0)


def make(reply: bytes | None, tmp_path: Path, **kwargs: Any) -> tuple[Responding, WriteGuard]:
    transport = Responding(reply, **kwargs)
    return transport, WriteGuard(transport, dry_run=False, audit_path=tmp_path / "write.jsonl")


async def test_battery_probe_sends_once_and_decodes(tmp_path: Path) -> None:
    transport, guard = make(build_bc_frame(0x42, bytes([87, 0])), tmp_path)
    result = await run_probe(transport, guard, command("battery_read"), tmp_path / "audit.jsonl")
    assert transport.writes == [(WRITE_CHAR, bytes.fromhex("bc42020001b00000"), False)]
    assert result.notifications_active and result.sent
    assert "battery 87%" in result.verdict
    audit = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert audit["tx"] == "bc42020001b00000" and audit["replies"][0]["expected"] is True
    sent = [json.loads(x)["outcome"] for x in (tmp_path / "write.jsonl").read_text().splitlines()]
    assert sent == ["sent"]


async def test_no_reply_is_reported_and_never_retried(tmp_path: Path) -> None:
    transport, guard = make(None, tmp_path)
    result = await run_probe(transport, guard, command("battery_read"), tmp_path / "audit.jsonl")
    assert len(transport.writes) == 1
    assert "no reply" in result.verdict and "without retrying" in result.verdict


async def test_insane_battery_value_is_flagged(tmp_path: Path) -> None:
    transport, guard = make(build_bc_frame(0x42, bytes([150, 0])), tmp_path)
    result = await run_probe(transport, guard, command("battery_read"), tmp_path / "audit.jsonl")
    assert "not sane" in result.verdict and result.reply is None


async def test_other_events_are_recorded_but_not_taken_as_the_reply(tmp_path: Path) -> None:
    transport, guard = make(build_bc_frame(0x73, bytes([0x0B, 0x2C])), tmp_path)
    result = await run_probe(transport, guard, command("battery_read"), tmp_path / "audit.jsonl")
    assert "no reply" in result.verdict
    assert result.replies and result.replies[0].cmd == 0x73 and not result.replies[0].expected


async def test_subscribe_failure_means_nothing_is_written(tmp_path: Path) -> None:
    transport, guard = make(
        None, tmp_path, subscribe_errors={NOTIFY_CHAR: PairingRequiredError("Access Denied")}
    )
    result = await run_probe(transport, guard, command("battery_read"), tmp_path / "audit.jsonl")
    assert transport.writes == [] and not result.notifications_active
    assert "nothing sent" in result.verdict


async def test_unapproved_payload_is_refused(tmp_path: Path) -> None:
    transport, guard = make(None, tmp_path)
    photo = command("photo")
    fake_battery = Command("battery_read", photo.characteristic, photo.payload, "write",
                           photo.status, None, False)  # fmt: skip
    with pytest.raises(ProtocolError, match="not an approved"):
        await run_probe(transport, guard, fake_battery, tmp_path / "audit.jsonl")
    assert transport.writes == []


def test_device_info_and_camera_status_decoding() -> None:
    strings = [b"9.40.00", b"V9.4", b"2512271030", b"wifi-hw"]
    header = b"\x00" + b"".join(len(s).to_bytes(2, "little") for s in strings)
    frame = parse_bc_frame(build_bc_frame(0x43, header + b"".join(strings)))
    sane, text = decode_reply("device_info_read", frame)
    assert sane and "wifi firmware='2512271030'" in text and "firmware='9.40.00'" in text
    truncated = parse_bc_frame(build_bc_frame(0x43, header + b"9.4"))
    assert not decode_reply("device_info_read", truncated)[0]
    camera = parse_bc_frame(build_bc_frame(0x4A, bytes([1, 1, 2])))
    assert decode_reply("camera_status_read", camera) == (
        True,
        "earphone camera open True, rotation True (raw 010102)",
    )


def test_cli_dry_run_refusal_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def no_connect(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not connect")

    monkeypatch.setattr(Glasses, "connect", staticmethod(no_connect))
    dry = runner.invoke(cli.app, ["probe", "battery_read"])
    assert dry.exit_code == 0 and "bc42020001b00000" in dry.output and "nothing sent" in dry.output
    refused = runner.invoke(cli.app, ["probe", "wifi_direct_on"])
    assert refused.exit_code == 2 and "not approved" in refused.output
    declined = runner.invoke(cli.app, ["probe", "battery_read", "--send"], input="n\n")
    assert declined.exit_code != 0
