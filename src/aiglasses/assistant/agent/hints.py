"""Which tool the first model step must call, from words in the request (data in hints.yaml)."""

import re
from importlib import resources
from pathlib import Path

import yaml

from aiglasses.assistant.intents import normalise
from aiglasses.domain.errors import ConfigError


class Hints:
    def __init__(self, groups: list[tuple[list[str], tuple[re.Pattern[str], ...]]]) -> None:
        self._groups = groups

    @classmethod
    def load(cls, path: Path | None = None) -> "Hints":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant.agent")
                .joinpath("hints.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)["hints"]
            return cls(
                [
                    (
                        [g["tool"]] if "tool" in g else list(g["chain"]),
                        tuple(re.compile(p) for p in g["patterns"]),
                    )
                    for g in raw
                ]
            )
        except (OSError, KeyError, TypeError, re.error, yaml.YAMLError) as exc:
            raise ConfigError(f"agent hints file is invalid: {type(exc).__name__}") from exc

    def plan(self, utterance: str, enabled: tuple[str, ...]) -> list[str]:
        """The tools the next model steps must call, in order (empty: the model decides)."""
        text = normalise(utterance)
        for tools, patterns in self._groups:
            if all(t in enabled for t in tools) and any(p.search(text) for p in patterns):
                return list(tools)
        return []
