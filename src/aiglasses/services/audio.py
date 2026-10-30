from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from aiglasses.domain.errors import NotSupportedError
from aiglasses.transport.base import AudioEndpoint, AudioIO, Samples

TONE_FADE_SECONDS = 0.05


@dataclass(frozen=True)
class RecordingStats:
    path: Path
    seconds: float
    sample_rate: int
    peak: float
    rms: float


def make_tone(freq: float, seconds: float, sample_rate: int, volume: float) -> Samples:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    tone = volume * np.sin(2 * np.pi * freq * t)
    fade = min(int(TONE_FADE_SECONDS * sample_rate), len(tone) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade)
        tone[:fade] *= ramp
        tone[-fade:] *= ramp[::-1]
    return tone.astype(np.float32)


def resample_linear(samples: Samples, src_rate: int, dst_rate: int) -> Samples:
    """Linear resampling of (frames, channels) audio; adequate for test tones and speech."""
    if src_rate == dst_rate:
        return samples
    frames = int(len(samples) * dst_rate / src_rate)
    positions = np.linspace(0, len(samples) - 1, frames)
    channels = [
        np.interp(positions, np.arange(len(samples)), samples[:, c])
        for c in range(samples.shape[1])
    ]
    return np.stack(channels, axis=1).astype(np.float32)


def fit_channels(samples: Samples, channels: int) -> Samples:
    if samples.shape[1] == channels:
        return samples
    if samples.shape[1] == 1:
        return np.repeat(samples, channels, axis=1)
    return samples[:, :channels]


class AudioService:
    """Plays to the glasses speaker (A2DP) and records their microphone (HFP)."""

    def __init__(self, audio: AudioIO | None, name_hint: str = "AIMB-G2") -> None:
        self._audio = audio
        self._hint = name_hint

    def _io(self) -> AudioIO:
        if self._audio is None:
            raise NotSupportedError("no audio backend configured")
        return self._audio

    def endpoints(self, name_hint: str | None = None) -> list[AudioEndpoint]:
        return self._io().list_endpoints(self._hint if name_hint is None else name_hint)

    def _pick(self, kind: str, index: int | None) -> AudioEndpoint:
        candidates = [e for e in self.endpoints() if e.kind == kind]
        if index is not None:
            candidates = [e for e in candidates if e.index == index]
        if not candidates:
            raise NotSupportedError(
                f"no {kind} endpoint matching '{self._hint}'. "
                "Pair the glasses in Windows Settings first."
            )
        return candidates[0]

    async def play_tone(
        self,
        freq: float = 440.0,
        seconds: float = 1.0,
        volume: float = 0.15,
        endpoint: int | None = None,
    ) -> AudioEndpoint:
        target = self._pick("output", endpoint)
        tone = make_tone(freq, seconds, target.sample_rate, volume)
        await self._io().play(
            fit_channels(tone[:, None], target.channels), target.sample_rate, target
        )
        return target

    async def play_file(self, path: Path, endpoint: int | None = None) -> AudioEndpoint:
        target = self._pick("output", endpoint)
        data, rate = sf.read(path, dtype="float32", always_2d=True)
        data = fit_channels(resample_linear(data, rate, target.sample_rate), target.channels)
        await self._io().play(data, target.sample_rate, target)
        return target

    async def record(
        self, seconds: float, path: Path, endpoint: int | None = None
    ) -> RecordingStats:
        target = self._pick("input", endpoint)
        samples = await self._io().record(seconds, target)
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, samples, target.sample_rate, subtype="PCM_16")
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        rms = float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0
        return RecordingStats(
            path, len(samples) / target.sample_rate, target.sample_rate, peak, rms
        )
