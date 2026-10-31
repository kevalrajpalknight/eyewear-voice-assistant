"""Speech-to-text engines under test. Each takes 16 kHz mono float32 and returns text.

`hints` is the user's glossary. Engines without a hint mechanism ignore it and say so through
`supports_hints`.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from bench.audio import Floats
from bench.models import MODELS


class Engine(Protocol):
    name: str
    supports_hints: bool

    def transcribe(self, samples: Floats, hints: Sequence[str] | None = None) -> str: ...


def _moonshine_folder(arch_name: str) -> Path:
    base = MODELS / "moonshine" / "download.moonshine.ai" / "model"
    pattern = {
        "TINY": "tiny-en/quantized/tiny-en",
        "BASE": "base-en/quantized/base-en",
        "SMALL_STREAMING": "small-streaming-en/quantized_*",
        "MEDIUM_STREAMING": "medium-streaming-en/quantized_*",
    }[arch_name]
    found = sorted(base.glob(pattern))
    if not found:
        raise SystemExit(f"Moonshine {arch_name} is not downloaded under {base}")
    return found[-1]


class MoonshineEngine:
    supports_hints = False

    def __init__(self, arch_name: str) -> None:
        import moonshine_voice as m
        from moonshine_voice.transcriber import Transcriber

        self.name = "moonshine-" + arch_name.lower().replace("_streaming", "-v2").replace("_", "-")
        self._transcriber = Transcriber(
            model_path=str(_moonshine_folder(arch_name)), model_arch=getattr(m.ModelArch, arch_name)
        )

    def transcribe(self, samples: Floats, hints: Sequence[str] | None = None) -> str:
        result = self._transcriber.transcribe_without_streaming(samples.tolist(), 16000)
        return " ".join(line.text.strip() for line in result.lines).strip()


class FasterWhisperEngine:
    supports_hints = True

    def __init__(self, folder_name: str, hint_mode: str = "prompt") -> None:
        from faster_whisper import WhisperModel

        self.name = folder_name.replace("faster-whisper-", "fw-") + "-int8"
        self._hint_mode = hint_mode
        self._model = WhisperModel(
            str(Path(MODELS / folder_name)), device="cpu", compute_type="int8", cpu_threads=4
        )
        self._english_only = folder_name.endswith(".en")

    def transcribe(self, samples: Floats, hints: Sequence[str] | None = None) -> str:
        options: dict[str, Any] = {}
        if hints and self._hint_mode == "prompt":
            options["initial_prompt"] = "Names and terms: " + ", ".join(hints) + "."
        elif hints and self._hint_mode == "hotwords":
            options["hotwords"] = " ".join(hints)
        segments, _info = self._model.transcribe(
            samples, language="en", beam_size=1, temperature=0.0, vad_filter=False,
            condition_on_previous_text=False, **options,
        )  # fmt: skip
        return " ".join(s.text.strip() for s in segments).strip()


class ParakeetEngine:
    """Parakeet TDT 0.6B v2, int8 ONNX, through sherpa-onnx. Hints are tried as sherpa hotwords."""

    supports_hints = True

    def __init__(self, hotwords_file: Path | None = None) -> None:
        import onnxruntime  # noqa: F401  (loads the pip DLL before sherpa-onnx finds the older system one)
        import sherpa_onnx

        folder = MODELS / "parakeet-tdt-0.6b-v2-int8"
        self.name = "parakeet-tdt-v2-int8"
        extra: dict[str, Any] = {}
        if hotwords_file is not None:
            extra = {
                "decoding_method": "modified_beam_search", "hotwords_file": str(hotwords_file),
                "hotwords_score": 2.0, "modeling_unit": "bpe",
                "bpe_vocab": str(folder / "tokens.txt"),
            }  # fmt: skip
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder.int8.onnx"), decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"), tokens=str(folder / "tokens.txt"),
            num_threads=4, model_type="nemo_transducer", **extra,
        )  # fmt: skip

    def transcribe(self, samples: Floats, hints: Sequence[str] | None = None) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        self._recognizer.decode_stream(stream)
        return str(stream.result.text).strip()


def make_engine(key: str, hotwords_file: Path | None = None) -> Engine:
    """Keys: moonshine-tiny|base|small-v2|medium-v2, parakeet, fw-<model>[:prompt|:hotwords]."""
    moonshine = {
        "moonshine-tiny": "TINY", "moonshine-base": "BASE",
        "moonshine-small-v2": "SMALL_STREAMING", "moonshine-medium-v2": "MEDIUM_STREAMING",
    }  # fmt: skip
    if key in moonshine:
        return MoonshineEngine(moonshine[key])
    if key == "parakeet":
        return ParakeetEngine(hotwords_file)
    name, _, mode = key.partition(":")
    if name in ("fw-base.en", "fw-small.en", "fw-small"):
        return FasterWhisperEngine("faster-whisper-" + name.removeprefix("fw-"), mode or "prompt")
    raise SystemExit(f"unknown engine {key}")


ENGINES = [
    "moonshine-tiny", "moonshine-base", "moonshine-small-v2", "moonshine-medium-v2", "parakeet",
    "fw-base.en", "fw-small.en", "fw-small",
]  # fmt: skip
