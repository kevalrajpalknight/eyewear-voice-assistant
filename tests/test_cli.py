import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from aiglasses import Glasses
from aiglasses.cli import main as cli
from aiglasses.domain.models import GattService, ScanResult
from aiglasses.logging_setup import setup_logging
from aiglasses.transport.fakes import FakeBleTransport
from tests.conftest import AE02

runner = CliRunner()


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def use_fake(monkeypatch: pytest.MonkeyPatch, transport: FakeBleTransport) -> None:
    @asynccontextmanager
    async def fake_connect(config: Any, **_: Any) -> Any:
        await transport.connect()
        yield Glasses(transport, config)
        await transport.disconnect()

    monkeypatch.setattr(Glasses, "connect", staticmethod(fake_connect))


def test_capabilities_lists_confirmed_rows() -> None:
    result = runner.invoke(cli.app, ["capabilities"])
    assert result.exit_code == 0
    assert "ble_scan" in result.output and "UNVERIFIED" in result.output


@pytest.mark.parametrize("args", [["photo"], ["wifi", "up"]])
def test_unbuilt_commands_exit_2(args: list[str]) -> None:
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert "not implemented until Phase" in result.output


def test_scan_saves_selected_address(monkeypatch: pytest.MonkeyPatch, in_tmp: Path) -> None:
    results = [
        ScanResult("20:15:DE:52:19:65", "Monitor", -40),
        ScanResult("D0:4B:1B:C3:23:24", "AIMB-G2_2324", -60),
    ]

    async def fake_scan(_timeout: float) -> list[ScanResult]:
        return results

    monkeypatch.setattr(cli, "scan", fake_scan)
    result = runner.invoke(cli.app, ["scan"], input="\n")
    assert result.exit_code == 0
    assert 'device_address = "D0:4B:1B:C3:23:24"' in (in_tmp / "config.toml").read_text()


def test_scan_nothing_found_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_scan(_timeout: float) -> list[ScanResult]:
        return []

    monkeypatch.setattr(cli, "scan", fake_scan)
    assert runner.invoke(cli.app, ["scan"]).exit_code == 1


def test_scan_reports_bluetooth_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiglasses.domain.errors import BluetoothUnavailableError

    async def fake_scan(_timeout: float) -> list[ScanResult]:
        raise BluetoothUnavailableError("radio off")

    monkeypatch.setattr(cli, "scan", fake_scan)
    result = runner.invoke(cli.app, ["scan", "--no-select"])
    assert result.exit_code == 1 and "radio off" in result.output


def test_info_command(
    monkeypatch: pytest.MonkeyPatch, gatt_services: list[GattService], gatt_reads: dict[str, bytes]
) -> None:
    use_fake(monkeypatch, FakeBleTransport(gatt_reads, gatt_services))
    result = runner.invoke(cli.app, ["info"])
    assert result.exit_code == 0
    assert "AM01G1_9.40.00_260114" in result.output


def test_gatt_dump_command_writes_json(
    monkeypatch: pytest.MonkeyPatch,
    gatt_services: list[GattService],
    gatt_reads: dict[str, bytes],
    in_tmp: Path,
) -> None:
    use_fake(monkeypatch, FakeBleTransport(gatt_reads, gatt_services))
    result = runner.invoke(cli.app, ["gatt-dump"])
    assert result.exit_code == 0
    data = json.loads((in_tmp / "logs" / "gatt_dump.json").read_text())
    assert data["mtu"] == 23


def test_listen_command_prints_and_summarises(
    monkeypatch: pytest.MonkeyPatch, gatt_services: list[GattService]
) -> None:
    transport = FakeBleTransport(services=gatt_services)
    use_fake(monkeypatch, transport)

    original = FakeBleTransport.subscribe

    async def subscribe_and_emit(self: FakeBleTransport, char: str, handler: Any) -> None:
        await original(self, char, handler)
        if char == AE02:
            handler(char, b"\xbc\x73")

    monkeypatch.setattr(FakeBleTransport, "subscribe", subscribe_and_emit)
    result = runner.invoke(cli.app, ["listen", "--duration", "0.1"])
    assert result.exit_code == 0
    assert "bc73" in result.output and "Raw summary" in result.output


def test_connection_error_is_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiglasses.domain.errors import DeviceNotFoundError

    @asynccontextmanager
    async def failing(config: Any, **_: Any) -> Any:
        raise DeviceNotFoundError("Glasses not found")
        yield

    monkeypatch.setattr(Glasses, "connect", staticmethod(failing))
    result = runner.invoke(cli.app, ["info"])
    assert result.exit_code == 1 and "Glasses not found" in result.output


def test_setup_logging_writes_json_lines(tmp_path: Path) -> None:
    setup_logging(tmp_path / "logs", verbose=True)
    logging.getLogger("aiglasses.test").info("hello %s", "world")
    root = logging.getLogger("aiglasses")
    for handler in root.handlers:
        handler.close()
    root.handlers.clear()
    line = json.loads((tmp_path / "logs" / "aiglasses.jsonl").read_text().splitlines()[0])
    assert line["msg"] == "hello world" and line["level"] == "INFO"
