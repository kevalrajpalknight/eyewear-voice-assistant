"""Re-score stored transcripts (no hints) against the current glossary without re-running engines.

    uv run python -m bench.rescore          # rewrites the plain parts in out/round2_parts

Needed when the glossary file changes after a run: kinds (ordinary or glossary), term counts and
the WER splits all depend on it. Hinted parts are not rescored (the hint itself changed).
"""

import json

import jiwer

from bench.models import ROOT
from bench.stt_bench import GLOSSARY_FILE, contains_terms, read_terms
from bench.textnorm import clean


def main() -> None:
    glossary = read_terms(GLOSSARY_FILE)
    for part in sorted((ROOT / "out" / "round2_parts").glob("*_plain.json")):
        result = json.loads(part.read_text())
        for row in result["files"]:
            expected = contains_terms(row["reference"], glossary)
            row["kind"] = "glossary" if expected else "ordinary"
            row["terms_expected"] = expected
            row["terms_hit"] = contains_terms(row["text"], expected)
        kept = [r for r in result["files"] if not r["truncated"]]

        def wer(kind: str | None, kept: list[dict] = kept) -> float | None:  # type: ignore[type-arg]
            pairs = [
                (clean(r["reference"]), clean(r["text"])) for r in kept if kind in (None, r["kind"])
            ]
            return (
                round(jiwer.wer([a for a, _ in pairs], [b for _, b in pairs]), 3) if pairs else None
            )

        result.update(
            wer_all=wer(None), wer_ordinary=wer("ordinary"), wer_glossary=wer("glossary"),
            terms_expected=sum(len(r["terms_expected"]) for r in kept),
            terms_hit=sum(len(r["terms_hit"]) for r in kept),
        )  # fmt: skip
        part.write_text(json.dumps(result))
        print(part.name, result["wer_all"], result["wer_ordinary"], result["wer_glossary"])


if __name__ == "__main__":
    main()
