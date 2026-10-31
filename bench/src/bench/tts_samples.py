"""Five assistant-style replies through the production TTS settings, saved as WAVs to listen to.

    uv run python -m bench.tts_samples [--glossary-term "RapDuo SM"]

Kokoro int8, voice bf_isabella, speed 1.05, split into clauses exactly like the echo loop. Nothing
is played; files go to bench/out/tts_samples (local, gitignored).
"""

import asyncio
import importlib.util
import sys
import time

import numpy as np
import soundfile as sf

from bench.models import MODELS, ROOT

OUT = ROOT / "out" / "tts_samples"
TEMPLATES = [
    "Sure. The meeting is at three o'clock tomorrow afternoon, and it should take about half an hour.",
    "I found a few results about {term}. Do you want me to read the first one?",
    "It is about twenty two degrees and sunny right now, so you probably will not need a jacket.",
    "Sorry, I did not catch that. Could you say it again?",
    "Your timer for ten minutes is set. I will remind you to take the bread out.",
]


def load_chunker():  # type: ignore[no-untyped-def]
    path = ROOT.parent / "src" / "aiglasses" / "assistant" / "chunker.py"
    spec = importlib.util.spec_from_file_location("chunker_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["chunker_module"] = module
    spec.loader.exec_module(module)
    return module


async def main() -> None:
    from kokoro_onnx import Kokoro

    term = (
        sys.argv[sys.argv.index("--glossary-term") + 1]
        if "--glossary-term" in sys.argv
        else "RapDuo SM"
    )
    split_clauses = load_chunker().split_clauses
    engine = Kokoro(
        str(MODELS / "kokoro" / "kokoro-v1.0.int8.onnx"), str(MODELS / "kokoro" / "voices-v1.0.bin")
    )
    OUT.mkdir(parents=True, exist_ok=True)
    async for _ in engine.create_stream("Warm up.", voice="bf_isabella", speed=1.05, lang="en-gb"):
        break
    for number, template in enumerate(TEMPLATES, start=1):
        text = template.format(term=term)
        pieces: list[np.ndarray] = []
        first = None
        started = time.perf_counter()
        for clause in split_clauses(text):
            async for samples, _rate in engine.create_stream(
                clause, voice="bf_isabella", speed=1.05, lang="en-gb"
            ):
                if first is None:
                    first = time.perf_counter() - started
                pieces.append(samples)
        audio = np.concatenate(pieces)
        total = time.perf_counter() - started
        path = OUT / f"sample{number}.wav"
        sf.write(path, audio, 24000)
        print(
            f"{path.name}: first audio {first:.2f}s, total {total:.2f}s for {len(audio) / 24000:.1f}s of speech"
        )


if __name__ == "__main__":
    asyncio.run(main())
