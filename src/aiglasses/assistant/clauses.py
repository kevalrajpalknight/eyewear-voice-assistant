"""Cut a streaming LLM reply into speakable chunks while it is still arriving."""

from aiglasses.assistant.chunker import ends_sentence
from aiglasses.assistant.speech_chunks import MERGE_BELOW_WORDS, split_speech


class ClauseStream:
    """Feed text deltas; get chunks as soon as they are certain. The newest chunk is held back
    until a later one starts (or `finish`), because more words may still extend or merge it."""

    def __init__(self, first_min_words: int) -> None:
        self._first_min = first_min_words
        self._buffer = ""
        self._emitted = 0

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        text = self._buffer
        if text and not text[-1].isspace():
            head, _, _partial = text.rpartition(" ")
            text = head  # the last word may still be growing
        chunks = split_speech(text, self._first_min)
        last = chunks[-1].split() if chunks else []
        finished = (
            self._buffer[-1:].isspace()
            and len(last) >= MERGE_BELOW_WORDS
            and ends_sentence(last[-1])
        )
        ready = chunks[self._emitted :] if finished else chunks[self._emitted : -1]
        self._emitted += len(ready)
        return ready

    def finish(self, *, drop_unfinished: bool = False) -> list[str]:
        """The rest. With `drop_unfinished` a last chunk that stops mid-sentence is left out."""
        chunks = split_speech(self._buffer, self._first_min)
        rest = chunks[self._emitted :]
        self._emitted = len(chunks)
        if drop_unfinished and rest and not ends_sentence(rest[-1].split()[-1]):
            rest = rest[:-1]
        return rest
