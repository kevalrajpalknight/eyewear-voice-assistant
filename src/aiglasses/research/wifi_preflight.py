"""Read-only check of the PC's current Wi-Fi band and channel (parses `netsh wlan show interfaces`).

The network name is read only so a later rollback can reconnect; it is never printed or logged.
"""

import re
import subprocess
from dataclasses import dataclass, field


@dataclass
class WifiStatus:
    connected: bool
    state: str
    radio_type: str | None = None
    channel: int | None = None
    band: str | None = None
    profile: str | None = field(default=None, repr=False)  # for rollback only; never shown


def _value(text: str, key: str) -> str | None:
    match = re.search(rf"^\s*{key}\s*:\s*(.+?)\s*$", text, re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else None


def band_for_channel(channel: int) -> str:
    """Band implied by a channel number (netsh normally states the band itself)."""
    return "2.4 GHz" if 1 <= channel <= 14 else "5 GHz"


def parse_interfaces(text: str) -> WifiStatus:
    state = _value(text, "State") or "unknown"
    channel_text = _value(text, "Channel")
    channel = int(channel_text) if channel_text and channel_text.isdigit() else None
    band = _value(text, "Band")
    if band is None and channel is not None:
        band = band_for_channel(channel)
    return WifiStatus(
        connected=state.lower() == "connected",
        state=state,
        radio_type=_value(text, "Radio type"),
        channel=channel,
        band=band,
        profile=_value(text, "Profile"),
    )


def read_status() -> WifiStatus:
    done = subprocess.run(
        ["netsh", "wlan", "show", "interfaces"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return parse_interfaces(done.stdout)


def advice(status: WifiStatus) -> str:
    """What to do before a Wi-Fi Direct test with glasses that are probably 2.4 GHz only."""
    if not status.connected:
        return (
            "The PC is not connected to Wi-Fi, so nothing competes for the radio. Good for "
            "the test; internet is unavailable until the normal network is restored."
        )
    band = status.band or "unknown band"
    where = f"Connected on {band} (channel {status.channel})."
    if band.startswith("2.4"):
        return (
            f"{where} A Wi-Fi Direct group would normally share that channel. Fine for the "
            "test; if the group does not form, disconnect from Wi-Fi first."
        )
    return (
        f"{where} The glasses are probably 2.4 GHz only (unverified), and one adapter "
        "usually cannot hold a 5 GHz connection and a 2.4 GHz group at the same time. "
        "Recommended: connect the PC to a 2.4 GHz network, or disconnect from Wi-Fi, before "
        "Step 2, and restore the normal network afterwards."
    )
