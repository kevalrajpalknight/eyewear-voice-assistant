"""Needs the real glasses awake and not connected to a phone. Run: pytest --hardware"""

from pathlib import Path

import pytest

from aiglasses import Glasses
from aiglasses.config import Settings
from aiglasses.protocol import uuids
from aiglasses.research.gatt_dump import dump_gatt

pytestmark = pytest.mark.hardware


async def test_connect_info_and_dump(tmp_path: Path) -> None:
    settings = Settings(log_dir=tmp_path)
    async with Glasses.connect(settings) as g:
        info = await g.device.info()
        data = await dump_gatt(g.transport)
    assert info.firmware
    assert any(s["uuid"] == uuids.DEVICE_INFO_SERVICE for s in data["services"])
