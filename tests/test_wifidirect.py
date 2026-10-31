import pytest

from aiglasses.services import wifidirect
from aiglasses.services.wifidirect import (
    FakeHostNetwork,
    FakeWifiDirect,
    Severity,
    WifiDirectError,
    WifiDirectSession,
    preflight,
)


def severities(report: wifidirect.PreflightReport) -> dict[str, Severity]:
    return {c.name: c.severity for c in report.checks}


async def test_preflight_blocks_without_approval_and_without_pins() -> None:
    report = await preflight(FakeHostNetwork(), approved=False, pins_installed=False)
    assert not report.ok
    assert severities(report)["approval"] is Severity.BLOCK
    assert severities(report)["winrt packages"] is Severity.BLOCK


async def test_preflight_passes_when_approved_pinned_and_online() -> None:
    report = await preflight(FakeHostNetwork(), approved=True, pins_installed=True)
    assert report.ok and "band" not in severities(report)


async def test_preflight_warns_about_5ghz_and_blocks_when_internet_is_already_down() -> None:
    report = await preflight(
        FakeHostNetwork(band="5 GHz", internet=[False]), approved=True, pins_installed=True
    )
    assert severities(report)["band"] is Severity.WARN
    assert severities(report)["internet"] is Severity.BLOCK and not report.ok


async def test_preflight_never_shows_the_profile_name() -> None:
    report = await preflight(
        FakeHostNetwork(profile="MyHomeWifi"), approved=True, pins_installed=True
    )
    assert "MyHomeWifi" not in repr(report)


async def test_session_connects_as_group_owner_and_rolls_back() -> None:
    wifi, host = FakeWifiDirect(), FakeHostNetwork()
    async with WifiDirectSession(wifi, host, peer_prefix="AIMB-G2") as session:
        assert session.group is not None and session.group.channel == 6
        assert wifi.connect_calls == [("AIMB-G2_TEST", 15)]
    assert session.rollback is not None and session.rollback.clean
    assert wifi.disconnects == 1 and host.restored == ["home"]


async def test_one_retry_then_success() -> None:
    wifi = FakeWifiDirect(connect_errors=1)
    async with WifiDirectSession(wifi, FakeHostNetwork(), peer_prefix="AIMB-G2") as session:
        assert session.attempts == 2 and session.group is not None


async def test_gives_up_after_one_retry_and_still_rolls_back() -> None:
    wifi, host = FakeWifiDirect(connect_errors=5), FakeHostNetwork()
    session = WifiDirectSession(wifi, host, peer_prefix="AIMB-G2")
    with pytest.raises(WifiDirectError, match="2 attempt"):
        await session.__aenter__()
    assert wifi.disconnects == 1 and host.restored == ["home"]
    assert session.rollback is not None and session.rollback.clean


async def test_no_peer_found_is_a_failure_with_rollback() -> None:
    wifi, host = FakeWifiDirect(peers=[None]), FakeHostNetwork()
    session = WifiDirectSession(wifi, host, peer_prefix="AIMB-G2")
    with pytest.raises(WifiDirectError, match="no peer"):
        await session.__aenter__()
    assert wifi.disconnects == 1 and host.restored == ["home"]


async def test_hung_connect_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifidirect, "CONNECT_TIMEOUT_SECONDS", 0.05)
    wifi, host = FakeWifiDirect(hang=True), FakeHostNetwork()
    session = WifiDirectSession(wifi, host, peer_prefix="AIMB-G2")
    with pytest.raises(WifiDirectError):
        await session.__aenter__()
    assert wifi.disconnects == 1


async def test_rollback_runs_when_the_body_raises() -> None:
    wifi, host = FakeWifiDirect(), FakeHostNetwork()
    with pytest.raises(RuntimeError):
        async with WifiDirectSession(wifi, host, peer_prefix="AIMB-G2"):
            raise RuntimeError("download failed")
    assert wifi.disconnects == 1 and host.restored == ["home"]


async def test_rollback_reports_a_failed_restore_and_no_internet() -> None:
    host = FakeHostNetwork(restore_error=True, internet=[False])
    async with WifiDirectSession(FakeWifiDirect(), host, peer_prefix="AIMB-G2") as session:
        pass
    report = session.rollback
    assert report is not None and not report.clean
    assert report.profile_restored is False and report.internet_ok is False


async def test_nothing_to_restore_when_the_pc_was_offline() -> None:
    host = FakeHostNetwork(profile=None)
    async with WifiDirectSession(FakeWifiDirect(), host, peer_prefix="AIMB-G2") as session:
        pass
    assert session.rollback is not None and session.rollback.clean
    assert session.rollback.profile_restored is None and host.restored == []
