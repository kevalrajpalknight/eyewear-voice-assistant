from pathlib import Path

import numpy as np
import pytest

from aiglasses.domain.errors import ProtocolError
from aiglasses.protocol.bcframe import BcFrame, crc16_modbus, parse_bc_frame
from aiglasses.protocol.voice import (
    CONTROL_CMD,
    VOICE_CMD,
    VOICE_END_PAYLOAD,
    VOICE_START_PAYLOAD,
    voice_bursts,
)
from tests.helpers import build_bc_frame


def test_crc16_modbus_matches_captured_frames() -> None:
    # Captured from the unit: voice start marker (payload 03 01) and end marker (0a 01).
    assert crc16_modbus(bytes.fromhex("0301")) == 0x80C0
    assert crc16_modbus(bytes.fromhex("0a01")) == 0xD0C6
    assert crc16_modbus(b"") == 0xFFFF


def test_parse_roundtrip_and_known_real_frame() -> None:
    frame = parse_bc_frame(build_bc_frame(0x73, b"\x03\x01"))
    assert frame == BcFrame(cmd=0x73, payload=b"\x03\x01")
    # Captured from the unit: voice start marker.
    assert parse_bc_frame(bytes.fromhex("bc730200c0800301")) == BcFrame(0x73, b"\x03\x01")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xaa\x73\x00\x00\xff\xff",
        bytes.fromhex("bc730300c0800301"),
        bytes.fromhex("bc730200c1800301"),
    ],
)
def test_parse_rejects_bad_frames(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        parse_bc_frame(raw)


def test_voice_bursts_split_on_markers_and_gaps() -> None:
    def voice(ts: float, tag: int) -> tuple[float, BcFrame]:
        return ts, BcFrame(VOICE_CMD, bytes([tag]) * 40)

    frames = [
        (0.0, BcFrame(CONTROL_CMD, VOICE_START_PAYLOAD)),
        voice(0.1, 1),
        voice(0.12, 2),
        (0.14, BcFrame(CONTROL_CMD, VOICE_END_PAYLOAD)),
        voice(5.0, 3),
        voice(5.02, 4),
        voice(9.0, 5),  # gap > 1 s starts another burst
        (9.5, BcFrame(CONTROL_CMD, b"\x0b\x2c")),  # unrelated control event
    ]
    bursts = voice_bursts(frames)
    assert [[p[0] for p in b] for b in bursts] == [[1, 2], [3, 4], [5]]


def test_opus_roundtrip_on_synthetic_tone() -> None:
    av = pytest.importorskip("av")
    from aiglasses.research.voice_decode import OpusVoiceDecoder

    rate, frame_len = 16000, 320
    t = np.arange(rate) / rate
    tone = (0.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    enc = av.CodecContext.create("libopus", "w")
    enc.sample_rate = rate
    enc.layout = "mono"
    enc.format = "s16"
    enc.bit_rate = 16000
    enc.options = {"frame_duration": "20", "vbr": "off", "application": "voip"}
    enc.open()
    packets: list[bytes] = []
    for i in range(0, len(tone) - frame_len + 1, frame_len):
        frame = av.AudioFrame.from_ndarray(
            tone[None, i : i + frame_len], format="s16", layout="mono"
        )
        frame.sample_rate = rate
        frame.pts = i
        packets += [bytes(p) for p in enc.encode(frame)]
    assert len(packets) > 40

    wrapped = [parse_bc_frame(build_bc_frame(VOICE_CMD, p)).payload for p in packets]
    audio = OpusVoiceDecoder().decode(wrapped)
    samples = audio.samples.astype(np.float64)
    assert audio.sample_rate == 16000
    assert samples.size >= audio.sample_rate * 0.8
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    peak_hz = np.argmax(spectrum) * audio.sample_rate / samples.size
    assert abs(peak_hz - 440) < 25


def test_voice_candidates_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from typer.testing import CliRunner

    from aiglasses.cli import main as cli

    pytest.importorskip("av")
    root = tmp_path
    monkeypatch.chdir(root)
    (root / "logs").mkdir()
    lines = []
    for i in range(6):
        frame = build_bc_frame(VOICE_CMD, bytes([0x4B, 0x41, 0x1F]) + bytes(37))
        entry = {"ts": f"2026-10-30T19:04:08.{600 + i * 20:03d}+00:00", "op": "notify",
                 "uuid": "de5bf729-d711-4e47-af26-65e3012a5dc7", "hex": frame.hex()}  # fmt: skip
        lines.append(json.dumps(entry))
    (root / "logs" / "trace.jsonl").write_text("\n".join(lines), encoding="utf-8")
    result = CliRunner().invoke(cli.app, ["voice", "candidates"])
    assert result.exit_code == 0 and "Decode attempts" in result.output
    assert list((root / "logs" / "decode_candidates").glob("*strip2*"))
