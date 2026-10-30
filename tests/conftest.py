import pytest

from aiglasses.domain.models import GattCharacteristic, GattDescriptor, GattService
from aiglasses.protocol import uuids

CCCD = "00002902-0000-1000-8000-00805f9b34fb"
AE01 = "0000ae01-0000-1000-8000-00805f9b34fb"
AE02 = "0000ae02-0000-1000-8000-00805f9b34fb"
SERVICE_CHANGED = "00002a05-0000-1000-8000-00805f9b34fb"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--hardware", action="store_true", help="run tests that need real glasses")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "hardware: needs the real glasses in range (skipped by default)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--hardware"):
        return
    skip = pytest.mark.skip(reason="needs --hardware")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def gatt_services() -> list[GattService]:
    return [
        GattService(
            "00001800-0000-1000-8000-00805f9b34fb",
            1,
            [GattCharacteristic(uuids.DEVICE_NAME, 2, ["read", "write"])],
        ),
        GattService(
            "0000ae30-0000-1000-8000-00805f9b34fb",
            4,
            [
                GattCharacteristic(AE01, 5, ["write-without-response"]),
                GattCharacteristic(AE02, 7, ["notify"], [GattDescriptor(CCCD, 9)]),
            ],
        ),
        GattService(
            "00001801-0000-1000-8000-00805f9b34fb",
            70,
            [GattCharacteristic(SERVICE_CHANGED, 71, ["indicate"])],
        ),
        GattService(
            uuids.DEVICE_INFO_SERVICE,
            146,
            [
                GattCharacteristic(uuids.SERIAL_NUMBER, 147, ["read"]),
                GattCharacteristic(uuids.HARDWARE_REVISION, 149, ["read"]),
                GattCharacteristic(uuids.FIRMWARE_REVISION, 151, ["read"]),
            ],
        ),
    ]


@pytest.fixture
def gatt_reads() -> dict[str, bytes]:
    return {
        uuids.DEVICE_NAME: b"AIMB-G2_2324",
        uuids.SERIAL_NUMBER: b"",
        uuids.HARDWARE_REVISION: b"AM01G1_V9.4",
        uuids.FIRMWARE_REVISION: b"AM01G1_9.40.00_260114",
    }
