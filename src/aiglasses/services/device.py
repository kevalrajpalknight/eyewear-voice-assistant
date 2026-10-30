from aiglasses.domain.errors import NotSupportedError
from aiglasses.domain.models import BatteryStatus, DeviceInfo
from aiglasses.protocol import uuids
from aiglasses.transport.base import BleTransport


class DeviceService:
    def __init__(self, transport: BleTransport) -> None:
        self._transport = transport

    def _has(self, char_uuid: str) -> bool:
        return any(
            c.uuid == char_uuid for s in self._transport.services() for c in s.characteristics
        )

    async def _text(self, char_uuid: str) -> str | None:
        if not self._has(char_uuid):
            return None
        raw = await self._transport.read(char_uuid)
        return raw.decode("utf-8", errors="replace").strip("\x00 ") or None

    async def info(self) -> DeviceInfo:
        """Standard Device Information (0x180A) and device name; read-only."""
        address = str(self._transport.connection_info().get("address", ""))
        return DeviceInfo(
            name=await self._text(uuids.DEVICE_NAME),
            address=address,
            hardware=await self._text(uuids.HARDWARE_REVISION),
            firmware=await self._text(uuids.FIRMWARE_REVISION),
            serial=await self._text(uuids.SERIAL_NUMBER),
        )

    async def battery(self) -> BatteryStatus:
        if not self._has(uuids.BATTERY_LEVEL):
            raise NotSupportedError(
                "no standard Battery service (0x180F) here; needs a captured command (Phase 3)"
            )
        raw = await self._transport.read(uuids.BATTERY_LEVEL)
        return BatteryStatus(percent=raw[0])
