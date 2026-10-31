import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from rich.logging import RichHandler
from rich.text import Text

from aiglasses.redact import redact
from aiglasses.timefmt import stamps


def _console_time(moment: datetime) -> Text:
    """Console time with the local UTC offset always shown, e.g. [12:27:17 +05:30]."""
    local = moment.astimezone()
    offset = local.strftime("%z")
    return Text(f"[{local.strftime('%H:%M:%S')} {offset[:3]}:{offset[3:]}]")


class RedactingFilter(logging.Filter):
    """Scrub configured secret values and key-shaped strings from every record."""

    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = redact(record.getMessage(), self.secrets), None
        return True


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


def setup_logging(
    log_dir: Path, verbose: bool = False, level: str = "INFO", secrets: tuple[str, ...] = ()
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("aiglasses")
    root.setLevel(logging.DEBUG if verbose else level)
    root.handlers.clear()
    root.filters.clear()
    guard = RedactingFilter(secrets)
    for handler in (
        RichHandler(show_path=False, log_time_format=_console_time),
        JsonLinesHandler(log_dir / "aiglasses.jsonl", encoding="utf-8"),
    ):
        handler.addFilter(guard)
        root.addHandler(handler)
