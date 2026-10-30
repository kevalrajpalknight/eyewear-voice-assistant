from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiglasses.capabilities import CAPABILITIES
from aiglasses.config import Settings
from aiglasses.domain.models import Capability, Confidence, GlassesEvent
from aiglasses.protocol.registry import allowlist_from, load_commands
from aiglasses.safety import WriteGuard
from aiglasses.services import AudioService, CameraService, DeviceService, EventService
from aiglasses.transport.base import AudioIO, BleTransport
from aiglasses.transport.bleak_transport import BleakTransport
from aiglasses.transport.retry import connect_with_retry
from aiglasses.transport.tracing import TracingTransport


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
        self.transport = transport
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
        """Connect (saved address, else name prefix), retrying with backoff; always disconnects."""
        if transport is None:
            transport = TracingTransport(
                BleakTransport(
                    address=config.device_address,
                    name_prefix=config.name_prefix,
                    scan_timeout=config.scan_timeout,
                    connect_timeout=config.connect_timeout,
                    io_timeout=config.io_timeout,
                ),
                config.log_dir / "trace.jsonl",
            )
        await connect_with_retry(transport, attempts=config.connect_attempts)
        try:
            yield cls(transport, config, audio=audio, dry_run=dry_run)
        finally:
            await transport.disconnect()
