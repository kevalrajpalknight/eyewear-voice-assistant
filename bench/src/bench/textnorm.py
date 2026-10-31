"""Normalise reference and hypothesis text before computing word error.

Lower-case, spoken form of small numbers ("7:30" -> "seven thirty", "12th" -> "twelfth", "10" ->
"ten"), punctuation removed. Applied to both sides so numerals versus words are not errors.
"""

import re

ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
IRREGULAR_ORDINALS = {
    "one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
    "nine": "ninth", "twelve": "twelfth",
}  # fmt: skip


def number_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else "")
    return " ".join(ONES[int(d)] for d in str(n))  # 214 -> two one four


def ordinal_words(n: int) -> str:
    words = number_words(n).split()
    last = words[-1]
    if last in IRREGULAR_ORDINALS:
        words[-1] = IRREGULAR_ORDINALS[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] = last + "th"
    return " ".join(words)


def _time(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    if minute == 0:
        return number_words(hour)
    return number_words(hour) + (" oh " if minute < 10 else " ") + number_words(minute)


def clean(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _time, text)
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", lambda m: ordinal_words(int(m.group(1))), text)
    text = re.sub(r"\b\d+\b", lambda m: number_words(int(m.group(0))), text)
    text = re.sub(r"[^a-z' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
