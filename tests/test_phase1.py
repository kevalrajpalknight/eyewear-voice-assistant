import asyncio
import json
from pathlib import Path

import pytest
from bleak.exc import BleakBluetoothNotAvailableError, BleakError

from aiglasses.domain.errors import (
    BluetoothUnavailableError,
    ConnectionFailedError,
    GlassesError,
    NotSupportedError,
    PairingRequiredError,
)
from aiglasses.domain.models import GattCharacteristic, GattService
from aiglasses.protocol import uuids
from aiglasses.research.gatt_dump import dump_gatt, write_dump
from aiglasses.research.listen import listen
from aiglasses.services.device import DeviceService
from aiglasses.transport.bleak_transport import map_bleak_error
from aiglasses.transport.fakes import FakeBleTransport
from aiglasses.transport.retry import connect_with_retry
from aiglasses.transport.tracing import TracingTransport
from tests.conftest import AE02, SERVICE_CHANGED


async def test_gatt_dump_reads_only_readable_and_never_writes(
    gatt_services: list[GattService], gatt_reads: dict[str, bytes], tmp_path: Path
) -> None:
    transport = FakeBleTransport(gatt_reads, gatt_services)
    data = await dump_gatt(transport)
    assert sorted(transport.read_log) == sorted(gatt_reads)
    assert transport.writes == []
    chars = {c["uuid"]: c for s in data["services"] for c in s["characteristics"]}
    assert chars[uuids.FIRMWARE_REVISION]["value_ascii"] == "AM01G1_9.40.00_260114"
    assert chars[uuids.SERIAL_NUMBER]["value_hex"] == ""
    assert "value_hex" not in chars[AE02]
    assert data["mtu"] == 23 and data["connection"]["fake"] is True
    out = tmp_path / "dump.json"
    write_dump(out, data)
    assert json.loads(out.read_text())["services"][0]["characteristics"][0]["handle"] == 2


async def test_listen_collects_skips_and_reports_handles(gatt_services: list[GattService]) -> None:
    transport = FakeBleTransport(
        services=gatt_services,
        subscribe_errors={SERVICE_CHANGED: PairingRequiredError("Access Denied")},
    )
    asyncio.get_running_loop().call_later(0.05, transport.emit, AE02, b"\x01\x02")
    result = await listen(transport, 0.2)
    assert result.subscribed == [AE02]
    assert SERVICE_CHANGED in result.skipped
    assert [(n.handle, n.data) for n in result.notifications] == [(7, b"\x01\x02")]
    assert transport.writes == []


async def test_listen_reconnects_and_resubscribes(gatt_services: list[GattService]) -> None:
    transport = FakeBleTransport(services=gatt_services)
    await transport.connect()
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, transport.drop_link)
    loop.call_later(0.15, transport.emit, AE02, b"\xaa")
    result = await listen(transport, 0.3)
    assert transport.connect_calls == 2
    assert [n.data for n in result.notifications] == [b"\xaa"]


async def test_connect_with_retry_backs_off_then_succeeds() -> None:
    transport = FakeBleTransport(fail_connects=2)
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    await connect_with_retry(transport, attempts=4, base_delay=1.0, sleep=fake_sleep)
    assert delays == [1.0, 2.0]
    assert transport.connected


async def test_connect_with_retry_gives_up() -> None:
    transport = FakeBleTransport(fail_connects=10)

    async def no_sleep(_: float) -> None:
        return None

    with pytest.raises(ConnectionFailedError):
        await connect_with_retry(transport, attempts=3, sleep=no_sleep)
    assert transport.connect_calls == 3


async def test_pairing_errors_are_not_retried() -> None:
    class NeedsPairing(FakeBleTransport):
        async def connect(self) -> None:
            self.connect_calls += 1
            raise PairingRequiredError("bond first")

    transport = NeedsPairing()
    with pytest.raises(PairingRequiredError):
        await connect_with_retry(transport, attempts=3)
    assert transport.connect_calls == 1


async def test_tracing_logs_cccd_reads_and_notifies_with_handles(
    gatt_services: list[GattService], gatt_reads: dict[str, bytes], tmp_path: Path
) -> None:
    inner = FakeBleTransport(gatt_reads, gatt_services)
    trace = tmp_path / "trace.jsonl"
    transport = TracingTransport(inner, trace)
    await transport.subscribe(AE02, lambda _u, _d: None)
    inner.emit(AE02, b"\x07")
    await transport.read(uuids.FIRMWARE_REVISION)
    entries = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [e["op"] for e in entries] == ["cccd_enable", "notify", "read_request", "read"]
    assert entries[1]["handle"] == 7 and entries[1]["hex"] == "07"
    assert entries[3]["handle"] == 151 and "ts" in entries[3]
    assert transport.mtu == 23 and not transport.disconnected.is_set()
    assert transport.connection_info()["fake"] is True


async def test_device_info_and_battery(
    gatt_services: list[GattService], gatt_reads: dict[str, bytes]
) -> None:
    service = DeviceService(FakeBleTransport(gatt_reads, gatt_services))
    info = await service.info()
    assert (info.name, info.hardware, info.firmware, info.serial) == (
        "AIMB-G2_2324",
        "AM01G1_V9.4",
        "AM01G1_9.40.00_260114",
        None,
    )
    with pytest.raises(NotSupportedError):
        await service.battery()


async def test_battery_read_when_standard_service_exists() -> None:
    services = [
        GattService(
            uuids.BATTERY_SERVICE, 1, [GattCharacteristic(uuids.BATTERY_LEVEL, 2, ["read"])]
        )
    ]
    service = DeviceService(FakeBleTransport({uuids.BATTERY_LEVEL: b"\x57"}, services))
    assert (await service.battery()).percent == 87


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Could not start notify on 0047: Access Denied", PairingRequiredError),
        ("Could not get GATT services: Unreachable", ConnectionFailedError),
        ("something else", ConnectionFailedError),
    ],
)
def test_map_bleak_error(message: str, expected: type[GlassesError]) -> None:
    assert isinstance(map_bleak_error(BleakError(message), "op"), expected)


def test_map_bleak_error_radio_off() -> None:
    err = BleakBluetoothNotAvailableError("off", reason=None)  # type: ignore[arg-type]
    assert isinstance(map_bleak_error(err, "scan"), BluetoothUnavailableError)


async def test_mark_is_written_to_trace(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    TracingTransport(FakeBleTransport(), trace).mark("photo")
    entry = json.loads(trace.read_text().splitlines()[0])
    assert entry["op"] == "mark" and entry["label"] == "photo" and "ts" in entry
