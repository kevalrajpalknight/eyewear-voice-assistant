from collections.abc import AsyncIterator

from aiglasses.domain.models import GlassesEvent
from aiglasses.services.channel import FrameChannel


class EventService:
    """Typed events from the glasses: 0x73 device events and 0x59 voice frames."""

    def __init__(self, channel: FrameChannel) -> None:
        self._channel = channel

    def events(self) -> AsyncIterator[GlassesEvent]:
        return self._channel.events()
