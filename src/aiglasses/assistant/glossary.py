"""The user's own words (names, brands, projects) and a conservative post-STT corrector for them.

The glossary is a private text file, one term per line, never committed. A line may add spoken
forms after a pipe: `RapDuo SM | rap duo s m, rap do`. Heavy imports (rapidfuzz, jellyfish) are
optional extra `assistant`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

WORD = re.compile(r"[A-Za-z0-9']+")
MAX_EXTRA_WORDS = 1
# Function words are never the edge of a corrected span, so ordinary phrasing is left alone.
_COMMON_WORDS = """
    a about after again all also am an and any are as at be been but by can could did do
    does for from get go going had has have he her here him his how i if in into is it its
    just me my no not of off on one or our out over play please she so some than that the
    their them then there these they this to too up us was we what when where which who why
    will with would yes you your
"""
COMMON = frozenset(_COMMON_WORDS.split())


@dataclass(frozen=True)
class Correction:
    original: str
    corrected: str
    score: float
    reason: str  # "spacing/case" | "fuzzy" | "phonetic"


@dataclass(frozen=True)
class Term:
    canonical: str
    forms: tuple[str, ...]  # the canonical text first, then user-given spoken forms


def _key(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _phonetic(text: str) -> str:
    import jellyfish

    return " ".join(jellyfish.metaphone(w) for w in WORD.findall(text.lower()))


def parse_glossary(lines: Sequence[str]) -> list[Term]:
    terms = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        canonical, _, spoken = line.partition("|")
        canonical = canonical.strip()
        forms = [canonical, *(s.strip() for s in spoken.split(",") if s.strip())]
        terms.append(Term(canonical, tuple(forms)))
    return terms


def glossary_warnings(lines: Sequence[str]) -> list[str]:
    """Lines that probably do not mean what they say (the term text is never repeated)."""
    return [
        f"glossary line {number}: more than one '|'. Spoken forms go after ONE '|', separated "
        "by commas; put separate terms on separate lines"
        for number, line in enumerate(lines, start=1)
        if line.count("|") > 1 and not line.lstrip().startswith("#")
    ]


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    text: str


class Glossary:
    def __init__(
        self,
        terms: Sequence[Term],
        *,
        fuzzy_threshold: float = 88.0,
        phonetic_threshold: float = 72.0,
        min_letters: int = 4,
    ) -> None:
        self.terms = list(terms)
        self.fuzzy_threshold = fuzzy_threshold
        self.phonetic_threshold = phonetic_threshold
        self.min_letters = min_letters
        self._forms = [(t, _key(f), _phonetic(f)) for t in self.terms for f in t.forms]
        self._max_words = max(
            (len(WORD.findall(f)) for t in self.terms for f in t.forms), default=1
        )

    @classmethod
    def load(cls, path: Path, *, fuzzy_threshold: float = 88.0) -> "Glossary":
        lines = path.read_text(encoding="utf-8").splitlines()
        return cls(parse_glossary(lines), fuzzy_threshold=fuzzy_threshold)

    def __bool__(self) -> bool:
        return bool(self.terms)

    def _tokens(self, text: str) -> list[_Token]:
        return [_Token(m.start(), m.end(), m.group()) for m in WORD.finditer(text)]

    def _windows(self, text: str, tokens: list[_Token]) -> list[tuple[int, int]]:
        """Token index ranges whose gaps hold only spaces or hyphens (no sentence punctuation)."""
        spans = []
        for i in range(len(tokens)):
            for n in range(1, self._max_words + MAX_EXTRA_WORDS + 1):
                j = i + n
                if j > len(tokens):
                    break
                if n > 1 and not re.fullmatch(
                    r"[ \-]+", text[tokens[j - 2].end : tokens[j - 1].start]
                ):
                    break
                spans.append((i, j))
        return spans

    def _best(self, window: str) -> tuple[Term, float, str] | None:
        key = _key(window)
        words = WORD.findall(window.lower())
        if len(key) < self.min_letters or words[0] in COMMON or words[-1] in COMMON:
            return None
        phon = _phonetic(window)
        best: tuple[Term, float, str] | None = None
        for term, form_key, form_phon in self._forms:
            if len(form_key) < self.min_letters or abs(len(key) - len(form_key)) > max(
                2, len(form_key) // 4
            ):
                continue
            if key == form_key:
                found = (term, 100.0, "spacing/case")
            else:
                score = float(fuzz.ratio(key, form_key))
                if score >= self.fuzzy_threshold:
                    found = (term, score, "fuzzy")
                elif phon == form_phon and score >= self.phonetic_threshold:
                    found = (term, score, "phonetic")
                else:
                    continue
            if best is None or found[1] > best[1]:
                best = found
        return best

    def correct(self, text: str) -> tuple[str, list[Correction]]:
        if not self.terms:
            return text, []
        tokens = self._tokens(text)
        candidates = []
        for i, j in self._windows(text, tokens):
            window = text[tokens[i].start : tokens[j - 1].end]
            hit = self._best(window)
            if hit is not None and hit[0].canonical != window:
                candidates.append((hit[1], j - i, i, j, window, hit))
        candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))
        taken: set[int] = set()
        chosen = []
        for score, _n, i, j, window, (term, _s, reason) in candidates:
            if taken.intersection(range(i, j)):
                continue
            taken.update(range(i, j))
            chosen.append((i, j, Correction(window, term.canonical, score, reason)))
        corrected = text
        corrections = []
        for i, j, correction in sorted(chosen, key=lambda c: -c[0]):
            corrected = (
                corrected[: tokens[i].start] + correction.corrected + corrected[tokens[j - 1].end :]
            )
            corrections.append(correction)
        corrections.reverse()
        return corrected, corrections

    def prompt_note(self) -> str:
        """Text for the LLM system prompt. Built from the glossary at runtime, never stored."""
        if not self.terms:
            return ""
        names = ", ".join(t.canonical for t in self.terms)
        return (
            "The user's words come from speech recognition and may contain mistakes. When a word "
            "or phrase sounds like one of the user's own terms, interpret it as that term. "
            f"The user's terms: {names}."
        )
