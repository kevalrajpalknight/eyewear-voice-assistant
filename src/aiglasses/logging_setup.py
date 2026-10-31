import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from rich.logging import RichHandler

from aiglasses.timefmt import stamps


class JsonLinesHandler(logging.FileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            **stamps(datetime.fromtimestamp(record.created, timezone.utc)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        self.stream = self._open() if self.stream is None else self.stream
        self.stream.write(json.dumps(entry) + "\n")
        self.flush()


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("aiglasses")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    root.addHandler(RichHandler(show_path=False, log_time_format="[%H:%M:%S %z]"))
    root.addHandler(JsonLinesHandler(log_dir / "aiglasses.jsonl", encoding="utf-8"))
