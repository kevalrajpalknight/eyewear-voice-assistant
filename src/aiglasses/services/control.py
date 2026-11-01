"""Request/reply calls on the glasses' custom channel. Every write goes through WriteGuard."""

import asyncio
from dataclasses import dataclass

from aiglasses.domain.errors import (
    ConfirmationRequired,
    GlassesError,
    GlassesTimeoutError,
    NotSupportedError,
)
from aiglasses.domain.models import BatteryStatus, Confidence, DeviceInfo, MediaCounts
from aiglasses.protocol.bcframe import BcFrame
from aiglasses.protocol.events import EVENT_CMD
from aiglasses.protocol.registry import Command
from aiglasses.protocol.replies import (
    describe_control_reply,
    parse_battery,
    parse_device_info,
    parse_media_counts,
)
from aiglasses.safety import WriteGuard
from aiglasses.services.channel import FrameChannel

CONTROL_CMD = 0x41
PHOTO_OBSERVE_SECONDS = 15.0

# Approved by the user on 2026-11-01, for looking only: glassesControl payload 02 01 06 02 02
# (take a photo for AI vision, thumbnail size 2). It may repeat, once per look. Nothing else.
APPROVED_AI_PHOTO_FRAME = "bc4105003c9c0201060202"
AI_CAPTURE_EVENT = 2
AI_CAPTURE_WAIT_S = 10.0


@dataclass(frozen=True)
class PhotoResult:
    before: MediaCounts
    after: MediaCounts | None
    reply: str
    reply_hex: str

    @property
    def photos_added(self) -> int | None:
        return None if self.after is None else self.after.photos - self.before.photos


class ControlService:
    """The only requests the library can send: three confirmed reads and one confirmed photo."""

    def __init__(self, guard: WriteGuard, channel: FrameChannel, commands: list[Command]) -> None:
        self._guard = guard
        self._channel = channel
        self._commands = {c.name: c for c in commands}

    def _command(self, name: str) -> Command:
        command = self._commands.get(name)
        if command is None or command.status is not Confidence.CONFIRMED:
            raise NotSupportedError(f"{name} is not a CONFIRMED command on this unit")
        if self._guard.dry_run:
            raise GlassesError("dry run: connect with dry_run=False to send requests")
        return command

    async def read_battery(self) -> BatteryStatus:
        reply = await self._channel.request(
            self._guard, self._command("battery_read"), lambda f: f.cmd == 0x42
        )
        return parse_battery(reply.payload)

    async def read_device_info(self, address: str = "") -> DeviceInfo:
        reply = await self._channel.request(
            self._guard, self._command("device_info_read"), lambda f: f.cmd == 0x43
        )
        versions = parse_device_info(reply.payload)
        return DeviceInfo(
            name=None,
            address=address,
            hardware=versions.hardware or None,
            firmware=versions.firmware or None,
            serial=None,
        )

    async def read_media_counts(self) -> MediaCounts:
        def is_counts(frame: BcFrame) -> bool:
            return frame.cmd == CONTROL_CMD and len(frame.payload) > 1 and frame.payload[1] == 4

        reply = await self._channel.request(
            self._guard, self._command("media_counts_read"), is_counts
        )
        return parse_media_counts(reply.payload)

    async def enable_wifi_direct(self, *, approved_frame: str) -> None:
        """Send the ONE approved Wi-Fi Direct enable frame. The caller must pass the exact hex."""
        command = self._commands.get("wifi_direct_on")  # INFERRED in the registry; the user's
        if command is None:  # written approval of these exact bytes stands in for CONFIRMED
            raise NotSupportedError("wifi_direct_on is not in the command registry")
        if self._guard.dry_run:
            raise GlassesError("dry run: connect with dry_run=False to send requests")
        if approved_frame != APPROVED_WIFI_DIRECT_FRAME or command.payload.hex() != approved_frame:
            raise ConfirmationRequired("only the frame the user approved may be sent")
        self._guard.approve(command.name, command.characteristic, command.payload)
        await self._guard.write(command.characteristic, command.payload, response=False)

    async def capture_ai_photo(
        self, *, approved_frame: str, wait_s: float = AI_CAPTURE_WAIT_S
    ) -> None:
        """Take one photo for AI vision and wait until the glasses report it is ready."""
        command = self._commands.get("ai_photo_capture")
        if command is None:
            raise NotSupportedError("ai_photo_capture is not in the command registry")
        if self._guard.dry_run:
            raise GlassesError("dry run: connect with dry_run=False to send requests")
        if approved_frame != APPROVED_AI_PHOTO_FRAME or command.payload.hex() != approved_frame:
            raise ConfirmationRequired("only the frame the user approved may be sent")

        def is_ready(frame: BcFrame) -> bool:
            return frame.cmd == EVENT_CMD and frame.payload[:1] == bytes([AI_CAPTURE_EVENT])

        await self._channel.start()
        ready = self._channel.expect(is_ready)
        try:
            self._guard.approve(
                command.name, command.characteristic, command.payload, repeatable=True
            )
            await self._guard.write(command.characteristic, command.payload, response=False)
            await asyncio.wait_for(ready, wait_s)
        except asyncio.TimeoutError as exc:
            raise GlassesTimeoutError(f"no capture-ready event within {wait_s:.0f} s") from exc
        finally:
            self._channel.discard(ready)

    async def take_photo(
        self, *, confirm: bool, observe_seconds: float = PHOTO_OBSERVE_SECONDS
    ) -> PhotoResult:
        """Take ONE photo. Reads the counts first (and aborts if that fails), then writes once."""
        if not confirm:
            raise ConfirmationRequired(
                "take_photo changes the glasses' state; pass confirm=True only after the person "
                "present has agreed"
            )
        photo = self._command("photo")
        before = await self.read_media_counts()

        def is_photo_reply(frame: BcFrame) -> bool:
            return frame.cmd == CONTROL_CMD and len(frame.payload) > 1 and frame.payload[1] == 1

        def is_count_event(frame: BcFrame) -> bool:
            return frame.cmd == EVENT_CMD and frame.payload[:1] == b"\x01"

        count_event = self._channel.expect(is_count_event)
        try:
            self._guard.approve(photo.name, photo.characteristic, photo.payload)
            reply = await self._channel.request(self._guard, photo, is_photo_reply)
            try:
                event = await asyncio.wait_for(count_event, observe_seconds)
                after: MediaCounts | None = parse_event_counts(event)
            except asyncio.TimeoutError:
                after = None
        finally:
            self._channel.discard(count_event)
        return PhotoResult(
            before, after, describe_control_reply(reply.payload), reply.payload.hex()
        )


# Approved in writing by the user on 2026-11-01 for supervised runs only: glassesControl payload
# 02 01 04 01 (start Wi-Fi Direct), write without response, ONCE per run, never retried. Nothing
# else (no heartbeat, no 02 01 09, no access-point mode) is approved.
APPROVED_WIFI_DIRECT_FRAME = "bc410400935c02010401"


def parse_event_counts(event: BcFrame) -> MediaCounts:
    payload = event.payload
    le = int.from_bytes
    return MediaCounts(
        photos=le(payload[1:3], "little"),
        videos=le(payload[3:5], "little"),
        audio=le(payload[5:7], "little"),
        file_list_type=payload[7] if len(payload) > 7 else None,
    )
