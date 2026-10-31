"""Request/reply calls on the glasses' custom channel. Every write goes through WriteGuard."""

import asyncio
from dataclasses import dataclass

from aiglasses.domain.errors import ConfirmationRequired, GlassesError, NotSupportedError
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


def parse_event_counts(event: BcFrame) -> MediaCounts:
    payload = event.payload
    le = int.from_bytes
    return MediaCounts(
        photos=le(payload[1:3], "little"),
        videos=le(payload[3:5], "little"),
        audio=le(payload[5:7], "little"),
        file_list_type=payload[7] if len(payload) > 7 else None,
    )
