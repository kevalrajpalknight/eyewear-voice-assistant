"""Real speech engines. Heavy imports stay inside the constructors (optional `assistant` extra)."""

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np

from aiglasses.assistant.interfaces import AudioChunk, Pcm, Transcript, TtsOptions
from aiglasses.domain.errors import GlassesError, NotSupportedError

STT_CHOICES = ("moonshine-base", "moonshine-tiny", "fw-base.en")
TTS_CHOICES = ("int8", "fp32")
KOKORO_FILES = {"int8": "kokoro-v1.0.int8.onnx", "fp32": "kokoro-v1.0.onnx"}


class ModelMissingError(GlassesError):
    """A model file is missing or does not match the recorded checksum."""


def verify_checksum(models_dir: Path, relative: str, manifest_key: str) -> None:
    """Compare a model file with bench/models_manifest.json (from `python -m bench.models`)."""
    path = models_dir / relative
    if not path.exists():
        raise ModelMissingError(f"{path} is missing; run `python -m bench.models` in bench/")
    manifest_path = models_dir.parent / "models_manifest.json"
    if not manifest_path.exists():
        return
    recorded = json.loads(manifest_path.read_text()).get(manifest_key, {}).get("sha256")
    if recorded is None:
        return
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != recorded:
        raise ModelMissingError(f"{path.name} does not match its recorded checksum")


def _require_english(language: str) -> None:
    if language != "en":
        raise NotSupportedError(f"only English is implemented, not {language!r}")


def _to_float(pcm: Pcm, normalize: bool) -> np.ndarray[Any, np.dtype[np.float32]]:
    samples = pcm.astype(np.float32) / 32768.0
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if normalize and peak > 0.0:
        samples = samples * (10 ** (-3 / 20) / peak)
    return samples.astype(np.float32)


class MoonshineStt:
    def __init__(self, models_dir: Path, arch: str = "base", *, normalize: bool = False) -> None:
        from moonshine_voice import ModelArch
        from moonshine_voice.transcriber import Transcriber

        folders = [
            p.parent
            for p in (models_dir / "moonshine").rglob("encoder_model.ort")
            if p.parent.name == f"{arch}-en"
        ]
        if not folders:
            raise ModelMissingError(f"Moonshine {arch}-en not found under {models_dir}/moonshine")
        self.name = f"moonshine-{arch}"
        self._normalize = normalize
        self._lock = threading.Lock()
        self._transcriber: Any = Transcriber(
            model_path=str(folders[0]), model_arch=getattr(ModelArch, arch.upper())
        )

    def _run(self, samples: np.ndarray[Any, np.dtype[np.float32]]) -> str:
        with self._lock:
            result = self._transcriber.transcribe_without_streaming(samples.tolist(), 16000)
        return " ".join(line.text.strip() for line in result.lines).strip()

    async def transcribe(
        self, pcm: Pcm, sample_rate: int = 16000, *, language: str = "en"
    ) -> Transcript:
        _require_english(language)
        started = time.perf_counter()
        text = await asyncio.to_thread(self._run, _to_float(pcm, self._normalize))
        return Transcript(text, language, len(pcm) / sample_rate, time.perf_counter() - started)


class FasterWhisperStt:
    def __init__(self, models_dir: Path, *, normalize: bool = False) -> None:
        from faster_whisper import WhisperModel

        folder = models_dir / "faster-whisper-base.en"
        verify_checksum(
            models_dir, "faster-whisper-base.en/model.bin", "faster-whisper-base.en/model.bin"
        )
        self.name = "fw-base.en-int8"
        self._normalize = normalize
        self._lock = threading.Lock()
        self._model: Any = WhisperModel(
            str(folder), device="cpu", compute_type="int8", cpu_threads=4
        )

    def _run(self, samples: np.ndarray[Any, np.dtype[np.float32]]) -> str:
        with self._lock:
            segments, _info = self._model.transcribe(
                samples, language="en", beam_size=1, temperature=0.0, vad_filter=False,
                condition_on_previous_text=False,
            )  # fmt: skip
            return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(
        self, pcm: Pcm, sample_rate: int = 16000, *, language: str = "en"
    ) -> Transcript:
        _require_english(language)
        started = time.perf_counter()
        text = await asyncio.to_thread(self._run, _to_float(pcm, self._normalize))
        return Transcript(text, language, len(pcm) / sample_rate, time.perf_counter() - started)


def kokoro_language(voice: str, language: str) -> str:
    _require_english(language)
    return "en-gb" if voice.startswith("b") else "en-us"


class KokoroTts:
    name = "kokoro"

    def __init__(self, models_dir: Path, variant: str = "int8") -> None:
        from kokoro_onnx import Kokoro

        if variant not in KOKORO_FILES:
            raise NotSupportedError(f"unknown Kokoro variant {variant!r}")
        model = KOKORO_FILES[variant]
        verify_checksum(models_dir, f"kokoro/{model}", f"kokoro/{model}")
        verify_checksum(models_dir, "kokoro/voices-v1.0.bin", "kokoro/voices-v1.0.bin")
        self.name = f"kokoro-{variant}"
        self._engine: Any = Kokoro(
            str(models_dir / "kokoro" / model), str(models_dir / "kokoro" / "voices-v1.0.bin")
        )

    async def synthesize_stream(self, text: str, options: TtsOptions) -> AsyncIterator[AudioChunk]:
        lang = kokoro_language(options.voice, options.language)
        started = time.perf_counter()
        async for samples, rate in self._engine.create_stream(
            text, voice=options.voice, speed=options.speed, lang=lang
        ):
            pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
            yield AudioChunk(pcm, int(rate), time.perf_counter() - started)
