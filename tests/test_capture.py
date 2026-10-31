import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aiglasses.cli import main as cli
from aiglasses.domain.errors import ProtocolError
from aiglasses.research.btsnoop import parse_btsnoop, read_btsnoop
from aiglasses.research.capture import (
    FROM_GLASSES,
    TO_GLASSES,
    analyze_records,
    find_known,
    load_handle_names,
    reactions,
    segment,
)
from tests.helpers import (
    GLASSES,
    acl,
    att_op,
    btsnoop_file,
    build_bc_frame,
    classic_connection_complete,
    le_connection_complete,
)

PHOTO_EVENT = build_bc_frame(0x73, bytes.fromhex("0107000000000001"))
VOICE = build_bc_frame(0x59, bytes([0x4B, 0x41, 0x1F]) + bytes(37))
LE, CLASSIC = 0x40, 0x41
CCCD = "00002902-0000-1000-8000-00805f9b34fb"


def gatt_dump(tmp_path: Path) -> Path:
    data = {
        "services": [
            {
                "characteristics": [
                    {"uuid": "de5bf72a-d711", "handle": 141, "descriptors": []},
                    {
                        "uuid": "de5bf729-d711",
                        "handle": 143,
                        "descriptors": [{"uuid": CCCD, "handle": 145}],
                    },
                ]
            }
        ]
    }
    path = tmp_path / "gatt_dump.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def scenario() -> bytes:
    split_at = 5
    long_notify = att_op(0x1B, 144, PHOTO_EVENT)
    rfcomm_request = bytes([0x02, 0x07, 0x04, 0x00, 0x03, 0x00, 0x40, 0x00])
    rfcomm_response = bytes([0x03, 0x07, 0x08, 0x00, 0x41, 0x00, 0x40, 0x00, 0, 0, 0, 0])
    rfcomm_data = bytes([0x0B, 0xEF, 0x15]) + b"AT+BRSF=0\r" + bytes(1)
    packets = [
        (0.0, False, le_connection_complete(LE, GLASSES)),
        (0.1, False, le_connection_complete(0x50, "11:22:33:44:55:66")),
        (0.2, False, classic_connection_complete(CLASSIC, GLASSES)),
        (1.0, True, acl(LE, 4, att_op(0x12, 145, b"\x01\x00"))),
        (2.0, False, acl(LE, 4, att_op(0x1B, 144, PHOTO_EVENT))),
        (3.0, True, acl(LE, 4, att_op(0x52, 142, b"\xbc\x01"))),
        (3.1, True, acl(0x50, 4, att_op(0x52, 142, b"\xff"))),
        (14.0, False, acl(LE, 4, att_op(0x1B, 144, VOICE))),
        (14.02, False, acl(LE, 4, att_op(0x1B, 144, VOICE))),
        (14.04, False, acl(LE, 4, att_op(0x1B, 144, VOICE))),
        (25.0, False, acl(LE, 4, long_notify[:split_at], pb=2, l2cap_len=len(long_notify))),
        (25.0, False, acl(LE, 4, long_notify[split_at:], pb=1)),
        (31.0, True, acl(CLASSIC, 1, rfcomm_request)),
        (31.1, False, acl(CLASSIC, 1, rfcomm_response)),
        (32.0, True, acl(CLASSIC, 0x41, rfcomm_data)),
    ]
    return btsnoop_file(packets)


def test_parse_rejects_non_btsnoop() -> None:
    with pytest.raises(ProtocolError):
        parse_btsnoop(b"\x0a\x0d\x0d\x0apcapng....")


def test_analysis_tracks_target_only_and_decodes(tmp_path: Path) -> None:
    names = load_handle_names(gatt_dump(tmp_path))
    assert names[142] == "de5bf72a" and names[144] == "de5bf729"
    assert names[145] == "CCCD of de5bf729"
    analysis = analyze_records(parse_btsnoop(scenario()), GLASSES, names)
    ops = [(r.direction, r.channel, r.op, r.target) for r in analysis.rows]
    assert (TO_GLASSES, "ATT", "write request", "h145 CCCD of de5bf729") in ops
    assert (TO_GLASSES, "ATT", "write command", "h142 de5bf72a") in ops
    assert sum(1 for r in analysis.rows if r.target.startswith("h142")) == 1
    photo = [r for r in analysis.rows if "payload 0107000000000001" in r.decoded]
    assert len(photo) == 2  # one normal, one reassembled from two fragments
    voice = [r for r in analysis.rows if r.decoded.startswith("voice stream")]
    assert len(voice) == 1 and voice[0].count == 3
    rfcomm = [r for r in analysis.rows if r.channel == "RFCOMM"]
    assert rfcomm and rfcomm[0].op == "UIH"
    assert bytes.fromhex(rfcomm[0].hex).startswith(b"AT+BRSF")
    assert analysis.psm_counts == {"RFCOMM": 1}
    assert analysis.connections[LE] == GLASSES


def test_segments_and_reactions() -> None:
    analysis = analyze_records(parse_btsnoop(scenario()), GLASSES, {})
    segs = segment(analysis.rows, 6.0)
    assert segs[0] == 0 and max(segs) == 3
    first_event, writes = reactions(analysis.rows)[0]
    assert first_event.direction == FROM_GLASSES
    assert [w.hex for w in writes] == ["bc01"]


def test_unknown_address_warns() -> None:
    analysis = analyze_records(parse_btsnoop(scenario()), "AA:BB:CC:DD:EE:FF", {})
    assert analysis.rows == [] and "never connected" in analysis.warnings[0]


def test_zip_extraction(tmp_path: Path) -> None:
    archive = tmp_path / "bugreport.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("FS/data/misc/bluetooth/logs/btsnoop_hci.log", scenario())
        zf.writestr("dumpstate.txt", "x" * 10)
    assert len(read_btsnoop(archive)) == len(parse_btsnoop(scenario()))
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("dumpstate.txt", "x")
    with pytest.raises(ProtocolError, match="no btsnoop"):
        read_btsnoop(empty)


def test_capture_analyze_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    log = tmp_path / "btsnoop_hci.log"
    log.write_bytes(scenario())
    dump = gatt_dump(tmp_path)
    args = ["capture", "analyze", str(log), "--address", GLASSES, "--gatt-dump", str(dump)]
    result = CliRunner().invoke(cli.app, [*args, "--actions", "connect,voice,fragmented,rfcomm"])
    assert result.exit_code == 0, result.output
    report = (tmp_path / "captures" / "analysis.md").read_text()
    assert "write command" in report and "voice stream" in report
    assert "no writes" in result.output or "bc01" in result.output
    assert "voice" in (tmp_path / "captures" / "analysis.csv").read_text()
    mismatch = CliRunner().invoke(cli.app, [*args, "--actions", "a,b"])
    assert "labels not applied" in mismatch.output
    missing = CliRunner().invoke(cli.app, ["capture", "analyze", str(tmp_path / "x.log")])
    assert missing.exit_code != 0


def test_find_known_values() -> None:
    mac = bytes.fromhex("D04B1BC32324")
    info = build_bc_frame(0x73, b"fw 9.40.00 ok") + b""
    packets = [
        (0.0, False, le_connection_complete(LE, GLASSES)),
        (1.0, False, acl(LE, 4, att_op(0x1B, 144, info))),
        (2.0, False, acl(LE, 4, att_op(0x1B, 144, build_bc_frame(0x73, b"\x01d")))),
        (3.0, True, acl(LE, 4, att_op(0x12, 142, mac[::-1] + b"2512271030"))),
        (4.0, True, acl(LE, 4, att_op(0x12, 142, b"\x00" * 30 + b"d"))),  # long: weak ignored
    ]
    analysis = analyze_records(parse_btsnoop(btsnoop_file(packets)), GLASSES, {})
    found = {(h.name.split(" (")[0].split(" '")[0], h.strong) for h in find_known(analysis.rows)}
    assert ("firmware text", True) in found
    names = [h.name for h in find_known(analysis.rows)]
    assert any(n.startswith("MAC reversed") for n in names)
    assert "app version text '2512271030'" in names
    assert sum(1 for n in names if n.startswith("battery")) == 1


def test_empty_cfa_log_gives_clear_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import struct

    monkeypatch.chdir(tmp_path)
    header = b"btsnoop" + bytes(1) + struct.pack(">II", 1, 1002)
    archive = tmp_path / "bugreport.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("FS/data/misc/bluetooth/logs/BT_HCI_2026_1031_100407.cfa", header)
    assert read_btsnoop(archive) == []
    result = CliRunner().invoke(cli.app, ["capture", "analyze", str(archive)])
    assert result.exit_code == 1
    assert "0 packets" in result.output and "Bluetooth off" in result.output.replace("\n", " ")
