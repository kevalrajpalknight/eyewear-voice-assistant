"""One notification subscription shared by request/reply calls and the event stream."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from aiglasses.domain.errors import GlassesTimeoutError, ProtocolError
from aiglasses.domain.models import GlassesEvent
from aiglasses.protocol.bcframe import HEADER_LEN, START_BYTE, BcFrame, parse_bc_frame
from aiglasses.protocol.events import frame_to_event
from aiglasses.protocol.registry import Command
from aiglasses.protocol.uuids import GLASSES_NOTIFY
from aiglasses.safety import WriteGuard
from aiglasses.transport.base import BleTransport

log = logging.getLogger("aiglasses.channel")
Matcher = Callable[[BcFrame], bool]
MAX_FRAME_BYTES = 4096  # a longer declared length is not treated as a split frame
FRAGMENT_GAP_S = 1.0  # a split frame must continue within this time or it is dropped


@dataclass
class FrameStat:
    count: int
    first: float
    last: float


class FrameChannel:
    """Subscribes lazily (a second subscription would break `listen`) and fans frames out."""

    def __init__(self, transport: BleTransport) -> None:
        self._transport = transport
        self._started = False
        self.request_timeout = 5.0
        self._lock = asyncio.Lock()
        self._waiters: list[tuple[Matcher, asyncio.Future[BcFrame]]] = []
        self._queues: list[asyncio.Queue[GlassesEvent]] = []
        self.stats: dict[str, FrameStat] = {}
        self._partial = bytearray()
        self._partial_total = 0
        self._partial_at = 0.0

    async def start(self) -> None:
        async with self._lock:
            if not self._started:
                await self._transport.subscribe(GLASSES_NOTIFY, self._on_data)
                self._started = True

    @property
    def active(self) -> bool:
        return self._started

    def _count(self, label: str, moment: float) -> None:
        stat = self.stats.get(label)
        if stat is None:
            self.stats[label] = FrameStat(1, moment, moment)
        else:
            stat.count += 1
            stat.last = moment

    def _join_fragments(self, data: bytes, now: float) -> bytes | None:
        """Reassemble a frame split over several notifications; None while it is incomplete.

        Observed on this unit: a thumbnail chunk (1024 bytes) arrives as a BC header notification
        followed by notifications of raw continuation bytes.
        """
        if self._partial and now - self._partial_at > FRAGMENT_GAP_S:
            self._count("dropped partial frame", now)
            self._partial.clear()
        if self._partial:
            self._partial += data
            self._partial_at = now
            if len(self._partial) < self._partial_total:
                return None
            whole = bytes(self._partial[: self._partial_total])
            self._partial.clear()
            return whole
        if len(data) >= HEADER_LEN and data[0] == START_BYTE:
            total = HEADER_LEN + int.from_bytes(data[2:4], "little")
            if len(data) < total <= MAX_FRAME_BYTES:
                self._partial += data
                self._partial_total, self._partial_at = total, now
                return None
        return data

    def _on_data(self, _uuid: str, data: bytes) -> None:
        joined = self._join_fragments(data, time.time())
        if joined is None:
            return
        data = joined
        try:
            frame = parse_bc_frame(data)
        except ProtocolError as exc:
            log.warning("ignoring a notification that is not a valid BC frame: %s", exc)
            self._count("invalid frame", time.time())
            return
        now = time.time()
        event = frame_to_event(frame, now)
        self._count(event.kind if event is not None else f"cmd 0x{frame.cmd:02x}", now)
        if event is not None:
            for queue in list(self._queues):
                queue.put_nowait(event)
        for waiter in list(self._waiters):
            match, future = waiter
            if not future.done() and match(frame):
                future.set_result(frame)
                self._waiters.remove(waiter)

    def expect(self, match: Matcher) -> asyncio.Future[BcFrame]:
        """Register interest in the next matching frame. Call before writing."""
        future: asyncio.Future[BcFrame] = asyncio.get_running_loop().create_future()
        self._waiters.append((match, future))
        return future

    def discard(self, future: asyncio.Future[BcFrame]) -> None:
        self._waiters = [w for w in self._waiters if w[1] is not future]

    async def request(
        self, guard: WriteGuard, command: Command, match: Matcher, timeout: float | None = None
    ) -> BcFrame:
        """Write the command ONCE through the guard and wait for the matching reply. No retries."""
        await self.start()
        limit = self.request_timeout if timeout is None else timeout
        future = self.expect(match)
        try:
            await guard.write(command.characteristic, command.payload, response=False)
            return await asyncio.wait_for(future, limit)
        except asyncio.TimeoutError as exc:
            raise GlassesTimeoutError(f"no reply to {command.name} within {limit:.1f}s") from exc
        finally:
            self.discard(future)

    async def events(self) -> AsyncIterator[GlassesEvent]:
        await self.start()
        queue: asyncio.Queue[GlassesEvent] = asyncio.Queue()
        self._queues.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._queues.remove(queue)
