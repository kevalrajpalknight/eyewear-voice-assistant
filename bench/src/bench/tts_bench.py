"""Kokoro (kokoro-onnx) and Windows SAPI timing. One process per variant for clean peak memory.

Usage: python -m bench.tts_bench kokoro-fp32|kokoro-int8|sapi
Writes WAVs to bench/out/tts/ so the voices can be judged by ear.
"""

import asyncio
import json
import subprocess
import sys
import time

import numpy as np
import psutil
import soundfile as sf

from bench.models import MODELS, ROOT

OUT = ROOT / "out" / "tts"
ONE = "The meeting is at three o'clock tomorrow afternoon."
THREE = (
    "The meeting is at three o'clock tomorrow afternoon. "
    "It should take about half an hour. "
    "Would you like me to set a reminder for you?"
)
TEXTS = {"one_sentence": ONE, "three_sentences": THREE, "three_sentences_chunked": THREE}
SPEEDS = (1.0, 1.05)
VOICE = "bf_isabella"
REPEATS = 3


async def kokoro_run(variant: str) -> list[dict[str, object]]:
    from kokoro_onnx import Kokoro

    model = "kokoro-v1.0.onnx" if variant == "kokoro-fp32" else "kokoro-v1.0.int8.onnx"
    engine = Kokoro(str(MODELS / "kokoro" / model), str(MODELS / "kokoro" / "voices-v1.0.bin"))
    results: list[dict[str, object]] = []
    async for _ in engine.create_stream("Warm up.", voice=VOICE, speed=1.0, lang="en-gb"):
        break
    for label, text in TEXTS.items():
        for speed in SPEEDS:
            firsts, totals = [], []
            pieces: list[np.ndarray] = []
            rate = 24000
            for _ in range(REPEATS):
                pieces = []
                started = time.perf_counter()
                first = None
                parts = text.split(". ") if label.endswith("_chunked") else [text]
                for part in parts:
                    part = part if part.endswith((".", "?")) else part + "."
                    async for samples, rate in engine.create_stream(  # noqa: B007
                        part, voice=VOICE, speed=speed, lang="en-gb"
                    ):
                        if first is None:
                            first = time.perf_counter() - started
                        pieces.append(samples)
                totals.append(time.perf_counter() - started)
                firsts.append(first or 0.0)
            audio = np.concatenate(pieces)
            seconds = len(audio) / rate
            OUT.mkdir(parents=True, exist_ok=True)
            sf.write(OUT / f"{variant}_{VOICE}_{label}_speed{speed}.wav", audio, rate)
            results.append({
                "variant": variant, "text": label, "speed": speed, "audio_s": round(seconds, 2),
                "first_chunk_s": round(float(np.median(firsts)), 3),
                "total_s": round(float(np.median(totals)), 3),
                "rtf": round(float(np.median(totals)) / seconds, 3),
            })  # fmt: skip
    return results


def sapi_run() -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    OUT.mkdir(parents=True, exist_ok=True)
    for label, text in TEXTS.items():
        path = OUT / f"windows_sapi_{label}.wav"
        script = (
            "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text.replace(chr(39), chr(39) * 2)}'); $s.Dispose()"
        )
        started = time.perf_counter()
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=60)
        wall = time.perf_counter() - started
        audio, rate = sf.read(path)
        seconds = len(audio) / rate
        results.append({"variant": "windows-sapi", "text": label, "audio_s": round(seconds, 2),
                        "total_s_incl_powershell_start": round(wall, 3)})  # fmt: skip
    return results


if __name__ == "__main__":
    variant = sys.argv[1]
    rows = sapi_run() if variant == "sapi" else asyncio.run(kokoro_run(variant))
    peak = round(psutil.Process().memory_info().peak_wset / 2**20)
    print(json.dumps({"variant": variant, "peak_ram_mb": peak, "rows": rows}))
