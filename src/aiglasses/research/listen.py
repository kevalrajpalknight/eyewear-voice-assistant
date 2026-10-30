import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from aiglasses.domain.errors import GlassesError
from aiglasses.transport.base import BleTransport
from aiglasses.transport.retry import connect_with_retry

log = logging.getLogger("aiglasses.listen")


@dataclass(frozen=True)
class Notification:
    ts: float
    uuid: str
    handle: int | None
    data: bytes


@dataclass
class ListenResult:
    notifications: list[Notification]
    skipped: dict[str, str]
    subscribed: list[str]


def _handles(transport: BleTransport) -> dict[str, int]:
    return {c.uuid: c.handle for s in transport.services() for c in s.characteristics}


async def _subscribe_all(
    transport: BleTransport,
    handler: Callable[[str, bytes], None],
    result: ListenResult,
) -> None:
    result.subscribed.clear()
    for svc in transport.services():
        for ch in svc.characteristics:
            if "notify" not in ch.properties and "indicate" not in ch.properties:
                continue
            try:
                await transport.subscribe(ch.uuid, handler)
            except GlassesError as exc:
                log.warning("skipping %s: %s", ch.uuid, exc)
                result.skipped[ch.uuid] = str(exc)
            else:
                result.subscribed.append(ch.uuid)


async def listen(
    transport: BleTransport,
    duration: float,
    *,
    on_notification: Callable[[Notification], None] | None = None,
    reconnect_attempts: int = 4,
) -> ListenResult:
    """Subscribe to every notify/indicate characteristic for `duration` seconds; writes nothing
    but the standard CCCD. If the link drops, reconnect with backoff and resubscribe."""
    result = ListenResult(notifications=[], skipped={}, subscribed=[])

    def handler(uuid: str, data: bytes) -> None:
        note = Notification(time.time(), uuid, _handles(transport).get(uuid), data)
        result.notifications.append(note)
        if on_notification:
            on_notification(note)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration
    while loop.time() < deadline:
        await _subscribe_all(transport, handler, result)
        try:
            await asyncio.wait_for(transport.disconnected.wait(), max(0.0, deadline - loop.time()))
        except asyncio.TimeoutError:
            break
        log.warning("link dropped; reconnecting")
        await connect_with_retry(transport, attempts=reconnect_attempts)
    return result
