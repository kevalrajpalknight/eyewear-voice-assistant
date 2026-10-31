"""Copy the small, hard-to-recreate parts of bench/ into backups/bench-<UTC time>/ (gitignored).

Models are not copied (download them again with `python -m bench.models`). Nothing is overwritten
or deleted: every run makes a new folder.
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEEP = ["src", "data", "out", "pyproject.toml", "uv.lock", "models_manifest.json"]


def main() -> None:
    target = ROOT / "backups" / f"bench-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in KEEP:
        source = ROOT / "bench" / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=shutil.ignore_patterns("__pycache__"))
        elif source.exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / name)
    print(f"backed up to {target}")


if __name__ == "__main__":
    main()
