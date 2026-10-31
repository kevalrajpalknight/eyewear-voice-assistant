"""Run every STT engine over bench/data (raw and peak-normalised) and print one combined table.

Only recordings with a reference text (.txt next to the .wav) are used. Usage:
    uv run python -m bench.table
Results are also saved to bench/out/stt_table.json.
"""

import json
import subprocess
import sys

from bench.models import ROOT
from bench.stt_engines import ENGINES

CONDITIONS = ("raw", "normalized")


def run_one(engine: str, condition: str) -> dict[str, object]:
    done = subprocess.run(
        [sys.executable, "-m", "bench.stt_bench", engine, "--condition", condition],
        capture_output=True, text=True, cwd=ROOT, check=True,
    )  # fmt: skip
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


def main() -> None:
    results = [run_one(e, c) for e in ENGINES for c in CONDITIONS]
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "stt_table.json").write_text(json.dumps(results, indent=1))
    print("| Engine | Input | Files | WER | Median wall s | Median RTF | Peak RAM MB |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        files = r["files"]
        assert isinstance(files, list)
        walls = sorted(f["wall_s"] for f in files)
        rtfs = sorted(f["rtf"] for f in files)
        mid = len(files) // 2
        wer = r["overall_wer"]
        print(
            f"| {r['engine']} | {r['condition']} | {len(files)} | "
            f"{'-' if wer is None else f'{float(wer) * 100:.1f}%'} | {walls[mid]} | {rtfs[mid]} | "
            f"{r['peak_ram_mb']} |"
        )
    print("\nPer-sentence WER (raw input):")
    raw = [r for r in results if r["condition"] == "raw"]
    names = [f["file"] for f in raw[0]["files"]]  # type: ignore[index]
    print("| Sentence | " + " | ".join(str(r["engine"]) for r in raw) + " |")
    print("|---|" + "---|" * len(raw))
    for i, name in enumerate(names):
        cells = [str(r["files"][i].get("wer", "-")) for r in raw]  # type: ignore[index]
        print(f"| {name} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
