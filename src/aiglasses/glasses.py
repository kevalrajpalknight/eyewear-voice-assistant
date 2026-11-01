import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from aiglasses.capabilities import CAPABILITIES
from aiglasses.config import Settings
from aiglasses.domain.errors import GlassesTimeoutError
from aiglasses.domain.models import (
    BatteryStatus,
    Capability,
    Confidence,
    DeviceInfo,
    GlassesEvent,
    MediaCounts,
)
from aiglasses.protocol.registry import allowlist_from, load_commands
from aiglasses.protocol.voice import VoiceDecoder
from aiglasses.safety import WriteGuard
from aiglasses.services import (
    AudioService,
    CameraService,
    ControlService,
    DeviceService,
    EventService,
    FrameChannel,
    PhotoResult,
)
from aiglasses.services.thumbnail import ThumbnailResult, ThumbnailService
from aiglasses.services.voice import VoiceUtterance, voice_requests
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
        self.channel = FrameChannel(transport)
        self.control = ControlService(self.guard, self.channel, load_commands())
        self.thumbnail = ThumbnailService(self.guard, self.channel)
        self._events = EventService(self.channel)

    def events(self) -> AsyncIterator[GlassesEvent]:
        """Typed events: `async for event in glasses.events():`."""
        return self._events.events()

    def voice_requests(
        self, decoder: VoiceDecoder | None = None, *, on_start: Callable[[], None] | None = None
    ) -> AsyncIterator[VoiceUtterance]:
        """One decoded utterance per rear-click session. Read-only; needs the `research` extra."""
        if decoder is None:
            from aiglasses.research.voice_decode import OpusVoiceDecoder

            decoder = OpusVoiceDecoder()
        return voice_requests(
            self.events(), decoder, disconnected=self.transport.disconnected, on_start=on_start
        )

    async def read_battery(self) -> BatteryStatus:
        """Read-only request (CONFIRMED). Needs `connect(..., dry_run=False)`."""
        return await self.control.read_battery()

    async def read_device_info(self) -> DeviceInfo:
        """Read-only request (CONFIRMED): firmware and hardware strings."""
        address = str(self.transport.connection_info().get("address", ""))
        return await self.control.read_device_info(address)

    async def read_media_counts(self) -> MediaCounts:
        """Read-only request (CONFIRMED): photos, videos and audio recordings on the glasses."""
        return await self.control.read_media_counts()

    async def wait_for_ai_capture(self, timeout: float) -> None:
        """Return when the glasses report an AI capture (rear double click, "hey glasses")."""

        async def until_capture() -> None:
            async for event in self.events():
                if event.kind == "image_capture_ready":
                    return

        try:
            await asyncio.wait_for(until_capture(), timeout)
        except asyncio.TimeoutError as exc:
            raise GlassesTimeoutError(f"no capture within {timeout:.0f} s") from exc

    async def fetch_thumbnail(self) -> ThumbnailResult:
        """Read the latest AI-photo thumbnail over Bluetooth (needs `dry_run=False`)."""
        return await self.thumbnail.fetch()

    async def enable_wifi_direct(self, *, approved_frame: str) -> None:
        """Send the one user-approved Wi-Fi Direct enable frame (needs dry_run=False)."""
        await self.control.enable_wifi_direct(approved_frame=approved_frame)

    async def take_photo(self, *, confirm: bool) -> PhotoResult:
        """Take ONE photo. Changes the glasses' state, so it needs an explicit confirm=True."""
        return await self.control.take_photo(confirm=confirm)

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
