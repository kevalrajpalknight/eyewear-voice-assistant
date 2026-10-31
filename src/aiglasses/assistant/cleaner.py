"""Make LLM text fit for speech: no markdown, emoji, links or symbols; numbers read as words."""

import re

from aiglasses.assistant.spoken import number_words, year_words

CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
URL = re.compile(r"(?:https?://|www\.)\S+")
EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿️‍]+")
BULLET = re.compile(r"^[ \t]{0,3}(?:[-*•+]|\d+[.)])[ \t]+", re.MULTILINE)
HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
EMPHASIS = re.compile(r"[*_`~#>|]+")
DASHES = re.compile("[\u2013\u2014]")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
DECIMAL = re.compile(r"\b(\d+)\.(\d+)\b")
INTEGER = re.compile(r"\b\d{1,9}\b")
LONG_DIGITS = re.compile(r"\b\d{10,}\b")
MIN_YEAR, MAX_YEAR = 1900, 2099


def _time(match: re.Match[str]) -> str:
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return match.group(0)
    if minute == 0:
        return f"{number_words(hour)} o'clock"
    return f"{number_words(hour)} {'oh ' if minute < 10 else ''}{number_words(minute)}"


def _decimal(match: re.Match[str]) -> str:
    digits = " ".join(number_words(int(d)) for d in match.group(2))
    return f"{number_words(int(match.group(1)))} point {digits}"


def _integer(match: re.Match[str]) -> str:
    value = int(match.group(0))
    if len(match.group(0)) == 4 and MIN_YEAR <= value <= MAX_YEAR:
        return year_words(value)
    return number_words(value)


def clean_reply(text: str) -> str:
    text = CODE_BLOCK.sub(" ", text)
    text = MARKDOWN_LINK.sub(r"\1", text)
    text = URL.sub("", text)
    text = EMOJI.sub("", text)
    text = BULLET.sub("", text)
    text = HEADING.sub("", text)
    text = EMPHASIS.sub("", text)
    text = DASHES.sub(", ", text)
    text = THOUSANDS.sub("", text)
    text = CLOCK.sub(_time, text)
    text = PERCENT.sub(r"\1 percent", text)
    text = DECIMAL.sub(_decimal, text)
    text = LONG_DIGITS.sub(lambda m: " ".join(m.group(0)), text)
    text = INTEGER.sub(_integer, text)
    text = text.replace("°C", " degrees Celsius").replace("°F", " degrees Fahrenheit")
    text = text.replace("°", " degrees").replace("&", " and ")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([,.?!])", r"\1", text)
