"""Numbers, times and dates written the way they are said (local answers, reply cleaner)."""

from datetime import datetime

ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
ORDINAL_IRREGULAR = {
    "one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
    "nine": "ninth", "twelve": "twelfth",
}  # fmt: skip


def number_words(n: int) -> str:
    if n < 0:
        return "minus " + number_words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f" {ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return f"{ONES[n // 100]} hundred" + (f" and {number_words(rest)}" if rest else "")
    if n < 1_000_000:
        rest = n % 1000
        head = f"{number_words(n // 1000)} thousand"
        return head + (f" {number_words(rest)}" if rest else "")
    return " ".join(ONES[int(d)] for d in str(n))


def ordinal_words(n: int) -> str:
    words = number_words(n).split()
    last = words[-1]
    if last in ORDINAL_IRREGULAR:
        words[-1] = ORDINAL_IRREGULAR[last]
    elif last.endswith("y"):
        words[-1] = last[:-1] + "ieth"
    else:
        words[-1] += "th"
    return " ".join(words)


def year_words(year: int) -> str:
    if 2000 <= year < 2010:
        return "two thousand" + (f" and {number_words(year - 2000)}" if year > 2000 else "")
    tail = number_words(year % 100) if year % 100 else "hundred"
    return f"{number_words(year // 100)} {tail}"


def spoken_time(moment: datetime) -> str:
    hour12 = moment.hour % 12 or 12
    part = "in the morning" if moment.hour < 12 else "in the afternoon"
    if moment.hour >= 18:
        part = "in the evening"
    if moment.minute == 0:
        return f"{number_words(hour12)} o'clock {part}"
    minute = number_words(moment.minute)
    return f"{number_words(hour12)} {'oh ' if moment.minute < 10 else ''}{minute} {part}"


def spoken_date(moment: datetime) -> str:
    return f"{moment:%A}, the {ordinal_words(moment.day)} of {moment:%B}, {year_words(moment.year)}"
