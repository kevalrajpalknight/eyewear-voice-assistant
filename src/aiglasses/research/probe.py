"""Send one approved request and record the reply (anything else needs a new approval)."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiglasses.domain.errors import GlassesError, ProtocolError
from aiglasses.protocol.bcframe import BcFrame, parse_bc_frame
from aiglasses.protocol.events import EVENT_CMD, decode_event
from aiglasses.protocol.registry import Command
from aiglasses.safety import WriteGuard
from aiglasses.timefmt import stamps
from aiglasses.transport.base import BleTransport

NOTIFY_CHAR = "de5bf729-d711-4e47-af26-65e3012a5dc7"
REPLY_TIMEOUT_SECONDS = 5.0
GRACE_SECONDS = 1.0
STATE_CHANGE_OBSERVE_SECONDS = 15.0  # watch for the follow-up event after a state-changing write
BASELINE_WAIT_SECONDS = 1.5  # the glasses send media counts soon after connecting

# The user approved exactly these frames. Anything else is refused.
APPROVED_FRAMES: dict[str, str] = {
    "battery_read": "bc42020001b00000",
    "device_info_read": "bc43020001b00000",
    "photo": "bc4103001050020101",
}
EXPECTED_REPLY_CMD: dict[str, int] = {
    "battery_read": 0x42,
    "device_info_read": 0x43,
    "photo": 0x41,
}
STATE_CHANGING = {"photo"}


@dataclass
class Reply:
    time: str
    local: str
    hex: str
    cmd: int | None
    decoded: str
    expected: bool


@dataclass
class ProbeResult:
    name: str
    tx_hex: str
    notifications_active: bool = False
    link_up_before_write: bool = False
    sent: bool = False
    replies: list[Reply] = field(default_factory=list)
    latency_seconds: float | None = None
    baseline_source: str = "none"
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
    if name == "photo":
        if len(p) < 4 or p[1] != 1:
            return False, f"unexpected control reply (raw {p.hex()})"
        meaning = {0: "accepted", 255: "busy"}.get(p[3], "unknown")
        busy = f", busy with activity {p[4]}" if len(p) > 4 and p[3] == 255 else ""
        return p[3] in (0, 255), f"function {p[2]}, error code {p[3]} ({meaning}){busy}"
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
        **stamps(),
        "name": result.name,
        "tx": result.tx_hex,
        "notifications_active": result.notifications_active,
        "link_up_before_write": result.link_up_before_write,
        "sent": result.sent,
        "latency_seconds": result.latency_seconds,
        "baseline_source": result.baseline_source,
        "replies": [r.__dict__ for r in result.replies],
        "verdict": result.verdict,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _counts(result: ProbeResult, start: int = 0) -> dict[str, int] | None:
    """The most recent media-count event seen at or after index `start`: {photos, videos, audio}."""
    for r in reversed(result.replies[start:]):
        if r.cmd == EVENT_CMD:
            try:
                event = decode_event(parse_bc_frame(bytes.fromhex(r.hex)).payload)
            except ProtocolError:
                continue
            if event.kind == "media_counts":
                return {k: int(event.fields[k]) for k in ("photos", "videos", "audio")}
    return None


async def run_probe(
    transport: BleTransport,
    guard: WriteGuard,
    command: Command,
    audit_path: Path,
    known_photo_count: int | None = None,
) -> ProbeResult:
    """Enable notifications, check the link, write the approved frame ONCE, wait up to 5 s."""
    name = command.name
    if APPROVED_FRAMES.get(name) != command.payload.hex():
        raise ProtocolError(f"{name} is not an approved probe; ask for approval first")
    result = ProbeResult(name=name, tx_hex=command.payload.hex())
    expected_cmd = EXPECTED_REPLY_CMD[name]
    got_reply = asyncio.Event()
    started = 0.0

    def on_notify(_uuid: str, data: bytes) -> None:
        stamp = stamps()
        try:
            frame = parse_bc_frame(data)
        except ProtocolError:
            result.replies.append(
                Reply(stamp["ts"], stamp["local"], data.hex(), None, "not a BC frame", False)
            )
            return
        expected = frame.cmd == expected_cmd
        if expected:
            sane, text = decode_reply(name, frame)
        elif frame.cmd == EVENT_CMD:
            sane, text = True, decode_event(frame.payload).text
        else:
            sane, text = True, f"cmd 0x{frame.cmd:02x}"
        result.replies.append(
            Reply(stamp["ts"], stamp["local"], data.hex(), frame.cmd, text, expected and sane)
        )
        if expected and result.latency_seconds is None and started:
            result.latency_seconds = round(time.monotonic() - started, 3)
        if expected:
            got_reply.set()

    try:
        await transport.subscribe(NOTIFY_CHAR, on_notify)
    except GlassesError as exc:
        result.verdict = f"notifications could not be enabled, nothing sent: {exc}"
        _audit(audit_path, result)
        return result
    result.notifications_active = True

    if name in STATE_CHANGING:
        waited = 0.0
        while _counts(result) is None and waited < BASELINE_WAIT_SECONDS:
            await asyncio.sleep(0.1)
            waited += 0.1
    baseline = _counts(result)
    if baseline is None and known_photo_count is not None:
        baseline = {"photos": known_photo_count, "videos": -1, "audio": -1}  # from an earlier frame
        result.baseline_source = "earlier frame (no connect-time frame arrived)"
    elif baseline is not None:
        result.baseline_source = "connect-time frame"
    result.link_up_before_write = transport.is_connected and not transport.disconnected.is_set()
    if not result.link_up_before_write:
        result.verdict = "the link is not up right before the write; nothing sent"
        _audit(audit_path, result)
        return result

    guard.approve(name, command.characteristic, command.payload)
    write_index = len(result.replies)  # only events after this point count as the effect
    started = time.monotonic()
    await guard.write(command.characteristic, command.payload, response=False)
    result.sent = not guard.dry_run
    try:
        await asyncio.wait_for(got_reply.wait(), REPLY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        result.verdict = f"no reply within {REPLY_TIMEOUT_SECONDS:.0f}s; stopped without retrying"
        _audit(audit_path, result)
        return result
    observe = STATE_CHANGE_OBSERVE_SECONDS if name in STATE_CHANGING else GRACE_SECONDS
    await asyncio.sleep(observe)
    reply = result.reply
    if reply is None:
        result.verdict = "a reply arrived but its contents were not sane"
    else:
        result.verdict = f"reply in {result.latency_seconds:.2f}s: {reply.decoded}"
        after = _counts(result, write_index)
        if name == "photo":
            before = baseline["photos"] if baseline else None
            shown_before = before if before is not None else "?"
            if after is None:
                result.verdict += (
                    f"; no media-count event arrived within {observe:.0f}s "
                    f"(count before: {shown_before}, from {result.baseline_source})"
                )
            else:
                result.verdict += (
                    f"; photo count {shown_before} -> {after['photos']}"
                    f" (before: {result.baseline_source})"
                )
    _audit(audit_path, result)
    return result
