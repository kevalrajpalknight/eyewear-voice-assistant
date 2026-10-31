from typer.testing import CliRunner

from aiglasses.cli import main as cli
from aiglasses.research import wifi_preflight as wp

FIVE_GHZ = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : SecretHomeNetwork
    BSSID                  : aa:bb:cc:dd:ee:ff
    Radio type             : 802.11ax
    Authentication         : WPA2-Personal
    Band                   : 5 GHz
    Channel                : 36
    Profile                : SecretHomeNetwork
"""
TWO_GHZ = FIVE_GHZ.replace("5 GHz", "2.4 GHz").replace("36", "6")
NO_BAND = FIVE_GHZ.replace("    Band                   : 5 GHz\n", "").replace("36", "11")
DISCONNECTED = "    Name : Wi-Fi\n    State : disconnected\n"


def test_parses_band_channel_and_radio() -> None:
    status = wp.parse_interfaces(FIVE_GHZ)
    assert (status.connected, status.band, status.channel, status.radio_type) == (
        True,
        "5 GHz",
        36,
        "802.11ax",
    )
    assert status.profile == "SecretHomeNetwork"
    assert "SecretHomeNetwork" not in repr(status)  # the name never leaks through repr


def test_band_is_derived_from_the_channel_when_missing() -> None:
    status = wp.parse_interfaces(NO_BAND)
    assert status.band == "2.4 GHz" and status.channel == 11


def test_advice_for_each_case() -> None:
    assert "2.4 GHz network" in wp.advice(wp.parse_interfaces(FIVE_GHZ))
    assert "share that channel" in wp.advice(wp.parse_interfaces(TWO_GHZ))
    disconnected = wp.parse_interfaces(DISCONNECTED)
    assert not disconnected.connected and "not connected" in wp.advice(disconnected)


def test_cli_never_prints_the_network_name(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(wp, "read_status", lambda: wp.parse_interfaces(FIVE_GHZ))
    result = CliRunner().invoke(cli.app, ["wifi", "preflight"])
    assert result.exit_code == 0
    assert "5 GHz" in result.output and "36" in result.output
    assert "SecretHomeNetwork" not in result.output
