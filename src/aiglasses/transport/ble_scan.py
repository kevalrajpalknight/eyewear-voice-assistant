from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakBluetoothNotAvailableError

from aiglasses.domain.errors import BluetoothUnavailableError
from aiglasses.domain.models import ScanResult

# CONFIRMED on this unit: service data under 0x3802 carries the device's own MAC bytes.
MAC_SERVICE_UUID = "00003802-0000-1000-8000-00805f9b34fb"


def is_candidate(result: ScanResult, name_prefix: str) -> bool:
    if result.name and result.name.startswith(name_prefix):
        return True
    mac = bytes.fromhex(result.address.replace(":", ""))
    return result.service_data.get(MAC_SERVICE_UUID) == mac


def to_scan_result(dev: BLEDevice, adv: AdvertisementData) -> ScanResult:
    return ScanResult(
        address=dev.address,
        name=dev.name or adv.local_name,
        rssi=adv.rssi,
        service_uuids=list(adv.service_uuids),
        manufacturer_data=dict(adv.manufacturer_data),
        service_data=dict(adv.service_data),
    )


async def scan(timeout: float) -> list[ScanResult]:
    try:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except BleakBluetoothNotAvailableError as exc:
        raise BluetoothUnavailableError(str(exc)) from exc
    results = [to_scan_result(dev, adv) for dev, adv in found.values()]
    return sorted(results, key=lambda r: r.rssi, reverse=True)
