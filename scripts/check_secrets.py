"""Pre-commit guard: refuse .env files and strings that look like API keys. Prints no secret."""

import re
import sys
from pathlib import Path

KEY_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-or-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\b[A-Z_]*API_KEY\s*=\s*[\"']?[A-Za-z0-9_\-]{20,}"),
]
ALLOWED_ENV_NAMES = {".env.example"}


def problems(path: Path) -> list[str]:
    found = []
    if path.name.startswith(".env") and path.name not in ALLOWED_ENV_NAMES:
        found.append(f"{path}: environment files must not be committed")
        return found
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return found
    for number, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in KEY_PATTERNS):
            found.append(f"{path}:{number}: looks like an API key (value not shown)")
    return found


def main(paths: list[str]) -> int:
    failures = [msg for p in paths for msg in problems(Path(p))]
    for message in failures:
        print(message, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
