from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiglasses.capabilities import CAPABILITIES
from aiglasses.config import Settings
from aiglasses.domain.errors import NotSupportedError
from aiglasses.domain.models import Capability, Confidence, GlassesEvent
from aiglasses.protocol.registry import allowlist_from, load_commands
from aiglasses.safety import WriteGuard
from aiglasses.services import AudioService, CameraService, DeviceService, EventService
from aiglasses.transport.base import AudioIO, BleTransport


class Glasses:
    """Facade over all services. Use `async with Glasses.connect(config) as g:`."""

    def __init__(
        self,
        transport: BleTransport,
        config: Settings,
        *,
        audio: AudioIO | None = None,
        dry_run: bool = True,
    ) -> None:
        self.config = config
        self.guard = WriteGuard(
            transport,
            allowlist_from(load_commands()),
            dry_run=dry_run,
            audit_path=config.log_dir / "write_audit.jsonl",
        )
        self.device = DeviceService(transport)
        self.camera = CameraService(self.guard)
        self.audio = AudioService(audio)
        self._events = EventService(transport)

    def events(self) -> AsyncIterator[GlassesEvent]:
        return self._events.events()

    def capabilities(self) -> dict[Capability, Confidence]:
        return dict(CAPABILITIES)

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        config: Settings,
        *,
        transport: BleTransport | None = None,
        audio: AudioIO | None = None,
        dry_run: bool = True,
    ) -> AsyncIterator["Glasses"]:
        if transport is None:
            raise NotSupportedError("BleakTransport arrives in Phase 1; pass a transport")
        await transport.connect()
        try:
            yield cls(transport, config, audio=audio, dry_run=dry_run)
        finally:
            await transport.disconnect()
