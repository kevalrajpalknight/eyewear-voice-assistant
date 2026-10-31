"""Speech-to-text engines under test. Each takes 16 kHz mono float32 and returns text."""

import json
from pathlib import Path
from typing import Protocol

from bench.audio import Floats
from bench.models import MODELS


class Engine(Protocol):
    name: str

    def transcribe(self, samples: Floats) -> str: ...


class MoonshineEngine:
    def __init__(self, arch_name: str) -> None:
        import moonshine_voice as m
        from moonshine_voice.transcriber import Transcriber

        files = json.loads((MODELS / "moonshine_files.json").read_text())
        folder = (MODELS / "moonshine" / files[f"{arch_name}/tokenizer.bin"]["path"]).parent
        arch = getattr(m.ModelArch, arch_name)
        self.name = f"moonshine-{arch_name.lower()}"
        self._transcriber = Transcriber(model_path=str(folder), model_arch=arch)

    def transcribe(self, samples: Floats) -> str:
        result = self._transcriber.transcribe_without_streaming(samples.tolist(), 16000)
        return " ".join(line.text.strip() for line in result.lines).strip()


class FasterWhisperEngine:
    def __init__(self, folder_name: str) -> None:
        from faster_whisper import WhisperModel

        self.name = folder_name.replace("faster-whisper-", "fw-") + "-int8"
        self._model = WhisperModel(
            str(Path(MODELS / folder_name)), device="cpu", compute_type="int8", cpu_threads=4
        )

    def transcribe(self, samples: Floats) -> str:
        segments, _info = self._model.transcribe(
            samples, language="en", beam_size=1, temperature=0.0, vad_filter=False,
            condition_on_previous_text=False,
        )  # fmt: skip
        return " ".join(s.text.strip() for s in segments).strip()


def make_engine(key: str) -> Engine:
    if key == "moonshine-tiny":
        return MoonshineEngine("TINY")
    if key == "moonshine-base":
        return MoonshineEngine("BASE")
    if key in ("fw-base.en", "fw-small.en"):
        return FasterWhisperEngine("faster-whisper-" + key.removeprefix("fw-"))
    raise SystemExit(f"unknown engine {key}")


ENGINES = ["moonshine-tiny", "moonshine-base", "fw-base.en", "fw-small.en"]
