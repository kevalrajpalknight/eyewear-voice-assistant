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


def read_learned(path: Path | None) -> list[str]:
    """Names your own voice produced during `assistant wake-train` (a small local file)."""
    if path is None or not path.exists():
        return []
    try:
        names = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("names") or []
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"learned wake names file is invalid: {type(exc).__name__}") from exc
    return [str(n).lower() for n in names]


def save_learned(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"names": sorted(set(names))}), encoding="utf-8")


def learn_names(transcripts: list[str], greetings: list[str]) -> list[str]:
    """The word each transcript used for the name: after an optional greeting, the first word."""
    found: list[str] = []
    for text in transcripts:
        words = re.findall(r"[a-z']+", text.lower())
        if words and words[0] in greetings and len(words) > 1:
            words = words[1:]
        if words:
            found.append(words[0])
    return found


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
    def load(cls, path: Path | None = None, learned: Path | None = None) -> "WakePhrases":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant")
                .joinpath("wake_phrases.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)
            names = list(raw["names"]) + read_learned(learned)
            return cls(names, list(raw["greetings"]), list(raw["listen_words"]))
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise ConfigError(f"wake phrases file is invalid: {type(exc).__name__}") from exc

    def strip(self, text: str) -> WakeResult:
        found = self._pattern.match(text)
        if found is None:
            return WakeResult(text.strip(), False, False)
        rest = text[found.end() :].strip()
        return WakeResult(rest, True, not rest)
