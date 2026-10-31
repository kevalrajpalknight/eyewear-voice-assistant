"""Transcribe WHILE audio is still arriving (paced like the glasses: 20 ms every 20 ms) versus
only after the end marker. Moonshine's stream API is used for the first; every engine is timed
for the second. Usage: python -m bench.stream_bench moonshine-tiny|moonshine-base
"""

import json
import sys
import time

from bench.audio import load_mono
from bench.models import ROOT
from bench.stt_engines import MoonshineEngine

STEP = 320  # 20 ms at 16 kHz, one glasses voice frame


def run(arch_name: str, wav: str) -> dict[str, object]:
    engine = MoonshineEngine(arch_name)
    samples, rate = load_mono(ROOT / "data" / wav)
    engine.transcribe(samples[:8000])  # warm-up
    t0 = time.perf_counter()
    after_text = engine.transcribe(samples)
    after_end = time.perf_counter() - t0

    stream = engine._transcriber.create_stream()
    stream.start()
    clock = time.perf_counter()
    for index, start in enumerate(range(0, len(samples), STEP)):
        stream.add_audio(samples[start : start + STEP].tolist(), rate)
        wait = clock + (index + 1) * STEP / rate - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
    end = time.perf_counter()
    transcript = stream.stop()
    after_marker = time.perf_counter() - end
    text = " ".join(line.text.strip() for line in transcript.lines).strip()
    return {
        "engine": engine.name, "file": wav, "audio_s": round(len(samples) / rate, 2),
        "after_end_only_s": round(after_end, 3), "streaming_final_after_end_s": round(after_marker, 3),
        "same_text": text == after_text, "streaming_words": len(text.split()),
        "after_words": len(after_text.split()),
    }  # fmt: skip


if __name__ == "__main__":
    print(
        json.dumps(
            run({"moonshine-tiny": "TINY", "moonshine-base": "BASE"}[sys.argv[1]], "capture_a.wav")
        )
    )
