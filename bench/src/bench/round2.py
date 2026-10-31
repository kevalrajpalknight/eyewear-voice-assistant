"""Round 2: run every STT configuration over bench/data and print one combined table.

    uv run python -m bench.round2

Configurations with hints are skipped while bench/data/glossary.txt does not exist. Results go to
bench/out/round2.json (local, gitignored).
"""

import json
import subprocess
import sys

from bench.models import ROOT
from bench.stt_bench import GLOSSARY_FILE
from bench.stt_engines import ENGINES

HINTED = ["fw-base.en:prompt", "fw-base.en:hotwords", "fw-small.en:prompt", "fw-small.en:hotwords",
          "fw-small:prompt", "fw-small:hotwords", "parakeet"]  # fmt: skip


TIMEOUT_S = 900


def run_one(key: str, hints: bool) -> dict[str, object]:
    """One configuration in its own process; each finished result is kept in out/round2_parts."""
    parts = ROOT / "out" / "round2_parts"
    parts.mkdir(parents=True, exist_ok=True)
    cache = parts / f"{key.replace(':', '_')}_{'hints' if hints else 'plain'}.json"
    if cache.exists():
        return json.loads(cache.read_text())  # type: ignore[no-any-return]
    args = [sys.executable, "-m", "bench.stt_bench", key] + (["--hints"] if hints else [])
    try:
        done = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"engine": key, "hints": hints, "error": f"no result after {TIMEOUT_S} s (hung)"}
    lines = done.stderr.strip().splitlines()
    if done.returncode != 0 or not done.stdout.strip():
        return {"engine": key, "hints": hints, "error": lines[-1] if lines else "no output"}
    result: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    if not result["files"]:
        return {"engine": key, "hints": hints, "error": "no recordings found in bench/data"}
    cache.write_text(json.dumps(result))
    return result


def pct(value: object) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f}%"  # type: ignore[arg-type]


def main() -> None:
    has_glossary = GLOSSARY_FILE.exists()
    results = [run_one(k, False) for k in ENGINES]
    if has_glossary:
        results += [run_one(k, True) for k in HINTED]
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "round2.json").write_text(json.dumps(results, indent=1))
    print("| Engine | Hints | WER all | WER ordinary | WER glossary | Terms hit | Median wall s "
          "| Median RTF | Load s | Peak RAM MB | Note |")  # fmt: skip
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        if "error" in r:
            print(f"| {r['engine']} | {'yes' if r['hints'] else 'no'} | error: {r['error']} |")
            continue
        files = r["files"]
        assert isinstance(files, list) and files
        walls = sorted(f["wall_s"] for f in files)
        rtfs = sorted(f["rtf"] for f in files)
        mid = len(files) // 2
        terms = f"{r['terms_hit']}/{r['terms_expected']}" if r["terms_expected"] else "-"
        print(
            f"| {r['engine']} | {'yes' if r['hints'] else 'no'} | {pct(r['wer_all'])} | "
            f"{pct(r['wer_ordinary'])} | {pct(r['wer_glossary'])} | {terms} | {walls[mid]} | "
            f"{rtfs[mid]} | {r['load_s']} | {r['peak_ram_mb']} | {r['hint_note']} |"
        )


if __name__ == "__main__":
    main()
