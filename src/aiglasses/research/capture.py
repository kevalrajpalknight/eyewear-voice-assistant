"""Turn an HCI snoop log into an action-vs-bytes table for one device. Read-only analysis."""

import json
import struct
from dataclasses import dataclass, field, replace
from pathlib import Path

from aiglasses.domain.errors import ProtocolError
from aiglasses.protocol.bcframe import parse_bc_frame
from aiglasses.protocol.voice import VOICE_CMD
from aiglasses.research.btsnoop import HCI_ACL, HCI_EVENT, HciRecord

CID_ATT = 0x0004
CID_SIGNALING = 0x0001
PSM_NAMES = {
    0x0001: "SDP",
    0x0003: "RFCOMM",
    0x0017: "AVCTP",
    0x0019: "AVDTP",
    0x001B: "AVCTP-browse",
}
ATT_NAMES = {
    0x01: "error",
    0x0A: "read request",
    0x0B: "read response",
    0x12: "write request",
    0x13: "write response",
    0x16: "prepare write",
    0x18: "execute write",
    0x1B: "notification",
    0x1D: "indication",
    0x52: "write command",
}
ATT_WITH_HANDLE_AND_VALUE = {0x12, 0x52, 0x1B, 0x1D}
RFCOMM_TYPES = {0x2F: "SABM", 0x63: "UA", 0x0F: "DM", 0x43: "DISC", 0xEF: "UIH"}
TO_GLASSES, FROM_GLASSES = "TO glasses", "FROM glasses"
VOICE_LABEL = "voice stream (BC cmd 0x59)"


@dataclass(frozen=True)
class Row:
    seq: int
    ts: float
    direction: str
    channel: str  # "ATT" | "RFCOMM"
    op: str
    target: str
    hex: str
    decoded: str = ""
    count: int = 1  # collapsed frames (voice stream bursts)
    end_ts: float = 0.0  # last frame time of a collapsed burst


@dataclass
class Analysis:
    rows: list[Row] = field(default_factory=list)
    connections: dict[int, str] = field(default_factory=dict)
    psm_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def load_handle_names(gatt_dump: Path | None) -> dict[int, str]:
    """Map ATT value/descriptor handles to UUIDs. Windows/bleak reports the characteristic
    declaration handle, so the value handle is that plus one."""
    if gatt_dump is None or not gatt_dump.exists():
        return {}
    names: dict[int, str] = {}
    for svc in json.loads(gatt_dump.read_text(encoding="utf-8"))["services"]:
        for ch in svc["characteristics"]:
            short = ch["uuid"][:8].lstrip("0") or ch["uuid"]
            names[ch["handle"] + 1] = short
            for d in ch["descriptors"]:
                names[d["handle"]] = (
                    f"CCCD of {short}" if d["uuid"].startswith("00002902") else d["uuid"][:8]
                )
    return names


def _address(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in reversed(raw))


class _Parser:
    def __init__(self, target: str | None, handle_names: dict[int, str]) -> None:
        self.target = target.upper() if target else None
        self.names = handle_names
        self.out = Analysis()
        self._buffers: dict[tuple[int, bool], tuple[int, bytearray]] = {}
        self._psm: dict[tuple[int, int, bool], int] = {}
        self._pending: dict[tuple[int, int], tuple[int, bool]] = {}

    def _wanted(self, handle: int) -> bool:
        addr = self.out.connections.get(handle)
        return addr is not None and (self.target is None or addr == self.target)

    def _add(self, ts: float, sent: bool, channel: str, op: str, target: str, value: bytes) -> None:
        decoded = ""
        try:
            frame = parse_bc_frame(value)
            decoded = f"BC cmd 0x{frame.cmd:02x} payload {frame.payload.hex()}"
            if frame.cmd == VOICE_CMD and not sent:
                self._extend_voice(ts, op, target)
                return
        except ProtocolError:
            pass
        row = Row(
            len(self.out.rows),
            ts,
            TO_GLASSES if sent else FROM_GLASSES,
            channel,
            op,
            target,
            value.hex(),
            decoded,
        )
        self.out.rows.append(row)

    def _extend_voice(self, ts: float, op: str, target: str) -> None:
        last = self.out.rows[-1] if self.out.rows else None
        if last and last.decoded == VOICE_LABEL and ts - (last.end_ts or last.ts) < 1.0:
            self.out.rows[-1] = replace(last, count=last.count + 1, end_ts=ts)
        else:
            self.out.rows.append(
                Row(len(self.out.rows), ts, FROM_GLASSES, "ATT", op, target, "", VOICE_LABEL, 1, ts)
            )

    def event(self, rec: HciRecord) -> None:
        d = rec.data
        if len(d) < 2:
            return
        code, params = d[0], d[2:]
        if code == 0x03 and len(params) >= 11 and params[0] == 0:  # classic connection complete
            self.out.connections[struct.unpack("<H", params[1:3])[0] & 0x0FFF] = _address(
                params[3:9]
            )
        elif code == 0x3E and params:  # LE meta
            sub, body = params[0], params[1:]
            if sub in (0x01, 0x0A) and len(body) >= 10 and body[0] == 0:
                self.out.connections[struct.unpack("<H", body[1:3])[0] & 0x0FFF] = _address(
                    body[5:11]
                )

    def acl(self, rec: HciRecord) -> None:
        d = rec.data
        if len(d) < 4:
            return
        hf, _length = struct.unpack("<HH", d[:4])
        handle, pb, payload = hf & 0x0FFF, (hf >> 12) & 3, d[4:]
        key = (handle, rec.sent)
        if pb in (0, 2) and len(payload) >= 4:
            l2len = struct.unpack("<H", payload[:2])[0]
            self._buffers[key] = (l2len, bytearray(payload[2:]))
        elif pb == 1 and key in self._buffers:
            self._buffers[key][1].extend(payload)
        else:
            return
        l2len, buf = self._buffers[key]
        if len(buf) >= l2len + 2:
            del self._buffers[key]
            self.l2cap(
                rec, handle, struct.unpack("<H", bytes(buf[:2]))[0], bytes(buf[2 : 2 + l2len])
            )

    def l2cap(self, rec: HciRecord, handle: int, cid: int, data: bytes) -> None:
        if not self._wanted(handle):
            return
        if cid == CID_ATT:
            self.att(rec, data)
        elif cid == CID_SIGNALING:
            self.signaling(rec, handle, data)
        else:
            psm = self._psm.get((handle, cid, rec.sent))
            if psm is not None:
                name = PSM_NAMES.get(psm, f"PSM 0x{psm:04x}")
                self.out.psm_counts[name] = self.out.psm_counts.get(name, 0) + 1
                if psm == 0x0003:
                    self.rfcomm(rec, data)

    def signaling(self, rec: HciRecord, handle: int, data: bytes) -> None:
        if len(data) < 4:
            return
        code, ident, body = data[0], data[1], data[4:]
        if code == 0x02 and len(body) >= 4:  # connection request
            psm, scid = struct.unpack("<HH", body[:4])
            self._pending[(handle, ident)] = (psm, rec.sent)
            self._psm[(handle, scid, not rec.sent)] = psm
        elif code == 0x03 and len(body) >= 6 and (handle, ident) in self._pending:  # response
            dcid, _scid, result = struct.unpack("<HHH", body[:6])
            if result == 0:
                psm, init_sent = self._pending[(handle, ident)]
                self._psm[(handle, dcid, init_sent)] = psm

    def att(self, rec: HciRecord, data: bytes) -> None:
        if not data:
            return
        opcode = data[0]
        op = ATT_NAMES.get(opcode, f"opcode 0x{opcode:02x}")
        if opcode in ATT_WITH_HANDLE_AND_VALUE and len(data) >= 3:
            handle = struct.unpack("<H", data[1:3])[0]
            target = f"h{handle} {self.names.get(handle, '?')}"
            self._add(rec.ts, rec.sent, "ATT", op, target, data[3:])
        elif opcode == 0x0A and len(data) >= 3:
            handle = struct.unpack("<H", data[1:3])[0]
            self._add(rec.ts, rec.sent, "ATT", op, f"h{handle} {self.names.get(handle, '?')}", b"")
        elif opcode == 0x0B:
            self._add(rec.ts, rec.sent, "ATT", op, "", data[1:])

    def rfcomm(self, rec: HciRecord, data: bytes) -> None:
        if len(data) < 4:
            return
        dlci, ctrl = data[0] >> 2, data[1]
        kind = RFCOMM_TYPES.get(ctrl & 0xEF, f"0x{ctrl:02x}")
        if data[2] & 1:
            length, start = data[2] >> 1, 3
        else:
            length, start = (data[2] >> 1) | (data[3] << 7), 4
        payload = data[start : start + length]
        if kind == "UIH" and dlci > 0 and ctrl & 0x10 and payload:
            payload = payload[1:]  # credit byte
        direction = TO_GLASSES if rec.sent else FROM_GLASSES
        target = f"DLCI {dlci}"
        self.out.rows.append(
            Row(len(self.out.rows), rec.ts, direction, "RFCOMM", kind, target, payload.hex())
        )


def analyze_records(
    records: list[HciRecord], address: str | None, handle_names: dict[int, str]
) -> Analysis:
    parser = _Parser(address, handle_names)
    for rec in records:
        if rec.packet_type == HCI_EVENT:
            parser.event(rec)
        elif rec.packet_type == HCI_ACL:
            parser.acl(rec)
    if not parser.out.connections:
        parser.out.warnings.append(
            "no connection events in the log; start logging before connecting"
        )
    elif address and address.upper() not in parser.out.connections.values():
        parser.out.warnings.append(f"{address} never connected in this log; connections: "
                                   f"{sorted(set(parser.out.connections.values()))}")  # fmt: skip
    return parser.out


def segment(rows: list[Row], gap_seconds: float) -> list[int]:
    """Segment index per row: a new segment starts after a quiet gap (actions are 10 s apart)."""
    segments: list[int] = []
    current = 0
    for i, row in enumerate(rows):
        if i and row.ts - (rows[i - 1].end_ts or rows[i - 1].ts) > gap_seconds:
            current += 1
        segments.append(current)
    return segments


def reactions(rows: list[Row], window: float = 5.0) -> list[tuple[Row, list[Row]]]:
    """For each non-voice notification from the glasses, the writes sent in the next `window` s."""
    result: list[tuple[Row, list[Row]]] = []
    for i, row in enumerate(rows):
        if row.direction != FROM_GLASSES or row.decoded == VOICE_LABEL or row.op != "notification":
            continue
        writes: list[Row] = []
        for later in rows[i + 1 :]:
            if later.ts - row.ts > window:
                break
            is_event = later.direction == FROM_GLASSES and later.op == "notification"
            if is_event and later.decoded != VOICE_LABEL:
                break
            if later.direction == TO_GLASSES:
                writes.append(later)
        result.append((row, writes))
    return result


@dataclass(frozen=True)
class KnownHit:
    row: Row
    name: str
    strong: bool  # weak patterns (short byte strings) are noisy and need a small payload
    offset: int


_MAC = bytes.fromhex("D04B1BC32324")
KNOWN_PATTERNS: list[tuple[str, bytes, bool]] = [
    ("firmware text '9.40.00'", b"9.40.00", True),
    ("app version text '2512271030'", b"2512271030", True),
    ("app version as BCD 25 12 27 10 30", bytes.fromhex("2512271030"), True),
    ("MAC D0:4B:1B:C3:23:24 (display order)", _MAC, True),
    ("MAC reversed (little-endian)", _MAC[::-1], True),
    ("firmware as bytes 09 28 00", bytes([9, 40, 0]), False),
    ("battery 100% (0x64)", b"\x64", False),
]
WEAK_MAX_LEN = 16


def find_known(rows: list[Row]) -> list[KnownHit]:
    """Search captured values for known facts: battery, firmware, MAC, app version."""
    hits: list[KnownHit] = []
    for row in rows:
        value = bytes.fromhex(row.hex)
        for name, pattern, strong in KNOWN_PATTERNS:
            if not strong and len(value) > WEAK_MAX_LEN:
                continue
            offset = value.find(pattern)
            if offset >= 0:
                hits.append(KnownHit(row, name, strong, offset))
    return hits
