"""Run one STT engine over bench/data/*.wav (with *.txt references). One process per engine so the
peak memory figure belongs to that engine alone.

Usage: python -m bench.stt_bench ENGINE [--condition raw|normalized] [--hints]

With --hints the private glossary (bench/data/glossary.txt, one term per line) is given to the
engine as a hint where it has one. bench/data/truncated.txt lists sentence ids whose recording was
cut off by the glasses before the sentence ended (also detected automatically: speech still
going in the last 100 ms); they are reported apart and kept out of the WER totals. Everything under bench/data is local and gitignored.
"""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

import jiwer
import psutil

from bench.audio import Floats, load_mono, normalize_peak, peak_dbfs
from bench.models import ROOT
from bench.stt_engines import make_engine
from bench.textnorm import clean

DATA = ROOT / "data"
REPEATS = 3


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def contains_terms(text: str, terms: list[str]) -> list[str]:
    """Glossary terms present in `text` as whole-word sequences, after normalisation."""
    padded = f" {clean(text)} "
    return [t for t in terms if f" {clean(t)} " in padded]


def cut_off(samples: Floats) -> bool:
    """Speech still going in the last 100 ms: the glasses ended the session mid-sentence."""
    if len(samples) < 16000:
        return False
    loud = abs(samples)
    return bool(loud.max() > 0 and loud[-1600:].mean() > 0.1 * loud.max())


def wer_of(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    return round(jiwer.wer([r for r, _ in pairs], [h for _, h in pairs]), 3)


def run(engine_key: str, condition: str, use_hints: bool) -> dict[str, object]:
    glossary = read_lines(DATA / "glossary.txt")
    truncated = set(read_lines(DATA / "truncated.txt"))
    hints = glossary if use_hints else None
    hint_error = ""
    process = psutil.Process()
    started = time.perf_counter()
    hotwords = None
    if use_hints and engine_key == "parakeet" and glossary:
        hotwords = Path(tempfile.mkdtemp()) / "hotwords.txt"
        hotwords.write_text("\n".join(t.upper() for t in glossary), encoding="utf-8")
    try:
        engine = make_engine(engine_key, hotwords)
    except Exception as error:  # a hint option the library rejects is a result, not a crash
        if hotwords is None:
            raise
        hint_error = f"{type(error).__name__}: {error}"
        engine, hints = make_engine(engine_key), None
    load_seconds = time.perf_counter() - started
    if use_hints and not engine.supports_hints:
        hint_error = "no hint mechanism"
    rows = []
    for wav in sorted(DATA.glob("*.wav")):
        reference = wav.with_suffix(".txt")
        if not reference.exists() or not reference.read_text().strip():
            continue
        samples, rate = load_mono(wav)
        if rate != 16000:
            raise SystemExit(f"{wav.name}: expected 16 kHz, got {rate}")
        if condition == "normalized":
            samples = normalize_peak(samples)
        seconds = len(samples) / rate
        engine.transcribe(samples[: rate // 2], hints)  # warm-up, not timed
        walls, text = [], ""
        for _ in range(REPEATS):
            t0 = time.perf_counter()
            text = engine.transcribe(samples, hints)
            walls.append(time.perf_counter() - t0)
        wall = statistics.median(walls)
        ref_text = reference.read_text(encoding="utf-8").strip()
        expected = contains_terms(ref_text, glossary)
        rows.append({
            "file": wav.name, "audio_s": round(seconds, 2),
            "peak_dbfs": round(peak_dbfs(samples), 1), "wall_s": round(wall, 3),
            "rtf": round(wall / seconds, 3), "text": text, "reference": ref_text,
            "kind": "glossary" if expected else "ordinary", "truncated": wav.stem in truncated or cut_off(samples),
            "terms_expected": expected, "terms_hit": contains_terms(text, expected),
            "wer": round(jiwer.wer(clean(ref_text), clean(text)), 3),
        })  # fmt: skip

    def pairs(kind: str | None) -> list[tuple[str, str]]:
        return [
            (clean(str(r["reference"])), clean(str(r["text"])))
            for r in rows
            if not r["truncated"] and (kind is None or r["kind"] == kind)
        ]

    expected_total = sum(len(r["terms_expected"]) for r in rows if not r["truncated"])  # type: ignore[arg-type]
    hit_total = sum(len(r["terms_hit"]) for r in rows if not r["truncated"])  # type: ignore[arg-type]
    return {
        "engine": engine.name, "key": engine_key, "condition": condition, "hints": use_hints,
        "hint_note": hint_error, "load_s": round(load_seconds, 2),
        "peak_ram_mb": round(process.memory_info().peak_wset / 2**20), "files": rows,
        "wer_all": wer_of(pairs(None)), "wer_ordinary": wer_of(pairs("ordinary")),
        "wer_glossary": wer_of(pairs("glossary")),
        "terms_expected": expected_total, "terms_hit": hit_total,
    }  # fmt: skip


if __name__ == "__main__":
    key = sys.argv[1]
    cond = sys.argv[sys.argv.index("--condition") + 1] if "--condition" in sys.argv else "raw"
    print(json.dumps(run(key, cond, "--hints" in sys.argv)))
