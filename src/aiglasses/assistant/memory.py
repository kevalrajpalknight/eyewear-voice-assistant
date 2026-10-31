"""Short-term memory: RAM only, one format for every lane.

Limits (whichever hits first): MEMORY_MAX_TURNS exchanges, MEMORY_TTL_S seconds of age. Expiry is
silent. Tool calls and search results are stored as compact one-line notes. Interrupted replies are
flagged. `forget` clears memory and tells listeners (for example the turn joiner) to reset.
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from aiglasses.assistant.interfaces import Message

INTERRUPTED_NOTE = " [the user interrupted before the end]"
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4


@dataclass
class Exchange:
    user: str
    assistant: str
    interrupted: bool = False
    turn: int = 0
    notes: list[str] = field(default_factory=list)  # one line each, e.g. "read_battery: 83 percent"
    at: float = 0.0


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + MESSAGE_OVERHEAD_TOKENS


class Memory:
    def __init__(
        self,
        max_turns: int,
        ttl_s: float,
        max_tokens: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_turns, self.ttl_s, self.max_tokens = max_turns, ttl_s, max_tokens
        self._clock = clock
        self._items: deque[Exchange] = deque()
        self._on_forget: list[Callable[[], None]] = []

    @property
    def turns(self) -> int:
        """Old name of the turn limit."""
        return self.max_turns

    def on_forget(self, listener: Callable[[], None]) -> None:
        self._on_forget.append(listener)

    def _expire(self) -> None:
        now = self._clock()
        while self._items and now - self._items[0].at > self.ttl_s:
            self._items.popleft()
        while len(self._items) > max(self.max_turns, 0):
            self._items.popleft()

    def add(self, exchange: Exchange) -> None:
        if self.max_turns <= 0:
            return
        exchange.at = self._clock()
        self._items.append(exchange)
        self._expire()

    def drop_turn(self, turn: int) -> bool:
        """Remove the newest exchange if it belongs to `turn` (a request that gets re-asked)."""
        self._expire()
        if self._items and self._items[-1].turn == turn:
            self._items.pop()
            return True
        return False

    def forget(self, scope: str) -> int:
        """`last_exchange` or `everything`. Returns how many exchanges were removed."""
        self._expire()
        if scope == "last_exchange":
            removed = 1 if self._items else 0
            if removed:
                self._items.pop()
        else:
            removed = len(self._items)
            self._items.clear()
        for listener in self._on_forget:
            listener()
        return removed

    @property
    def last(self) -> Exchange | None:
        self._expire()
        return self._items[-1] if self._items else None

    def exchanges(self) -> list[Exchange]:
        self._expire()
        return list(self._items)

    def __len__(self) -> int:
        self._expire()
        return len(self._items)

    def messages(self) -> list[Message]:
        found: list[Message] = []
        for item in self.exchanges():
            found.append(Message("user", item.user))
            reply = item.assistant
            if item.notes:
                reply += " (" + "; ".join(item.notes) + ")"
            found.append(
                Message("assistant", reply + (INTERRUPTED_NOTE if item.interrupted else ""))
            )
        return found


def fit_tokens(messages: list[Message], max_tokens: int) -> list[Message]:
    """Drop the oldest exchanges until the estimate fits. The system prompt (first) and the final
    user message (last) are always kept."""
    if len(messages) <= 2:
        return messages
    head, body, tail = messages[0], messages[1:-1], messages[-1]
    fixed = estimate_tokens(head.content) + estimate_tokens(tail.content)
    while body and fixed + sum(estimate_tokens(m.content) for m in body) > max_tokens:
        body = body[2:] if len(body) >= 2 else []
    return [head, *body, tail]
