"""Device tools. Each maps to ONE existing guarded facade call; none can send arbitrary bytes.

`DeviceHolder` is the live link to the glasses: it is bound while the BLE session is open, and a
tool called while it is unbound answers "not connected" instead of failing.
"""

import time
from collections.abc import Callable
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from aiglasses.assistant.tools.base import NoArgs, Risk, ToolContext, ToolRefused, ToolResult
from aiglasses.domain.errors import ConfirmationRequired, GlassesError
from aiglasses.domain.models import BatteryStatus, Confidence, MediaCounts
from aiglasses.protocol.registry import load_commands
from aiglasses.services.control import PhotoResult

NOT_CONNECTED = "The glasses are not connected."


class DeviceActions(Protocol):
    async def read_battery(self) -> BatteryStatus: ...

    async def read_media_counts(self) -> MediaCounts: ...

    async def take_photo(self, *, confirm: bool) -> PhotoResult: ...


class DeviceHolder:
    """Forwards to the currently connected `Glasses`, or refuses politely."""

    def __init__(self) -> None:
        self._glasses: Any = None

    def bind(self, glasses: Any) -> None:
        self._glasses = glasses

    def unbind(self) -> None:
        self._glasses = None

    def _need(self) -> Any:
        if self._glasses is None:
            raise ToolRefused("glasses not connected", NOT_CONNECTED)
        return self._glasses

    async def read_battery(self) -> BatteryStatus:
        result: BatteryStatus = await self._need().read_battery()
        return result

    async def read_media_counts(self) -> MediaCounts:
        result: MediaCounts = await self._need().read_media_counts()
        return result

    async def take_photo(self, *, confirm: bool) -> PhotoResult:
        result: PhotoResult = await self._need().take_photo(confirm=confirm)
        return result


class ReadBattery:
    name: ClassVar[str] = "read_battery"
    description: ClassVar[str] = "Read the glasses battery percentage."
    risk: ClassVar[Risk] = Risk.READ
    args_model: ClassVar[type[BaseModel]] = NoArgs
    announcement: ClassVar[str | None] = None

    def __init__(self, device: DeviceActions) -> None:
        self._device = device

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        try:
            status = await self._device.read_battery()
        except GlassesError as exc:
            raise ToolRefused(f"battery read failed: {type(exc).__name__}") from exc
        charging = "" if status.charging is None else (", charging" if status.charging else "")
        return ToolResult(
            f"battery {status.percent} percent{charging}",
            f"The glasses battery is at {status.percent} percent.",
        )


class ReadMediaCounts:
    name: ClassVar[str] = "read_media_counts"
    description: ClassVar[str] = "Count the photos, videos and recordings stored on the glasses."
    risk: ClassVar[Risk] = Risk.READ
    args_model: ClassVar[type[BaseModel]] = NoArgs
    announcement: ClassVar[str | None] = None

    def __init__(self, device: DeviceActions) -> None:
        self._device = device

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        try:
            counts = await self._device.read_media_counts()
        except GlassesError as exc:
            raise ToolRefused(f"media count failed: {type(exc).__name__}") from exc
        return ToolResult(
            f"photos {counts.photos}, videos {counts.videos}, recordings {counts.audio}",
            f"There are {counts.photos} photos, {counts.videos} videos and "
            f"{counts.audio} recordings on the glasses.",
        )


class TakePhoto:
    """State-changing: runs only when the user's own words asked for it (see intents.yaml).

    The facade's `confirm` flag is set here, and only here, after that check has passed.
    """

    name: ClassVar[str] = "take_photo"
    description: ClassVar[str] = "Take one photo with the glasses camera."
    risk: ClassVar[Risk] = Risk.DEVICE_STATE
    args_model: ClassVar[type[BaseModel]] = NoArgs
    announcement: ClassVar[str | None] = "Taking a photo."

    def __init__(
        self,
        device: DeviceActions,
        *,
        min_interval_s: float,
        max_per_session: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._device = device
        self._min_interval = min_interval_s
        self._max = max_per_session
        self._clock = clock
        self._taken = 0
        self._last: float | None = None

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        now = self._clock()
        if self._taken >= self._max:
            raise ToolRefused(
                "photo limit for this session reached", "That is enough photos for now."
            )
        if self._last is not None and now - self._last < self._min_interval:
            raise ToolRefused(
                "photo requested too soon after the last one", "One moment, too soon."
            )
        self._last = now
        try:
            result = await self._device.take_photo(confirm=True)
        except ConfirmationRequired as exc:  # cannot happen: confirm is True
            raise ToolRefused("facade asked for confirmation") from exc
        except GlassesError as exc:
            raise ToolRefused(f"photo failed: {type(exc).__name__}") from exc
        self._taken += 1
        added = result.photos_added
        if added is None:
            return ToolResult("photo command sent, result not confirmed", "Done, I think.")
        return ToolResult(f"photo taken, {added} added", "Done.")


class RecordingTool:
    """start_video / start_audio_recording: registered only when everything allows it, and even
    then they stay BLOCKED: the facade has no recording method and nothing is ever sent."""

    description: ClassVar[str] = "Start a recording (blocked)."
    risk: ClassVar[Risk] = Risk.BLOCKED
    args_model: ClassVar[type[BaseModel]] = NoArgs
    announcement: ClassVar[str | None] = None

    def __init__(self, name: str, command: str) -> None:
        self.name = name
        self.command = command

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        raise ToolRefused("recording tools are blocked")


RECORDING_TOOLS = {"start_video": "video_start", "start_audio_recording": "audio_record_start"}


def recording_tools_to_register(
    enabled: tuple[str, ...], allow: bool, command_status: dict[str, Confidence] | None = None
) -> list[RecordingTool]:
    """Only when listed in TOOLS_ENABLED AND ALLOW_RECORDING_TOOLS AND the command is CONFIRMED."""
    if not allow:
        return []
    status = command_status or {c.name: c.status for c in load_commands()}
    return [
        RecordingTool(name, command)
        for name, command in RECORDING_TOOLS.items()
        if name in enabled and status.get(command) is Confidence.CONFIRMED
    ]
