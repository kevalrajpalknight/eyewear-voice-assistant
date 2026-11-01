"""Ignore the assistant's own voice when the microphone picks it up again.

While (and just after) the assistant speaks, a request whose words are almost all words it said
within the last half minute is its own echo, not the user. The check is on text, so it works with
speakers in the room; it only applies to speech that started during or right after playback.
"""

import re
import time
from collections import deque
from collections.abc import Callable

SETTLE_S = 1.2  # after it starts talking, wait this long before a barge-in can count
WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


class EchoGuard:
    def __init__(
        self,
        *,
        window_s: float = 30.0,
        grace_s: float = 0.8,
        min_overlap: float = 0.75,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._window_s, self._grace_s, self._min_overlap = window_s, grace_s, min_overlap
        self._clock = clock
        self._said: deque[tuple[float, list[str]]] = deque()
        self._speaking_until = 0.0
        self._speaking_since = 0.0

    def settling(self) -> bool:
        """The assistant began speaking a moment ago: its echo level is not known yet."""
        now = self._clock()
        return now < self._speaking_until + self._grace_s and now - self._speaking_since < SETTLE_S

    def said(self, text: str) -> None:
        """A sentence is about to be spoken."""
        now = self._clock()
        self._said.append((now, words(text)))
        if now > self._speaking_until:
            self._speaking_since = now  # a new burst of speech, not a continuation
        self._speaking_until = max(self._speaking_until, now)

    def audio(self, seconds: float) -> None:
        """Playback time queued for what was just said."""
        self._speaking_until = max(self._speaking_until, self._clock()) + seconds

    def is_echo(self, heard: str, started_at: float) -> bool:
        if started_at > self._speaking_until + self._grace_s:
            return False
        now = self._clock()
        while self._said and now - self._said[0][0] > self._window_s:
            self._said.popleft()
        spoken = {w for _, ws in self._said for w in ws}
        heard_words = words(heard)
        if not heard_words:
            return True  # noise while the assistant was talking
        return sum(w in spoken for w in heard_words) / len(heard_words) >= self._min_overlap
