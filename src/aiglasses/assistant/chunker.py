"""Split text into short clauses so the first one reaches the speech engine quickly.

Kokoro takes about 0.5 s of compute per second of speech, so a first chunk of 3 to 6 words (1 to
2 s of speech) starts sounding in well under a second, while a whole sentence takes about 1.4 s.
"""

import re

SENTENCE_END = re.compile(r"[.?!]+[\"')\]]*$")
CLAUSE_END = re.compile(r"[,;:]+[\"')\]]*$")
DOTTED_ABBREVIATION = re.compile(r"^(?:[A-Za-z]{1,2}\.){2,}$")  # p.m., e.g.
TITLES = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "no."}
CONJUNCTIONS = {"and", "but", "so", "because", "then", "or", "which", "while", "when"}

FIRST_MIN = 3
MIN_WORDS, MAX_WORDS = 4, 10


def _ends_sentence(word: str) -> bool:
    if word.lower() in TITLES or DOTTED_ABBREVIATION.match(word):
        return False
    return SENTENCE_END.search(word) is not None


def split_clauses(text: str, first_max_words: int) -> list[str]:
    """Cut at sentence ends and commas, before conjunctions in long runs, and at a word limit."""
    words = text.split()
    clauses: list[list[str]] = []
    current: list[str] = []
    for index, word in enumerate(words):
        current.append(word)
        first = not clauses
        low, high = (FIRST_MIN, first_max_words) if first else (MIN_WORDS, MAX_WORDS)
        following = words[index + 1].lower().strip(",.") if index + 1 < len(words) else ""
        if _ends_sentence(word):
            cut = True
        elif CLAUSE_END.search(word):
            cut = len(current) >= low
        elif following in CONJUNCTIONS and len(current) >= max(low, 4 if first else 6):
            cut = True
        else:
            cut = len(current) >= high
        if cut:
            clauses.append(current)
            current = []
    if current:
        if clauses and len(current) == 1 and not _ends_sentence(clauses[-1][-1]):
            clauses[-1].extend(current)
        else:
            clauses.append(current)
    return [" ".join(c) for c in clauses]
