"""Shared fake glasses for the agent tests."""

from aiglasses.domain.models import BatteryStatus, MediaCounts
from aiglasses.services.control import PhotoResult


class Device:
    async def read_battery(self) -> BatteryStatus:
        return BatteryStatus(percent=61, charging=False)

    async def read_media_counts(self) -> MediaCounts:
        return MediaCounts(1, 1, 1)

    async def take_photo(self, *, confirm: bool) -> PhotoResult:
        return PhotoResult(MediaCounts(1, 1, 1), MediaCounts(2, 1, 1), "ok", "00")
