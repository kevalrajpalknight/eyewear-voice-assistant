import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from aiglasses.domain.errors import ConfirmationRequired, GlassesError, WriteBlockedError
from aiglasses.domain.models import GlassesEvent, MediaCounts
from aiglasses.research.glasses_http import HttpGuardError
from aiglasses.services.control import APPROVED_WIFI_DIRECT_FRAME, PhotoResult
from aiglasses.services.photo_fetch import FakeHelper, HelperWifiDirect
from aiglasses.services.wifi_run import IpWatcher, WifiDirectRun
from tests.test_facade import Device, glasses

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 40


class FakeHttp:
    log: ClassVar[list[str]] = []

    def __init__(self, ip: str, save_dir: Path) -> None:
        self.save_dir = save_dir

    def get(self, path: str) -> bytes:
        FakeHttp.log.append(path)
        return b"20261101_090000.jpg\n20261101_100000.jpg\n"

    def download(self, path: str, dest: Path, *, expect_jpeg: bool = False) -> Path:
        FakeHttp.log.append(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(JPEG)
        return dest


async def ip_events(ip: str | None = "192.168.49.2") -> AsyncIterator[GlassesEvent]:
    await asyncio.sleep(0.01)
    if ip is not None:
        yield GlassesEvent("ip_address", b"", 0.0, fields={"ip": ip})
    await asyncio.sleep(60)


async def photo() -> PhotoResult:
    return PhotoResult(MediaCounts(1, 0, 0), MediaCounts(2, 0, 0), "ok", "00")


def build(
    variant: str,
    tmp_path: Path,
    *,
    helper: FakeHelper | None = None,
    ip: str | None = "192.168.49.2",
    http: Any = FakeHttp,
    enable_calls: list[int] | None = None,
    take_photo: Any = photo,
) -> tuple[WifiDirectRun, FakeHelper]:
    helper = helper or FakeHelper()
    calls = enable_calls if enable_calls is not None else []

    async def enable() -> None:
        calls.append(1)

    FakeHttp.log = []
    run = WifiDirectRun(
        variant=variant, adapter=HelperWifiDirect(helper), enable=enable,
        ip=IpWatcher(ip_events(ip)), save_dir=tmp_path, peer_prefix="AIMB-G2", http=http,
        take_photo=take_photo if variant == "full" else None,
    )  # fmt: skip
    return run, helper


async def test_a_connects_reads_the_ip_and_restores_without_any_http(tmp_path: Path) -> None:
    calls: list[int] = []
    run, helper = build("connect", tmp_path, enable_calls=calls)
    report = await run.run()
    assert calls == [1] and FakeHttp.log == []
    assert helper.commands[-2:] == ["disconnect", "restore"] or "restore" in helper.commands
    assert report.wifi_back is True and report.internet_after is True and not report.errors
    names = [s.name for s in report.stages]
    assert names[:4] == ["preflight", "enable (BLE, once)", "find peer + join", "read IP event"]
    assert (
        report.internet_during_group is True and report.channel == "6"
    )  # the fake does not model loss


async def test_b_downloads_one_existing_photo_and_checks_the_jpeg(tmp_path: Path) -> None:
    run, _ = build("existing", tmp_path)
    report = await run.run()
    assert FakeHttp.log == ["/files/media.config", "/files/20261101_100000.jpg"]
    assert report.saved and report.jpeg_ok and (tmp_path / "20261101_100000.jpg").exists()


async def test_c_takes_a_photo_first_and_needs_the_count_to_rise(tmp_path: Path) -> None:
    run, helper = build("full", tmp_path)
    report = await run.run()
    assert [s.name for s in report.stages][:3] == ["preflight", "take photo", "enable (BLE, once)"]
    assert report.saved

    async def no_rise() -> PhotoResult:
        return PhotoResult(MediaCounts(1, 0, 0), MediaCounts(1, 0, 0), "ok", "00")

    stalled, helper2 = build("full", tmp_path, take_photo=no_rise)
    bad = await stalled.run()
    assert "did not rise" in bad.errors[0] and "connect" not in helper2.commands
    assert helper and bad.wifi_back is None


async def test_a_missing_ip_event_stops_the_run_and_still_rolls_back(tmp_path: Path) -> None:
    run, helper = build("connect", tmp_path, ip=None)
    run.ip = IpWatcher(ip_events(None))
    import aiglasses.services.wifi_run as module

    original = module.IP_WAIT_S
    module.IP_WAIT_S = 0.2
    try:
        report = await run.run()
    finally:
        module.IP_WAIT_S = original
    assert report.stopped_early and "no address event" in report.errors[0]
    assert (
        "disconnect" in helper.commands
        and "restore" in helper.commands
        and report.wifi_back is True
    )


async def test_a_failed_restore_prints_the_manual_step_without_names_or_addresses(
    tmp_path: Path,
) -> None:
    helper = FakeHelper(profile="MyHomeWifi", fail={"restore"})
    run, _ = build("connect", tmp_path, helper=helper)
    report = await run.run()
    text = report.render(secrets=("MyHomeWifi",))
    assert report.wifi_back is False and "Windows Settings" in text
    assert "MyHomeWifi" not in text and "192.168" not in text


async def test_a_5_ghz_pc_stops_before_anything_is_sent(tmp_path: Path) -> None:
    calls: list[int] = []
    run, helper = build("connect", tmp_path, helper=FakeHelper(band="5 GHz"), enable_calls=calls)
    report = await run.run()
    assert calls == [] and "2.4 GHz" in report.errors[0] and "connect" not in helper.commands


async def test_a_download_failure_is_reported_and_wifi_still_comes_back(tmp_path: Path) -> None:
    class Failing(FakeHttp):
        def get(self, path: str) -> bytes:
            raise HttpGuardError("HTTP 500")

    run, helper = build("existing", tmp_path, http=Failing)
    report = await run.run()
    assert not report.saved and "HTTP 500" in report.errors[0] and report.wifi_back is True
    assert "restore" in helper.commands


def test_the_report_masks_addresses_and_names() -> None:
    from aiglasses.services.wifi_run import RunReport

    report = RunReport("connect", errors=["could not reach 192.168.49.2 on HomeNet"])
    text = report.render(secrets=("HomeNet",))
    assert "[address]" in text and "[name]" in text and "192.168" not in text


# --- the BLE enable frame ---------------------------------------------------------------------


async def test_only_the_approved_frame_is_sent_and_only_once(tmp_path: Path) -> None:
    device = Device()
    g = glasses(device, tmp_path)
    await g.enable_wifi_direct(approved_frame=APPROVED_WIFI_DIRECT_FRAME)
    assert [w[1].hex() for w in device.writes] == [APPROVED_WIFI_DIRECT_FRAME]
    with pytest.raises(WriteBlockedError):
        await g.enable_wifi_direct(approved_frame=APPROVED_WIFI_DIRECT_FRAME)
    assert len(device.writes) == 1


async def test_any_other_frame_and_dry_run_refuse_before_writing(tmp_path: Path) -> None:
    device = Device()
    with pytest.raises(ConfirmationRequired):
        await glasses(device, tmp_path).enable_wifi_direct(approved_frame="bc410400935c02010402")
    with pytest.raises(GlassesError, match="dry run"):
        await glasses(device, tmp_path, dry_run=True).enable_wifi_direct(
            approved_frame=APPROVED_WIFI_DIRECT_FRAME
        )
    assert device.writes == []


async def test_pc_hosts_mode_advertises_before_the_enable_write_and_stops_on_early_failure(
    tmp_path: Path,
) -> None:
    order: list[str] = []
    helper = FakeHelper()
    run, _ = build("connect", tmp_path, helper=helper)
    run.pc_hosts = True

    async def enable() -> None:
        order.append(helper.commands[-1])

    run.enable = enable
    report = await run.run()
    assert order == ["advertise"] and not report.errors
    assert report.stages[1].name == "advertise (PC hosts)"

    broken = FakeHelper()

    async def refused() -> None:
        raise GlassesError("write refused")

    run2, _ = build("connect", tmp_path, helper=broken)
    run2.pc_hosts, run2.enable = True, refused
    bad = await run2.run()
    assert "write refused" in bad.errors[0] and "disconnect" in broken.commands
