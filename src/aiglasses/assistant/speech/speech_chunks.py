"""Cut a reply into chunks that sound natural when spoken one after another.

Tiny fragments ("One moment, let me think about" / "that.") make synthesised speech choppy, so:
- the first chunk is the first sentence, or a comma boundary once `first_min_words` have passed;
- very short sentences are merged into their neighbour;
- later chunks are whole sentences; only a very long sentence is split, at a comma.
The caller holds back the newest chunk until the next one starts (one-sentence look-ahead).
"""

from aiglasses.assistant.speech.chunker import ends_sentence

MERGE_BELOW_WORDS = 4  # a sentence shorter than this joins the next one
LONG_SENTENCE_WORDS = 28  # only sentences longer than this are split at a comma
MIN_PIECE_WORDS = 8  # a split piece is never shorter than this


def sentences_of(text: str) -> list[list[str]]:
    sentences: list[list[str]] = []
    current: list[str] = []
    for word in text.split():
        current.append(word)
        if ends_sentence(word):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def _merge_short(sentences: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    carry: list[str] = []
    for sentence in sentences:
        combined = carry + sentence
        if len(combined) < MERGE_BELOW_WORDS:
            carry = combined
            continue
        merged.append(combined)
        carry = []
    if carry:  # a short ending stays its own chunk: earlier chunks may already be spoken
        merged.append(carry)
    return merged


def _comma_cut(words: list[str], minimum: int) -> int | None:
    """Index just after the first comma-ended word at position >= minimum, else None."""
    for index, word in enumerate(words):
        if index + 1 >= minimum and word.endswith((",", ";", ":")) and index + 1 < len(words):
            return index + 1
    return None


def _split_long(words: list[str]) -> list[list[str]]:
    pieces: list[list[str]] = []
    while len(words) > LONG_SENTENCE_WORDS:
        cut = _comma_cut(words, MIN_PIECE_WORDS)
        if cut is None or len(words) - cut < MIN_PIECE_WORDS:
            break
        pieces.append(words[:cut])
        words = words[cut:]
    pieces.append(words)
    return pieces


def split_speech(text: str, first_min_words: int) -> list[str]:
    sentences = _merge_short(sentences_of(text))
    chunks: list[list[str]] = []
    for index, sentence in enumerate(sentences):
        if index == 0:
            cut = _comma_cut(sentence, first_min_words)
            if cut is not None:
                chunks.append(sentence[:cut])
                chunks.extend(_split_long(sentence[cut:]))
                continue
        chunks.extend(_split_long(sentence))
    return [" ".join(chunk) for chunk in chunks]
