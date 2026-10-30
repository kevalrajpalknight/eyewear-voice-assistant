from pathlib import Path

import pytest

from aiglasses import Glasses
from aiglasses.config import Settings, save_device_address
from aiglasses.domain.errors import NotSupportedError
from aiglasses.domain.models import Capability, Confidence, ScanResult
from aiglasses.transport.ble_scan import MAC_SERVICE_UUID, is_candidate
from aiglasses.transport.fakes import FakeBleTransport


async def test_connect_lifecycle_and_unsupported_services(tmp_path: Path) -> None:
    transport = FakeBleTransport()
    config = Settings(log_dir=tmp_path)
    async with Glasses.connect(config, transport=transport) as g:
        assert transport.connected
        with pytest.raises(NotSupportedError):
            await g.camera.take_photo()
        with pytest.raises(NotSupportedError):
            await g.device.battery()
        with pytest.raises(NotSupportedError):
            g.events()
        assert g.capabilities()[Capability.PHOTO] is Confidence.UNVERIFIED
    assert not transport.connected


def test_is_candidate_by_name_and_mac_service_data() -> None:
    by_name = ScanResult("D0:4B:1B:C3:23:24", "AIMB-G2_2324", -50)
    by_mac = ScanResult(
        "D0:4B:1B:C3:23:24",
        None,
        -50,
        service_data={MAC_SERVICE_UUID: bytes.fromhex("d04b1bc32324")},
    )
    other = ScanResult("3C:70:89:2A:F0:AA", "Other", -50)
    assert is_candidate(by_name, "AIMB-G2_")
    assert is_candidate(by_mac, "AIMB-G2_")
    assert not is_candidate(other, "AIMB-G2_")


def test_save_device_address_updates_in_place(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_device_address("AA:BB", path)
    save_device_address("CC:DD", path)
    assert path.read_text() == 'device_address = "CC:DD"\n'
