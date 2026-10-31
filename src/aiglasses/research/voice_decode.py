"""Offline experiments: try decoding the 0x59 voice bursts. Needs the `research` extra (PyAV)."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from aiglasses.protocol.voice import DecodedAudio


def to_int16(samples: np.ndarray) -> np.ndarray:
    """PyAV may return float32 in [-1, 1] or int16 depending on the decoder; normalise to int16."""
    if samples.dtype == np.int16:
        return samples
    return np.clip(samples.astype(np.float64) * 32767, -32768, 32767).astype(np.int16)


APP_SAMPLE_RATE = (
    16000  # the official app decodes with: no Ogg header, 16 kHz, mono, 40-byte packets
)


def decode_opus_16k(packets: Sequence[bytes]) -> tuple[np.ndarray, int, int]:
    """Decode raw Opus packets and return (int16 samples at 16 kHz mono, frames decoded, errors).

    FFmpeg's libopus always outputs 48 kHz, so the output is resampled to the app's 16 kHz.
    """
    import av

    ctx: Any = av.CodecContext.create("libopus", "r")
    ctx.sample_rate = APP_SAMPLE_RATE
    ctx.layout = "mono"
    ctx.open()
    resampler: Any = av.AudioResampler(format="s16", layout="mono", rate=APP_SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    decoded = errors = 0
    for packet in packets:
        try:
            frames = ctx.decode(av.Packet(packet))
        except Exception:  # the decoder rejected this packet
            errors += 1
            continue
        for frame in frames:
            decoded += 1
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
    for out in resampler.resample(None):
        chunks.append(out.to_ndarray().reshape(-1))
    samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    return to_int16(samples), decoded, errors


class OpusVoiceDecoder:
    """Decodes the glasses' voice packets the way the official app does (see docs/PROTOCOL.md)."""

    name = "opus"

    def decode(self, packets: Sequence[bytes]) -> DecodedAudio:
        samples, _decoded, _errors = decode_opus_16k(packets)
        return DecodedAudio(samples, APP_SAMPLE_RATE)


@dataclass(frozen=True)
class Candidate:
    name: str
    codec: str  # "opus" | "speex"
    strip: int  # bytes removed from the front of the 42-byte body (CRC + packet)


# The body is crc16 (2 bytes) + 40-byte packet, so strip=2 is the CRC-verified Opus layout.
CANDIDATES = [
    Candidate("opus_strip0_raw", "opus", 0),
    Candidate("opus_strip1", "opus", 1),
    Candidate("opus_strip2_app_format", "opus", 2),
    Candidate("opus_strip3", "opus", 3),
    Candidate("opus_strip4", "opus", 4),
    Candidate("speex_strip2", "speex", 2),
]


@dataclass(frozen=True)
class CandidateResult:
    name: str
    path: Path | None
    decoded_frames: int
    errors: int
    seconds: float
    rms: float
    peak: float
    note: str = ""


def _decode_with_pyav(codec: str, packets: list[bytes]) -> tuple[np.ndarray, int, int, int]:
    import av

    if codec == "opus":
        samples, decoded, errors = decode_opus_16k(packets)
        return samples, APP_SAMPLE_RATE, decoded, errors
    ctx: Any = av.CodecContext.create(codec, "r")
    ctx.open()
    chunks: list[np.ndarray] = []
    errors = 0
    for packet in packets:
        try:
            for frame in ctx.decode(av.Packet(packet)):
                chunks.append(frame.to_ndarray().reshape(-1))
        except Exception:  # decoder rejects the packet: counted as a failed frame
            errors += 1
    samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    return to_int16(samples), ctx.sample_rate, len(chunks), errors


def _stats(samples: np.ndarray) -> tuple[float, float]:
    if not samples.size:
        return 0.0, 0.0
    data = samples.astype(np.float64)
    return float(np.sqrt(np.mean(data**2))), float(np.max(np.abs(data)))


def normalize_peak(samples: np.ndarray, target_dbfs: float = -3.0) -> np.ndarray:
    """Scale int16 samples so the loudest sample sits at target_dbfs (processing, for listening)."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak == 0.0:
        return samples
    gain = 32767 * 10 ** (target_dbfs / 20) / peak
    return np.clip(samples.astype(np.float64) * gain, -32768, 32767).astype(np.int16)


def run_candidates(bodies: list[bytes], label: str, out_dir: Path) -> list[CandidateResult]:
    """Write one WAV per decode attempt. `bodies` are the 42-byte frame bodies (crc + packet)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[CandidateResult] = []
    for cand in CANDIDATES:
        packets = [b[cand.strip :] for b in bodies]
        try:
            samples, rate, frames, errors = _decode_with_pyav(cand.codec, packets)
        except Exception as exc:  # codec unavailable or needs extradata we do not have
            results.append(
                CandidateResult(cand.name, None, 0, len(packets), 0.0, 0.0, 0.0, str(exc))
            )
            continue
        rms, peak = _stats(samples)
        path = None
        if samples.size:
            path = out_dir / f"{label}_{cand.name}_{rate}Hz_mono.wav"
            sf.write(path, samples, rate, subtype="PCM_16")
            if cand.name == "opus_strip2_app_format":
                sf.write(
                    out_dir / f"{label}_{cand.name}_{rate}Hz_mono_PEAK-NORMALIZED.wav",
                    normalize_peak(samples),
                    rate,
                    subtype="PCM_16",
                )
        results.append(
            CandidateResult(
                cand.name, path, frames, errors, samples.size / rate if rate else 0.0, rms, peak
            )
        )
    return results
