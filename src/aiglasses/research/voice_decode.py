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


class OpusVoiceDecoder:
    """Decodes raw Opus packets (no Ogg container) with FFmpeg's libopus via PyAV."""

    name = "opus"

    def __init__(self, sample_rate: int = 16000) -> None:
        self._rate = sample_rate

    def decode(self, packets: Sequence[bytes]) -> DecodedAudio:
        import av

        ctx: Any = av.CodecContext.create("libopus", "r")
        ctx.sample_rate = self._rate
        ctx.layout = "mono"
        ctx.open()
        chunks: list[np.ndarray] = []
        for packet in packets:
            for frame in ctx.decode(av.Packet(packet)):
                chunks.append(frame.to_ndarray().reshape(-1))
        samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
        return DecodedAudio(to_int16(samples), ctx.sample_rate)


@dataclass(frozen=True)
class Candidate:
    name: str
    codec: str  # "opus" | "speex"
    strip: int  # bytes removed from the front of the 42-byte body (CRC + packet)


# The body is crc16 (2 bytes) + 40-byte packet, so strip=2 is the CRC-verified Opus layout.
CANDIDATES = [
    Candidate("opus_strip0_raw", "opus", 0),
    Candidate("opus_strip1", "opus", 1),
    Candidate("opus_strip2_crc_removed", "opus", 2),
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

    ctx: Any = av.CodecContext.create(codec, "r")
    if codec == "opus":
        ctx.sample_rate = 16000
        ctx.layout = "mono"
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
            if cand.name == "opus_strip2_crc_removed":
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
