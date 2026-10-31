"""Measure the glossary corrector offline on the transcripts in bench/out/round2.json.

    uv run python -m bench.glossary_eval [--threshold 88] [--phonetic 72]

Reports, per engine (hints off): glossary terms recognised before and after correction, WER
before and after, and the false-correction rate on ordinary sentences. Every correction is listed.
The glossary is private (bench/data/glossary.txt); output stays under bench/out.
"""

import importlib.util
import json
import sys

import jiwer

from bench.models import ROOT
from bench.stt_bench import contains_terms, read_lines
from bench.textnorm import clean


def load_glossary_module():  # type: ignore[no-untyped-def]
    """Import the library module by path: the package __init__ pulls in the BLE stack."""
    path = ROOT.parent / "src" / "aiglasses" / "assistant" / "glossary.py"
    spec = importlib.util.spec_from_file_location("glossary_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["glossary_module"] = module
    spec.loader.exec_module(module)
    return module


def option(name: str, default: float) -> float:
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main() -> None:
    module = load_glossary_module()
    lines = read_lines(ROOT / "data" / "glossary.txt")
    if not lines:
        raise SystemExit("bench/data/glossary.txt is missing or empty")
    glossary = module.Glossary(
        module.parse_glossary(lines),
        fuzzy_threshold=option("--threshold", 88.0),
        phonetic_threshold=option("--phonetic", 72.0),
    )
    terms = [t.canonical for t in glossary.terms]
    results = json.loads((ROOT / "out" / "round2.json").read_text())
    print("| Engine | Terms before | Terms after | Wrong terms added | WER glossary before -> after "
          "| Ordinary sentences changed | WER ordinary before -> after |")  # fmt: skip
    print("|---|---|---|---|---|---|---|")
    log = []
    for r in results:
        if r.get("hints") or "files" not in r:
            continue
        rows = [f for f in r["files"] if not f["truncated"]]
        before = after = wrong = expected = 0
        pairs = {"ordinary": ([], [], []), "glossary": ([], [], [])}
        changed_ordinary = 0
        for f in rows:
            corrected, found = glossary.correct(f["text"])
            expect = f["terms_expected"]
            expected += len(expect)
            before += len(contains_terms(f["text"], expect))
            after += len(contains_terms(corrected, expect))
            wrong += len([t for t in contains_terms(corrected, terms) if t not in expect])
            refs, raw, fixed = pairs[f["kind"]]
            refs.append(clean(f["reference"]))
            raw.append(clean(f["text"]))
            fixed.append(clean(corrected))
            if f["kind"] == "ordinary" and found:
                changed_ordinary += 1
            for c in found:
                log.append((r["engine"], f["kind"], f["file"], c.original, c.corrected, c.score))
        n_ord = len(pairs["ordinary"][0])

        def wers(kind: str, pairs: dict = pairs) -> str:  # type: ignore[type-arg]
            refs, raw, fixed = pairs[kind]
            if not refs:
                return "-"
            return f"{jiwer.wer(refs, raw) * 100:.1f}% -> {jiwer.wer(refs, fixed) * 100:.1f}%"

        print(f"| {r['engine']} | {before}/{expected} | {after}/{expected} | {wrong} | "
              f"{wers('glossary')} | {changed_ordinary}/{n_ord} | {wers('ordinary')} |")  # fmt: skip
    print("\nEvery correction (engine, kind, file, original -> corrected, score):")
    for engine, kind, file, original, corrected, score in log:
        print(f"- {engine} {kind} {file}: {original!r} -> {corrected!r} ({score:.0f})")


if __name__ == "__main__":
    main()
