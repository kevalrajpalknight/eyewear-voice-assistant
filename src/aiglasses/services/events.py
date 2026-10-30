from collections.abc import AsyncIterator

from aiglasses.domain.errors import NotSupportedError
from aiglasses.domain.models import GlassesEvent
from aiglasses.transport.base import BleTransport


class EventService:
    def __init__(self, transport: BleTransport) -> None:
        self._transport = transport

    def events(self) -> AsyncIterator[GlassesEvent]:
        raise NotSupportedError("decoded events arrive in Phase 5")
