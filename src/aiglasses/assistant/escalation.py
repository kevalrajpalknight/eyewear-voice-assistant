"""Deterministic escalation rules loaded from escalation.yaml."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib import resources
from pathlib import Path

import yaml

from aiglasses.assistant.intents import normalise
from aiglasses.domain.errors import ConfigError

MIN_WORDS_FOR_REPEAT = 3


@dataclass(frozen=True)
class EscalationRules:
    time_sensitive_patterns: tuple[re.Pattern[str], ...]
    research_patterns: tuple[re.Pattern[str], ...]
    dissatisfied_patterns: tuple[re.Pattern[str], ...]
    repeat_similarity: float
    time_sensitive_reject: tuple[re.Pattern[str], ...] = ()
    claim_patterns: tuple[re.Pattern[str], ...] = ()
    vague_query_patterns: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def load(cls, path: Path | None = None) -> "EscalationRules":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant")
                .joinpath("escalation.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)
            return cls(
                tuple(re.compile(p) for p in raw["time_sensitive"]),
                tuple(re.compile(p) for p in raw["research"]),
                tuple(re.compile(p) for p in raw["dissatisfied"]),
                float(raw["repeat_similarity"]),
                tuple(re.compile(p) for p in raw.get("time_sensitive_reject") or []),
                tuple(re.compile(p) for p in raw.get("claims_action") or []),
                tuple(re.compile(p) for p in raw.get("vague_query") or []),
            )
        except (OSError, KeyError, TypeError, ValueError, re.error, yaml.YAMLError) as exc:
            raise ConfigError(f"escalation rules are invalid: {type(exc).__name__}") from exc

    def time_sensitive(self, utterance: str) -> bool:
        text = normalise(utterance)
        if any(p.search(text) for p in self.time_sensitive_reject):
            return False
        return any(p.search(text) for p in self.time_sensitive_patterns)

    def claims_action(self, reply: str) -> bool:
        """The reply says it took a photo or searched: only true if a tool really ran."""
        text = normalise(reply)
        return any(p.search(text) for p in self.claim_patterns)

    def vague_query(self, query: str) -> bool:
        text = normalise(query)
        return not text or any(p.search(text) for p in self.vague_query_patterns)

    def research(self, utterance: str) -> bool:
        text = normalise(utterance)
        return any(p.search(text) for p in self.research_patterns)

    def dissatisfied(self, utterance: str) -> bool:
        text = normalise(utterance)
        return any(p.search(text) for p in self.dissatisfied_patterns)

    def repeated(self, utterance: str, previous: list[str]) -> bool:
        """The same question again; the caller passes only user turns inside the memory window."""
        text = normalise(utterance)
        if len(text.split()) < MIN_WORDS_FOR_REPEAT:
            return False
        return any(
            SequenceMatcher(None, text, normalise(old)).ratio() * 100 >= self.repeat_similarity
            for old in previous
        )
