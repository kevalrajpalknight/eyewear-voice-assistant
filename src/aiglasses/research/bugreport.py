"""Triage an Android bug report for evidence about the Cyan app and the glasses.

Only lines about the app package, the glasses MAC address or the glasses' Wi-Fi Direct name are
kept. Passphrases and the phone's own addresses are never collected. Read-only.
"""

import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE = "com.aitowe.aitoglasses"
GLASSES_MAC = "D0:4B:1B:C3:23:24"
GLASSES_P2P_NAME = "AIMB-G2_D04B1BC32324"

SECTION = re.compile(
    r"^(?:DUMP OF SERVICE (?:CRITICAL |HIGH )?([\w.\-:]+):"
    r"|------ (.+?) \(.*\) ------|------ (.+?) ------)"
)
GATT_HISTORY = re.compile(r"^\s*(\d\d-\d\d \d\d:\d\d:\d\d) - (\d+)ms : " + GLASSES_MAC)
LOG_TIME = re.compile(r"^\s*(\d\d-\d\d \d\d:\d\d:\d\d):\d+ ")
P2P_RECORD = re.compile(r"time=(\d\d-\d\d \d\d:\d\d:\d\d\.\d+) .*what=(\S+)(?: sender=(\S+))?")
USAGE_EVENT = re.compile(r'time="([^"]+)" type=(\S+) package=(\S+)(?: class=(\S+))?')
SOCKET = re.compile(r"^\s*tcp\s+(\S+)\s+\d+\s+\d+\s+\S+\s+(\S+)\s+users:\(\(\"[^\"]*aitoglasses")


@dataclass
class P2pGroup:
    network: str = ""
    phone_is_group_owner: bool | None = None
    group_owner_ip: str = ""
    glasses_p2p_address: str = ""


@dataclass
class Triage:
    zip_name: str
    snoop_files: list[tuple[str, int]] = field(default_factory=list)
    gatt_connections: list[str] = field(default_factory=list)
    audio_events: list[str] = field(default_factory=list)
    p2p_events: list[tuple[str, str]] = field(default_factory=list)
    p2p_group: P2pGroup = field(default_factory=P2pGroup)
    app_events: list[tuple[str, str, str]] = field(default_factory=list)
    remote_endpoints: set[str] = field(default_factory=set)


def _main_text_name(zf: zipfile.ZipFile) -> str | None:
    for info in zf.infolist():
        if info.filename.startswith("bugreport") and info.filename.endswith(".txt"):
            return info.filename
    return None


def triage_zip(path: Path) -> Triage:
    result = Triage(zip_name=path.name)
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            low = info.filename.lower()
            if "btsnoop" in low or low.endswith(".cfa"):
                result.snoop_files.append((info.filename, info.file_size))
        name = _main_text_name(zf)
        if name is None:
            return result
        with zf.open(name) as fh:
            _scan(fh, result)
    return result


def _scan(fh: Iterable[bytes], result: Triage) -> None:
    section = ""
    pending_client = False
    p2p_started = False
    for raw in fh:
        line = raw.decode("utf-8", "replace").rstrip()
        match = SECTION.match(line)
        if match:
            section = next(g for g in match.groups() if g)
            continue
        if section == "bluetooth_manager" and GATT_HISTORY.match(line):
            result.gatt_connections.append(line.strip())
        elif section == "audio" and GLASSES_MAC in line and LOG_TIME.match(line):
            if any(k in line for k in ("BT connected", "unavailable", "now available")):
                result.audio_events.append(line.strip()[:140])
        elif section == "usagestats" and PACKAGE in line:
            event = USAGE_EVENT.search(line)
            if event and event.group(3) == PACKAGE:
                result.app_events.append(
                    (event.group(1), event.group(2), (event.group(4) or "").split(".")[-1])
                )
        elif section == "DETAILED SOCKET STATE":
            sock = SOCKET.match(line)
            if sock:
                result.remote_endpoints.add(sock.group(2).replace("::ffff:", ""))
        elif section == "wifip2p":
            p2p_started = _scan_p2p(line, result, p2p_started)
            if "Client: Device:" in line:
                pending_client = GLASSES_P2P_NAME in line
            elif pending_client and line.strip().startswith("deviceAddress:"):
                result.p2p_group.glasses_p2p_address = line.split(":", 1)[1].strip()
                pending_client = False


def _scan_p2p(line: str, result: Triage, started: bool) -> bool:
    record = P2P_RECORD.search(line)
    if record:
        time, what, sender = record.groups()
        if (sender and PACKAGE in sender) or (started and what.startswith("WifiP2pMonitor.P2P_")):
            result.p2p_events.append(
                (time, what.replace("WifiP2pMonitor.", "").replace("WifiP2pManager.", ""))
            )
            return True
        if started and what.startswith(("WifiP2pMonitor.AP_STA", "TETHER")):
            result.p2p_events.append((time, what.replace("WifiP2pMonitor.", "")))
        return started
    group = result.p2p_group
    stripped = line.strip()
    if stripped.startswith("mGroup network:"):
        group.network = stripped.split(":", 1)[1].strip()
    elif stripped.startswith("isGO:"):
        group.phone_is_group_owner = stripped.endswith("true")
    elif "groupOwnerAddress:" in stripped:
        group.group_owner_ip = stripped.split("groupOwnerAddress:", 1)[1].strip().lstrip("/")
    return started
