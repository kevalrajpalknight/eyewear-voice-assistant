import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aiglasses.cli import main as cli
from aiglasses.domain.errors import WriteBlockedError
from aiglasses.domain.models import Confidence
from aiglasses.protocol.registry import Command, load_commands
from aiglasses.research import probe
from aiglasses.research.probe import NOTIFY_CHAR, run_probe
from aiglasses.safety import WriteGuard
from aiglasses.timefmt import both, stamps
from aiglasses.transport.bleak_transport import BleakTransport
from aiglasses.transport.fakes import FakeBleTransport
from tests.helpers import build_bc_frame

CHAR = "de5bf72a-d711-4e47-af26-65e3012a5dc7"


def command(name: str) -> Command:
    return next(c for c in load_commands() if c.name == name)


class Scripted(FakeBleTransport):
    """Fake glasses: optional count event after subscribe, canned replies after the write."""

    def __init__(
        self, replies: list[tuple[float, bytes]], baseline: bytes | None = None, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.replies = replies
        self.baseline = baseline

    async def subscribe(self, char: str, handler: Any) -> None:
        await super().subscribe(char, handler)
        if self.baseline is not None:
            asyncio.get_running_loop().call_later(0.01, self.emit, NOTIFY_CHAR, self.baseline)

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        await super().write(char, data, response=response)
        for delay, frame in self.replies:
            asyncio.get_running_loop().call_later(delay, self.emit, NOTIFY_CHAR, frame)


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "REPLY_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(probe, "GRACE_SECONDS", 0.25)
    monkeypatch.setattr(probe, "STATE_CHANGE_OBSERVE_SECONDS", 0.3)
    monkeypatch.setattr(probe, "BASELINE_WAIT_SECONDS", 0.3)


def guard_for(transport: FakeBleTransport, tmp_path: Path) -> WriteGuard:
    return WriteGuard(transport, dry_run=False, audit_path=tmp_path / "write.jsonl")


async def test_probe_aborts_without_writing_when_the_link_is_down(tmp_path: Path) -> None:
    transport = Scripted([], connected=False)
    result = await run_probe(
        transport, guard_for(transport, tmp_path), command("battery_read"), tmp_path / "a.jsonl"
    )
    assert transport.writes == [] and not result.link_up_before_write and not result.sent
    assert "not up" in result.verdict


async def test_probe_aborts_when_a_drop_was_signalled_after_subscribing(tmp_path: Path) -> None:
    transport = Scripted([])
    await transport.connect()
    transport.disconnected.set()  # what the transport does on a real drop
    result = await run_probe(
        transport, guard_for(transport, tmp_path), command("battery_read"), tmp_path / "a.jsonl"
    )
    assert transport.writes == [] and "not up" in result.verdict


async def test_latency_is_measured_at_the_reply_not_after_the_grace_period(tmp_path: Path) -> None:
    reply = build_bc_frame(0x42, bytes([100, 0]))
    transport = Scripted([(0.02, reply)])
    result = await run_probe(
        transport, guard_for(transport, tmp_path), command("battery_read"), tmp_path / "a.jsonl"
    )
    assert result.latency_seconds is not None and result.latency_seconds < 0.2
    assert "reply in 0.0" in result.verdict
    audit = (tmp_path / "a.jsonl").read_text()
    assert '"local"' in audit and '"+00:00"' in audit.replace("+00:00", '"+00:00"')


async def test_photo_probe_compares_counts_before_and_after(tmp_path: Path) -> None:
    before = build_bc_frame(0x73, bytes([1, 4, 0, 0, 0, 1, 0, 1]))
    after = build_bc_frame(0x73, bytes([1, 5, 0, 0, 0, 1, 0, 1]))
    control_reply = build_bc_frame(0x41, bytes([2, 1, 1, 0, 0]))
    transport = Scripted([(0.02, control_reply), (0.1, after)], baseline=before)
    result = await run_probe(
        transport, guard_for(transport, tmp_path), photo(), tmp_path / "a.jsonl"
    )
    assert len(transport.writes) == 1
    assert transport.writes[0][1] == bytes.fromhex("bc4103001050020101")
    assert "function 1, error code 0 (accepted)" in result.verdict
    assert "photo count 4 -> 5" in result.verdict


def photo() -> Command:
    return Command(
        "photo",
        CHAR,
        bytes.fromhex("bc4103001050020101"),
        "write",
        Confidence.INFERRED,
        None,
        False,
    )


async def test_guard_never_sends_the_same_payload_twice(tmp_path: Path) -> None:
    transport = Scripted([])
    guard = guard_for(transport, tmp_path)
    guard.approve("battery_read", CHAR, bytes.fromhex("bc42020001b00000"))
    await guard.write(CHAR, bytes.fromhex("bc42020001b00000"))
    with pytest.raises(WriteBlockedError, match="never repeated"):
        await guard.write(CHAR, bytes.fromhex("bc42020001b00000"))
    assert len(transport.writes) == 1


async def test_guard_refuses_when_the_link_is_down(tmp_path: Path) -> None:
    transport = Scripted([], connected=False)
    guard = guard_for(transport, tmp_path)
    guard.approve("battery_read", CHAR, bytes.fromhex("bc42020001b00000"))
    with pytest.raises(WriteBlockedError, match="link is not up"):
        await guard.write(CHAR, bytes.fromhex("bc42020001b00000"))
    assert transport.writes == []


def test_requested_disconnect_is_not_reported_as_a_lost_link(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = BleakTransport(address=None, name_prefix="AIMB-G2_")
    with caplog.at_level(logging.INFO, logger="aiglasses.bleak"):
        transport._closing = True
        transport._on_disconnect(None)  # type: ignore[arg-type]
        transport._closing = False
        transport._on_disconnect(None)  # type: ignore[arg-type]
    levels = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert levels[0] == ("INFO", "disconnected (we asked for it)")
    assert levels[1][0] == "WARNING" and "link lost" in levels[1][1]
    assert transport.disconnected.is_set()


def test_timestamps_always_carry_an_explicit_zone() -> None:
    s = stamps(datetime(2026, 10, 31, 6, 40, 14, 232000, tzinfo=timezone.utc))
    assert s["ts"] == "2026-10-31T06:40:14.232+00:00"
    assert re.search(r"[+-]\d\d:\d\d$", s["local"])
    text = both(datetime(2026, 10, 31, 6, 40, 14, 232000, tzinfo=timezone.utc))
    assert text.startswith("06:40:14.232Z / ") and re.search(r"[+-]\d\d:\d\d$", text)


def test_photo_cli_asks_a_different_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["probe", "photo", "--send"], input="n\n")
    assert result.exit_code != 0 and "CHANGES" in result.output


def test_not_found_hint_tells_the_user_what_to_do() -> None:
    from aiglasses.transport.bleak_transport import NOT_FOUND_HINT

    for needle in ("wake", "Bluetooth is off", "app is closed", "not scanning further"):
        assert needle in NOT_FOUND_HINT


async def test_photo_probe_uses_a_known_count_when_no_connect_frame_arrives(tmp_path: Path) -> None:
    after = build_bc_frame(0x73, bytes([1, 5, 0, 0, 0, 1, 0, 1]))
    control_reply = build_bc_frame(0x41, bytes([2, 1, 1, 0, 0]))
    transport = Scripted([(0.02, control_reply), (0.1, after)])
    result = await run_probe(
        transport,
        guard_for(transport, tmp_path),
        photo(),
        tmp_path / "a.jsonl",
        known_photo_count=4,
    )
    assert "photo count 4 -> 5" in result.verdict and "earlier frame" in result.verdict


async def test_baseline_frame_is_never_mistaken_for_the_result(tmp_path: Path) -> None:
    before = build_bc_frame(0x73, bytes([1, 4, 0, 0, 0, 1, 0, 1]))
    control_reply = build_bc_frame(0x41, bytes([2, 1, 1, 0, 0]))
    transport = Scripted([(0.02, control_reply)], baseline=before)  # no count event after the write
    result = await run_probe(
        transport, guard_for(transport, tmp_path), photo(), tmp_path / "a.jsonl"
    )
    assert "no media-count event arrived" in result.verdict and "4 -> " not in result.verdict
    assert "connect-time frame" in result.verdict


async def test_other_count_than_expected_is_reported_as_is(tmp_path: Path) -> None:
    after = build_bc_frame(0x73, bytes([1, 6, 0, 0, 0, 1, 0, 1]))
    control_reply = build_bc_frame(0x41, bytes([2, 1, 1, 0, 0]))
    transport = Scripted([(0.02, control_reply), (0.1, after)])
    result = await run_probe(
        transport,
        guard_for(transport, tmp_path),
        photo(),
        tmp_path / "a.jsonl",
        known_photo_count=4,
    )
    assert "photo count 4 -> 6" in result.verdict
