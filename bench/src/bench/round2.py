"""Round 2: run every STT configuration over bench/data and print one combined table.

    uv run python -m bench.round2

Configurations with hints are skipped while bench/data/glossary.txt does not exist. Results go to
bench/out/round2.json (local, gitignored).
"""

import json
import subprocess
import sys

from bench.models import ROOT
from bench.stt_engines import ENGINES

HINTED = ["fw-base.en:prompt", "fw-base.en:hotwords", "fw-small.en:prompt", "fw-small.en:hotwords",
          "fw-small:prompt", "fw-small:hotwords", "parakeet"]  # fmt: skip


def run_one(key: str, hints: bool) -> dict[str, object]:
    args = [sys.executable, "-m", "bench.stt_bench", key] + (["--hints"] if hints else [])
    done = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    if done.returncode != 0:
        return {"engine": key, "hints": hints, "error": done.stderr.strip().splitlines()[-1]}
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


def pct(value: object) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f}%"  # type: ignore[arg-type]


def main() -> None:
    has_glossary = (ROOT / "data" / "glossary.txt").exists()
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
        assert isinstance(files, list)
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
