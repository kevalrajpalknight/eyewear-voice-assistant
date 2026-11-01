"""Wi-Fi Direct helper process. Runs in the ISOLATED environment (tools/wifidirect-env).

Standard library plus `winrt` only: it must not import aiglasses, which keeps the main
program's BLE packages untouched. One JSON object per line on stdin, one reply per line on
stdout; commands preflight, find_peer, connect, status, disconnect, restore (see
docs/WIFI_DIRECT_HELPER.md). Errors reply {"ok": false, "error": "short text"}.

`--fake` runs a scripted backend with no radio (used by the tests). The real backend is
UNVERIFIED on hardware: the first real run is a supervised test the user approves.
"""

import asyncio
import json
import re
import socket
import subprocess
import sys
from typing import Any

PROBE = ("one.one.one.one", 443)
PROBE_TIMEOUT_S = 3.0


class FakeBackend:
    def __init__(self) -> None:
        self.connected = False

    async def preflight(self) -> dict[str, Any]:
        return {"profile": "fake-home", "band": "2.4 GHz"}

    async def find_peer(self, prefix: str, timeout: float) -> str | None:
        return f"{prefix}_FAKE"

    async def connect(self, peer: str, intent: int, timeout: float) -> dict[str, Any]:
        self.connected = True
        return {"band": "2.4 GHz", "channel": 6, "glasses_ip": "192.168.49.2"}

    async def disconnect(self) -> None:
        self.connected = False

    async def restore(self, profile: str) -> None:
        return None

    async def status(self) -> dict[str, Any]:
        return {"internet": not self.connected, "connected": self.connected}


class WinrtBackend:
    """Real radio code. Uses the winrt projection; names follow its snake_case convention."""

    def __init__(self) -> None:
        self._device: Any = None

    @staticmethod
    def _netsh(*args: str) -> str:
        done = subprocess.run(["netsh", "wlan", *args], capture_output=True, text=True, check=False)
        return done.stdout

    async def preflight(self) -> dict[str, Any]:
        text = self._netsh("show", "interfaces")
        profile = re.search(r"^\s*Profile\s*:\s*(.+)$", text, re.MULTILINE)
        band = re.search(r"^\s*Band\s*:\s*(.+)$", text, re.MULTILINE)
        return {
            "profile": profile.group(1).strip() if profile else None,
            "band": band.group(1).strip() if band else None,
        }

    async def find_peer(self, prefix: str, timeout: float) -> str | None:
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.devices.wifidirect import WiFiDirectDevice

        selector = WiFiDirectDevice.get_device_selector()
        found = await asyncio.wait_for(
            DeviceInformation.find_all_async_aqs_filter(selector), timeout
        )
        for info in found:
            if str(info.name).startswith(prefix):
                return str(info.id)
        return None

    async def connect(self, peer: str, intent: int, timeout: float) -> dict[str, Any]:
        from winrt.windows.devices.wifidirect import (
            WiFiDirectConnectionParameters,
            WiFiDirectDevice,
        )

        parameters = WiFiDirectConnectionParameters()
        parameters.group_owner_intent = intent
        self._device = await asyncio.wait_for(
            WiFiDirectDevice.from_id_async(peer, parameters), timeout
        )
        pairs = self._device.get_connection_endpoint_pairs()
        ip = str(pairs[0].remote_host_name.canonical_name) if len(pairs) else None
        return {"band": None, "channel": None, "glasses_ip": ip}

    async def disconnect(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    async def restore(self, profile: str) -> None:
        self._netsh("connect", f"name={profile}")

    async def status(self) -> dict[str, Any]:
        try:
            with socket.create_connection(PROBE, PROBE_TIMEOUT_S):
                return {"internet": True, "connected": self._device is not None}
        except OSError:
            return {"internet": False, "connected": self._device is not None}


async def handle(backend: Any, request: dict[str, Any]) -> dict[str, Any]:
    command = request["cmd"]
    reply: dict[str, Any]
    if command == "preflight":
        reply = await backend.preflight()
        return reply
    if command == "find_peer":
        return {"peer": await backend.find_peer(request["prefix"], float(request["timeout"]))}
    if command == "connect":
        reply = await backend.connect(
            request["peer"], int(request["group_owner_intent"]), float(request["timeout"])
        )
        return reply
    if command == "disconnect":
        await backend.disconnect()
        return {}
    if command == "restore":
        await backend.restore(request["profile"])
        return {}
    if command == "status":
        reply = await backend.status()
        return reply
    raise ValueError(f"unknown command {command!r}")


async def serve(backend: Any) -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            await backend.disconnect()  # the parent went away: leave the group
            return
        request = json.loads(line)
        try:
            reply = {"ok": True, **await handle(backend, request)}
        except Exception as exc:
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:120]}
        print(json.dumps({"id": request.get("id"), **reply}), flush=True)


if __name__ == "__main__":
    asyncio.run(serve(FakeBackend() if "--fake" in sys.argv else WinrtBackend()))
