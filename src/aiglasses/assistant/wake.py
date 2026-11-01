"""Wake phrases: "EVA ...", "hey EVA ...", "hello EVA ...", "EVA listen ...".

The phrase is stripped before routing. A request that is only the wake phrase gets a short
acknowledgement. The list of names (with common mis-hearings) is data in wake_phrases.yaml.
"""

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from aiglasses.domain.errors import ConfigError


@dataclass(frozen=True)
class WakeResult:
    text: str  # what is left after the wake phrase
    woke: bool
    only_wake: bool


class WakePhrases:
    def __init__(self, names: list[str], greetings: list[str], listen_words: list[str]) -> None:
        name = "|".join(re.escape(n) for n in names)
        greet = "|".join(re.escape(g) for g in greetings)
        listen = "|".join(re.escape(w) for w in listen_words)
        self._pattern = re.compile(
            rf"^\s*(?:(?:{greet})[\s,.!]+)?(?:{name})\b[\s,.!:;-]*(?:(?:{listen})\b[\s,.!:;-]*)?",
            re.IGNORECASE,
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "WakePhrases":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant")
                .joinpath("wake_phrases.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)
            return cls(list(raw["names"]), list(raw["greetings"]), list(raw["listen_words"]))
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise ConfigError(f"wake phrases file is invalid: {type(exc).__name__}") from exc

    def strip(self, text: str) -> WakeResult:
        found = self._pattern.match(text)
        if found is None:
            return WakeResult(text.strip(), False, False)
        rest = text[found.end() :].strip()
        return WakeResult(rest, True, not rest)
