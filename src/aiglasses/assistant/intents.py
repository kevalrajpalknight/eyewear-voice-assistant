"""Rule-based intents loaded from a YAML file (lane 0): no model, no network, instant."""

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from aiglasses.domain.errors import ConfigError


@dataclass(frozen=True)
class Intent:
    name: str
    tool: str
    args: dict[str, Any]
    match: tuple[re.Pattern[str], ...]
    reject: tuple[re.Pattern[str], ...] = field(default=())

    def matches(self, text: str) -> bool:
        return any(p.search(text) for p in self.match) and not any(
            p.search(text) for p in self.reject
        )


def normalise(text: str) -> str:
    text = text.lower().replace(chr(0x2019), chr(39))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text)).strip()


class Intents:
    def __init__(self, intents: list[Intent]) -> None:
        self._intents = intents

    @classmethod
    def load(cls, path: Path | None = None) -> "Intents":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant")
                .joinpath("intents.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)["intents"]
            return cls(
                [
                    Intent(
                        name=name,
                        tool=body["tool"],
                        args=dict(body.get("args") or {}),
                        match=tuple(re.compile(p) for p in body["match"]),
                        reject=tuple(re.compile(p) for p in body.get("reject") or []),
                    )
                    for name, body in raw.items()
                ]
            )
        except (OSError, KeyError, TypeError, re.error, yaml.YAMLError) as exc:
            raise ConfigError(f"intents file is invalid: {type(exc).__name__}") from exc

    def find(self, utterance: str) -> Intent | None:
        text = normalise(utterance)
        return next((i for i in self._intents if i.matches(text)), None)

    def requests(self, tool: str, utterance: str) -> bool:
        """True when the user's words ask for this tool (used for state-changing tools)."""
        text = normalise(utterance)
        return any(i.tool == tool and i.matches(text) for i in self._intents)
