"""Cut a streaming LLM reply into speakable clauses while it is still arriving."""

from aiglasses.assistant.chunker import split_clauses


class ClauseStream:
    """Feed text deltas; get clauses as soon as they are certain. The newest clause is held back
    until a later one starts (or `finish`), because more words may still extend it."""

    def __init__(self, first_max_words: int) -> None:
        self._first_max = first_max_words
        self._buffer = ""
        self._emitted = 0

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        text = self._buffer
        if text and not text[-1].isspace():
            head, _, _partial = text.rpartition(" ")
            text = head  # the last word may still be growing
        clauses = split_clauses(text, self._first_max)
        ready = clauses[self._emitted : -1]
        self._emitted += len(ready)
        return ready

    def finish(self) -> list[str]:
        clauses = split_clauses(self._buffer, self._first_max)
        rest = clauses[self._emitted :]
        self._emitted = len(clauses)
        return rest
