"""Run one STT engine over bench/data/*.wav (with optional *.txt references). One process per
engine so the peak memory figure belongs to that engine alone.

Usage: python -m bench.stt_bench ENGINE [--condition raw|normalized]
"""

import json
import re
import statistics
import sys
import time

import jiwer
import psutil

from bench.audio import load_mono, normalize_peak, peak_dbfs
from bench.models import ROOT
from bench.stt_engines import make_engine

DATA = ROOT / "data"
REPEATS = 3


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip()


def run(engine_key: str, condition: str) -> dict[str, object]:
    process = psutil.Process()
    started = time.perf_counter()
    engine = make_engine(engine_key)
    load_seconds = time.perf_counter() - started
    rows = []
    references, hypotheses = [], []
    for wav in sorted(DATA.glob("*.wav")):
        samples, rate = load_mono(wav)
        if rate != 16000:
            raise SystemExit(f"{wav.name}: expected 16 kHz, got {rate}")
        if condition == "normalized":
            samples = normalize_peak(samples)
        seconds = len(samples) / rate
        engine.transcribe(samples[: rate // 2])  # warm-up, not timed
        walls, text = [], ""
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            text = engine.transcribe(samples)
            walls.append(time.perf_counter() - t0)
        wall = statistics.median(walls)
        row: dict[str, object] = {
            "file": wav.name, "audio_s": round(seconds, 2), "peak_dbfs": round(peak_dbfs(samples), 1),
            "wall_s": round(wall, 3), "rtf": round(wall / seconds, 3), "text": text,
        }  # fmt: skip
        reference = wav.with_suffix(".txt")
        if reference.exists() and reference.read_text().strip():
            ref = clean(reference.read_text())
            row["wer"] = round(jiwer.wer(ref, clean(text)), 3)
            references.append(ref)
            hypotheses.append(clean(text))
        rows.append(row)
    return {
        "engine": engine.name, "condition": condition, "load_s": round(load_seconds, 2),
        "peak_ram_mb": round(process.memory_info().peak_wset / 2**20), "files": rows,
        "overall_wer": round(jiwer.wer(references, hypotheses), 3) if references else None,
    }  # fmt: skip


if __name__ == "__main__":
    key = sys.argv[1]
    cond = sys.argv[sys.argv.index("--condition") + 1] if "--condition" in sys.argv else "raw"
    print(json.dumps(run(key, cond)))
