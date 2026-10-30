import json
import logging
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
TARGET_FILE = LOG_DIR / "target.json"
LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        log.setLevel(logging.INFO)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        handler = logging.FileHandler(LOG_DIR / f"{name}_{stamp}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(handler)
    return log


def save_target(address: str, name: str | None) -> None:
    TARGET_FILE.write_text(json.dumps({"address": address, "name": name}), encoding="utf-8")


def load_target() -> dict:
    if not TARGET_FILE.exists():
        raise SystemExit("No target saved. Run scan.py first.")
    return json.loads(TARGET_FILE.read_text(encoding="utf-8"))


def ascii_of(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)
