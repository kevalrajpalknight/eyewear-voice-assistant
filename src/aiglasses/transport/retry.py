import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiglasses.domain.errors import ConnectionFailedError, DeviceNotFoundError, GlassesTimeoutError
from aiglasses.transport.base import BleTransport

log = logging.getLogger("aiglasses.retry")

RETRYABLE = (DeviceNotFoundError, ConnectionFailedError, GlassesTimeoutError)


async def connect_with_retry(
    transport: BleTransport,
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 15.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Connect with exponential backoff. Pairing errors are never retried."""
    for attempt in range(1, attempts + 1):
        try:
            await transport.connect()
            return
        except RETRYABLE as exc:
            if attempt == attempts:
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            if isinstance(exc, DeviceNotFoundError):
                log.info(
                    "glasses not advertising yet (attempt %d/%d). They can need up to about 30 s "
                    "after a session ends; trying again in %.1fs",
                    attempt,
                    attempts,
                    delay,
                )
            else:
                log.warning(
                    "connect attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    attempts,
                    exc,
                    delay,
                )
            await sleep(delay)
