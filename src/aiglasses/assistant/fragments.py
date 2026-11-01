"""Sounds and one-word replies that are not requests ("okay", "um", "?"), and thanks."""

from importlib import resources
from pathlib import Path

import yaml

from aiglasses.assistant.intents import normalise
from aiglasses.domain.errors import ConfigError


class Fragments:
    def __init__(self, ignore: set[str], thanks: set[str]) -> None:
        self._ignore, self._thanks = ignore, thanks

    @classmethod
    def load(cls, path: Path | None = None) -> "Fragments":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant")
                .joinpath("fragments.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)
            return cls({str(w) for w in raw["ignore"]}, {str(w) for w in raw["thanks"]})
        except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            raise ConfigError(f"fragments file is invalid: {type(exc).__name__}") from exc

    def kind(self, text: str) -> str | None:
        """ "ignore" (no answer wanted), "thanks" (a short fixed reply), or None (a request)."""
        words = normalise(text)
        if not words or words in self._ignore:
            return "ignore"
        if words in self._thanks:
            return "thanks"
        return None
