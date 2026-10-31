import asyncio
import logging
import platform
from collections.abc import Awaitable
from importlib import metadata
from typing import TypeVar

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakBluetoothNotAvailableError, BleakError

from aiglasses.domain.errors import (
    BluetoothUnavailableError,
    ConnectionFailedError,
    DeviceNotFoundError,
    GlassesError,
    GlassesTimeoutError,
    PairingRequiredError,
)
from aiglasses.domain.models import GattCharacteristic, GattDescriptor, GattService
from aiglasses.transport.base import NotifyHandler
from aiglasses.transport.ble_scan import is_candidate, to_scan_result

log = logging.getLogger("aiglasses.bleak")
T = TypeVar("T")

NOT_FOUND_HINT = (
    "Glasses not found after scanning. They can need up to about 30 s to advertise again after a "
    "session ends. If it persists, toggle their Bluetooth and make sure the phone is not connected "
    "(a connected phone stops the glasses advertising)."
)
PAIRING_WORDS = ("access denied", "pair", "authentication", "encryption", "protection")


def map_bleak_error(exc: BleakError, what: str) -> GlassesError:
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, BleakBluetoothNotAvailableError):
        return BluetoothUnavailableError(msg)
    if any(word in low for word in PAIRING_WORDS):
        return PairingRequiredError(
            f"{what}: needs bonding ({msg}). Not pairing automatically; ask the user first."
        )
    if "unreachable" in low:
        return ConnectionFailedError(
            f"{what}: device unreachable ({msg}). Is the phone still connected to the glasses?"
        )
    return ConnectionFailedError(f"{what}: {msg}")


class BleakTransport:
    """BLE over bleak. Windows connections skip the cached GATT table and never pair."""

    def __init__(
        self,
        *,
        address: str | None,
        name_prefix: str,
        scan_timeout: float = 15.0,
        connect_timeout: float = 20.0,
        io_timeout: float = 10.0,
    ) -> None:
        self._address = address
        self._name_prefix = name_prefix
        self._scan_timeout = scan_timeout
        self._connect_timeout = connect_timeout
        self._io_timeout = io_timeout
        self._client: BleakClient | None = None
        self._device: BLEDevice | None = None
        self._disconnected = asyncio.Event()
        self._closing = False

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def mtu(self) -> int:
        return self._connected().mtu_size

    @property
    def disconnected(self) -> asyncio.Event:
        return self._disconnected

    def _connected(self) -> BleakClient:
        if self._client is None or not self._client.is_connected:
            raise ConnectionFailedError("not connected")
        return self._client

    async def _io(self, awaitable: Awaitable[T], what: str, timeout: float) -> T:
        try:
            return await asyncio.wait_for(awaitable, timeout)
        except asyncio.TimeoutError as exc:
            raise GlassesTimeoutError(f"{what} timed out after {timeout:.0f}s") from exc
        except BleakError as exc:
            raise map_bleak_error(exc, what) from exc

    async def _find(self) -> BLEDevice:
        try:
            if self._address:
                device = await BleakScanner.find_device_by_address(
                    self._address, timeout=self._scan_timeout
                )
            else:
                device = await BleakScanner.find_device_by_filter(
                    lambda d, a: is_candidate(to_scan_result(d, a), self._name_prefix),
                    timeout=self._scan_timeout,
                )
        except BleakBluetoothNotAvailableError as exc:
            raise BluetoothUnavailableError(str(exc)) from exc
        if device is None:
            raise DeviceNotFoundError(NOT_FOUND_HINT)
        return device

    def _on_disconnect(self, _client: BleakClient) -> None:
        if self._closing:
            log.info("disconnected (we asked for it)")
        else:
            log.warning("link lost (the glasses dropped the connection)")
        self._disconnected.set()

    async def connect(self) -> None:
        device = await self._find()
        self._disconnected = asyncio.Event()
        self._closing = False
        client = BleakClient(
            device,
            disconnected_callback=self._on_disconnect,
            timeout=self._connect_timeout,
            pair=False,
            winrt={"use_cached_services": False},
        )
        log.info("connecting to %s (%s)", device.name, device.address)
        await self._io(client.connect(), "connect", self._connect_timeout + 5)
        self._client, self._device = client, device
        log.info("connected, MTU %d", client.mtu_size)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._closing = True
        log.info("disconnecting")
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), self._io_timeout)
        except (asyncio.TimeoutError, BleakError) as exc:
            log.warning("disconnect did not finish cleanly: %s", exc)

    def services(self) -> list[GattService]:
        return [
            GattService(
                uuid=svc.uuid,
                handle=svc.handle,
                characteristics=[
                    GattCharacteristic(
                        uuid=ch.uuid,
                        handle=ch.handle,
                        properties=list(ch.properties),
                        descriptors=[GattDescriptor(d.uuid, d.handle) for d in ch.descriptors],
                    )
                    for ch in svc.characteristics
                ],
            )
            for svc in self._connected().services
        ]

    def connection_info(self) -> dict[str, object]:
        client = self._connected()
        return {
            "address": client.address,
            "name": self._device.name if self._device else None,
            "is_connected": client.is_connected,
            "mtu": client.mtu_size,
            "platform": platform.platform(),
            "bleak_version": metadata.version("bleak"),
            "uncached_services": True,
            "paired_by_us": False,
        }

    async def read(self, char: str) -> bytes:
        data = await self._io(
            self._connected().read_gatt_char(char), f"read {char}", self._io_timeout
        )
        return bytes(data)

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        await self._io(
            self._connected().write_gatt_char(char, data, response=response),
            f"write {char}",
            self._io_timeout,
        )

    async def subscribe(self, char: str, handler: NotifyHandler) -> None:
        def callback(sender: BleakGATTCharacteristic, data: bytearray) -> None:
            handler(sender.uuid, bytes(data))

        await self._io(
            self._connected().start_notify(char, callback), f"subscribe {char}", self._io_timeout
        )
