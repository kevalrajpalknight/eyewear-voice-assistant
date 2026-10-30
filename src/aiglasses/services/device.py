from aiglasses.domain.errors import NotSupportedError
from aiglasses.domain.models import BatteryStatus, DeviceInfo
from aiglasses.transport.base import BleTransport


class DeviceService:
    def __init__(self, transport: BleTransport) -> None:
        self._transport = transport

    async def info(self) -> DeviceInfo:
        raise NotSupportedError("device info arrives in Phase 1")

    async def battery(self) -> BatteryStatus:
        raise NotSupportedError("battery needs a captured command (Phase 3)")
