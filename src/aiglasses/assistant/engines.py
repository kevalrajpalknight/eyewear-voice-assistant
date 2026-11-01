"""Real speech engines. Heavy imports stay inside the constructors (optional `assistant` extra)."""

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from aiglasses.assistant.interfaces import AudioChunk, Pcm, Transcript, TtsOptions
from aiglasses.domain.errors import GlassesError, NotSupportedError

MOONSHINE_FOLDERS = {
    "tiny": ("TINY", "tiny-en/quantized/tiny-en"),
    "base": ("BASE", "base-en/quantized/base-en"),
    "small-v2": ("SMALL_STREAMING", "small-streaming-en/quantized_*"),
    "medium-v2": ("MEDIUM_STREAMING", "medium-streaming-en/quantized_*"),
}
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

        if arch not in MOONSHINE_FOLDERS:
            raise NotSupportedError(f"unknown Moonshine model {arch!r}")
        arch_name, pattern = MOONSHINE_FOLDERS[arch]
        root = models_dir / "moonshine" / "download.moonshine.ai" / "model"
        folders = sorted(root.glob(pattern))
        if not folders:
            raise ModelMissingError(f"Moonshine {arch} not found under {root}")
        self.name = f"moonshine-{arch}"
        self._normalize = normalize
        self._lock = threading.Lock()
        self._transcriber: Any = Transcriber(
            model_path=str(folders[-1]), model_arch=getattr(ModelArch, arch_name)
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
    def __init__(
        self,
        models_dir: Path,
        size: str,
        *,
        compute_type: str,
        threads: int,
        hints: Sequence[str] = (),
        normalize: bool = False,
    ) -> None:
        from faster_whisper import WhisperModel

        folder = models_dir / f"faster-whisper-{size}"
        verify_checksum(
            models_dir, f"faster-whisper-{size}/model.bin", f"faster-whisper-{size}/model.bin"
        )
        self.name = f"fw-{size}-{compute_type}"
        self._normalize = normalize
        self._prompt = "Names and terms: " + ", ".join(hints) + "." if hints else None
        self._lock = threading.Lock()
        self._model: Any = WhisperModel(
            str(folder), device="cpu", compute_type=compute_type, cpu_threads=threads
        )

    def _run(self, samples: np.ndarray[Any, np.dtype[np.float32]]) -> str:
        with self._lock:
            segments, _info = self._model.transcribe(
                samples, language="en", beam_size=1, temperature=0.0, vad_filter=False,
                condition_on_previous_text=False, initial_prompt=self._prompt,
            )  # fmt: skip
            return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(
        self, pcm: Pcm, sample_rate: int = 16000, *, language: str = "en"
    ) -> Transcript:
        _require_english(language)
        started = time.perf_counter()
        text = await asyncio.to_thread(self._run, _to_float(pcm, self._normalize))
        return Transcript(text, language, len(pcm) / sample_rate, time.perf_counter() - started)


class ParakeetStt:
    """Parakeet TDT 0.6B v2 (int8 ONNX) through sherpa-onnx."""

    name = "parakeet-tdt-v2-int8"

    def __init__(self, models_dir: Path, *, threads: int, normalize: bool = False) -> None:
        import sherpa_onnx

        folder = models_dir / "parakeet-tdt-0.6b-v2-int8"
        for part in ("encoder", "decoder", "joiner"):
            verify_checksum(
                models_dir,
                f"parakeet-tdt-0.6b-v2-int8/{part}.int8.onnx",
                f"parakeet-tdt-0.6b-v2-int8/{part}.int8.onnx",
            )
        self._normalize = normalize
        self._lock = threading.Lock()
        self._recognizer: Any = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"),
            num_threads=threads, model_type="nemo_transducer",
        )  # fmt: skip

    def _run(self, samples: np.ndarray[Any, np.dtype[np.float32]]) -> str:
        with self._lock:
            stream = self._recognizer.create_stream()
            stream.accept_waveform(16000, samples)
            self._recognizer.decode_stream(stream)
            return str(stream.result.text).strip()

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
        self._blends: dict[str, Any] = {}

    def _voice(self, spec: str) -> Any:
        """A voice id, or a blend such as `bf_isabella:0.6+bf_emma:0.4` (weights are normalised)."""
        if "+" not in spec:
            return spec
        if spec not in self._blends:
            parts = [p.partition(":") for p in spec.split("+")]
            weights = [float(w) if w else 1.0 for _, _, w in parts]
            styles = [self._engine.get_voice_style(name) for name, _, _ in parts]
            total = sum(weights)
            self._blends[spec] = sum(
                w / total * style for w, style in zip(weights, styles, strict=True)
            )
        return self._blends[spec]

    async def synthesize_stream(self, text: str, options: TtsOptions) -> AsyncIterator[AudioChunk]:
        first_voice = options.voice.split("+")[0].partition(":")[0]
        lang = kokoro_language(first_voice, options.language)
        started = time.perf_counter()
        pauses = {
            key: float(options.extra[key])
            for key in ("sentence_pause", "clause_pause")
            if key in options.extra
        }
        async for samples, rate in self._engine.create_stream(
            text, voice=self._voice(options.voice), speed=options.speed, lang=lang, **pauses
        ):
            pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
            yield AudioChunk(pcm, int(rate), time.perf_counter() - started)
