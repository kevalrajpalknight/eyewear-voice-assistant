from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from aiglasses.domain.errors import ProtocolError
from aiglasses.domain.models import Confidence

DIRECTIONS = ("write", "notify")


@dataclass(frozen=True)
class Command:
    name: str
    characteristic: str
    payload: bytes
    direction: str
    status: Confidence
    source: str | None
    safe_to_send: bool


def _parse(entry: dict[str, Any]) -> Command:
    try:
        name = str(entry["name"])
        direction = str(entry["direction"])
        status = Confidence(entry["status"])
        payload = bytes.fromhex(str(entry["bytes"]))
        characteristic = str(entry["characteristic"]).lower()
    except (KeyError, ValueError) as exc:
        raise ProtocolError(f"invalid command entry {entry!r}: {exc}") from exc
    if direction not in DIRECTIONS:
        raise ProtocolError(f"{name}: direction must be one of {DIRECTIONS}")
    source = entry.get("source")
    if status is Confidence.CONFIRMED and not source:
        raise ProtocolError(f"{name}: CONFIRMED commands need a capture source")
    return Command(
        name=name,
        characteristic=characteristic,
        payload=payload,
        direction=direction,
        status=status,
        source=str(source) if source else None,
        safe_to_send=bool(entry.get("safe_to_send", False)),
    )


def load_commands(path: Path | None = None) -> list[Command]:
    text = (
        path.read_text(encoding="utf-8")
        if path
        else resources.files("aiglasses.protocol").joinpath("commands.yaml").read_text("utf-8")
    )
    data = yaml.safe_load(text) or {}
    return [_parse(e) for e in data.get("commands", [])]


def allowlist_from(commands: Iterable[Command]) -> list[Command]:
    """Only observed, explicitly safe write commands may be sent."""
    return [
        c
        for c in commands
        if c.direction == "write" and c.status is Confidence.CONFIRMED and c.safe_to_send
    ]
