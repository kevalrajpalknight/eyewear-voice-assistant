"""Short spoken fillers picked at random so the assistant does not repeat one stock sentence."""

import random
from importlib import resources
from pathlib import Path

import yaml

from aiglasses.domain.errors import ConfigError


class Fillers:
    def __init__(self, pools: dict[str, list[str]], rng: random.Random | None = None) -> None:
        self._pools = pools
        self._rng = rng or random.Random()
        self._last: dict[str, str] = {}

    @classmethod
    def load(cls, path: Path | None = None, rng: random.Random | None = None) -> "Fillers":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant")
                .joinpath("fillers.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)
            return cls({str(k): [str(p) for p in v] for k, v in raw.items()}, rng)
        except (OSError, AttributeError, TypeError, yaml.YAMLError) as exc:
            raise ConfigError(f"fillers file is invalid: {type(exc).__name__}") from exc

    def phrases(self, pool: str) -> list[str]:
        return list(self._pools.get(pool, []))

    def pick(self, pool: str) -> str | None:
        """A phrase from the pool, never the one used last time; None when the pool is empty."""
        options = self._pools.get(pool) or []
        if not options:
            return None
        choices = [p for p in options if p != self._last.get(pool)] or options
        phrase = self._rng.choice(choices)
        self._last[pool] = phrase
        return phrase
