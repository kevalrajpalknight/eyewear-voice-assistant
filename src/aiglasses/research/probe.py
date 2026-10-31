"""Send one approved read-only request and record the reply (others need new approval)."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aiglasses.domain.errors import GlassesError, ProtocolError
from aiglasses.protocol.bcframe import BcFrame, parse_bc_frame
from aiglasses.protocol.registry import Command
from aiglasses.safety import WriteGuard
from aiglasses.transport.base import BleTransport

NOTIFY_CHAR = "de5bf729-d711-4e47-af26-65e3012a5dc7"
REPLY_TIMEOUT_SECONDS = 5.0
GRACE_SECONDS = 1.0

# The user approved exactly these frames (2026-10-31). Anything else is refused.
APPROVED_FRAMES: dict[str, str] = {
    "battery_read": "bc42020001b00000",
    "device_info_read": "bc43020001b00000",
    "camera_status_read": "bc4a01007e8001",
}
EXPECTED_REPLY_CMD: dict[str, int] = {
    "battery_read": 0x42,
    "device_info_read": 0x43,
    "camera_status_read": 0x4A,
}


@dataclass
class Reply:
    time: str
    hex: str
    cmd: int | None
    decoded: str
    expected: bool


@dataclass
class ProbeResult:
    name: str
    tx_hex: str
    notifications_active: bool = False
    sent: bool = False
    replies: list[Reply] = field(default_factory=list)
    verdict: str = "not sent"

    @property
    def reply(self) -> Reply | None:
        return next((r for r in self.replies if r.expected), None)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def decode_reply(name: str, frame: BcFrame) -> tuple[bool, str]:
    """(sane, description) for a reply, following how the official app parses it (INFERRED)."""
    p = frame.payload
    if name == "battery_read":
        if len(p) < 2:
            return False, f"too short ({len(p)} bytes)"
        sane = 0 <= p[0] <= 100
        return sane, f"battery {p[0]}%, charging {p[1] == 1}"
    if name == "camera_status_read":
        if len(p) < 3:
            return False, f"too short ({len(p)} bytes)"
        return True, f"earphone camera open {p[1] == 1}, rotation {p[2] == 2} (raw {p.hex()})"
    if name == "device_info_read":
        if len(p) < 9:
            return False, f"too short ({len(p)} bytes)"
        lengths = [int.from_bytes(p[i : i + 2], "little") for i in (1, 3, 5, 7)]
        if 9 + sum(lengths) > len(p):
            return False, f"lengths {lengths} exceed payload of {len(p)} bytes"
        parts, pos = [], 9
        for length in lengths:
            parts.append(_text(p[pos : pos + length]))
            pos += length
        labels = ("firmware", "hardware", "wifi firmware", "wifi hardware")
        return True, ", ".join(f"{k}={v!r}" for k, v in zip(labels, parts, strict=True))
    return False, "unknown command"


def _audit(path: Path, result: ProbeResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "name": result.name,
        "tx": result.tx_hex,
        "notifications_active": result.notifications_active,
        "sent": result.sent,
        "replies": [r.__dict__ for r in result.replies],
        "verdict": result.verdict,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


async def run_probe(
    transport: BleTransport,
    guard: WriteGuard,
    command: Command,
    audit_path: Path,
) -> ProbeResult:
    """Enable notifications, write the approved frame ONCE, wait up to 5 s for the reply."""
    name = command.name
    if APPROVED_FRAMES.get(name) != command.payload.hex():
        raise ProtocolError(f"{name} is not an approved read-only probe; ask for approval first")
    result = ProbeResult(name=name, tx_hex=command.payload.hex())
    expected_cmd = EXPECTED_REPLY_CMD[name]
    got_reply = asyncio.Event()

    def on_notify(_uuid: str, data: bytes) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        try:
            frame = parse_bc_frame(data)
        except ProtocolError:
            result.replies.append(Reply(stamp, data.hex(), None, "not a BC frame", False))
            return
        expected = frame.cmd == expected_cmd
        sane, text = decode_reply(name, frame) if expected else (True, f"cmd 0x{frame.cmd:02x}")
        result.replies.append(Reply(stamp, data.hex(), frame.cmd, text, expected and sane))
        if expected:
            got_reply.set()

    try:
        await transport.subscribe(NOTIFY_CHAR, on_notify)
    except GlassesError as exc:
        result.verdict = f"notifications could not be enabled, nothing sent: {exc}"
        _audit(audit_path, result)
        return result
    result.notifications_active = True

    guard.approve(name, command.characteristic, command.payload)
    started = time.monotonic()
    await guard.write(command.characteristic, command.payload, response=False)
    result.sent = not guard.dry_run
    try:
        await asyncio.wait_for(got_reply.wait(), REPLY_TIMEOUT_SECONDS)
        await asyncio.sleep(GRACE_SECONDS)
        reply = result.reply
        result.verdict = (
            f"reply in {time.monotonic() - started:.2f}s: {reply.decoded}"
            if reply
            else "a reply arrived but its contents were not sane"
        )
    except asyncio.TimeoutError:
        result.verdict = f"no reply within {REPLY_TIMEOUT_SECONDS:.0f}s; stopped without retrying"
    _audit(audit_path, result)
    return result
