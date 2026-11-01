"""Wi-Fi Direct helper process. Runs in the ISOLATED environment (tools/wifidirect-env).

Standard library plus `winrt` only: it must not import aiglasses, which keeps the main
program's BLE packages untouched. One JSON object per line on stdin, one reply per line on
stdout; commands preflight, find_peer, connect, status, disconnect, restore (see
docs/WIFI_DIRECT_HELPER.md). Errors reply {"ok": false, "error": "short text"}.

`--fake` runs a scripted backend with no radio (used by the tests). The real backend follows the
Windows Wi-Fi Direct documentation but is UNVERIFIED on hardware (see the list in
docs/WIFI_DIRECT_HELPER.md). Wi-Fi network names are never printed or logged by this process.
"""

import asyncio
import json
import socket
import subprocess
import sys
from typing import Any

ADVERTISE_START_S = 5.0
RESCAN_S = 6.0
PROBE = ("one.one.one.one", 443)
PROBE_TIMEOUT_S = 3.0
RESTORE_ATTEMPTS = 3
RESTORE_WAIT_S = 10.0
POLL_S = 1.0
UNPAIR_TIMEOUT_S = 10.0


class FakeBackend:
    def __init__(self) -> None:
        self.connected = False

    async def advertise(self) -> None:
        return None

    async def preflight(self) -> dict[str, Any]:
        return {"profile": "fake-home", "band": "2.4 GHz"}

    async def find_peer(self, prefix: str, timeout: float) -> str | None:
        return f"{prefix}_FAKE"

    async def connect(self, peer: str, intent: int, timeout: float) -> dict[str, Any]:
        self.connected = True
        return {"band": "2.4 GHz", "channel": 6, "glasses_ip": "192.168.49.2"}

    async def disconnect(self) -> None:
        self.connected = False

    async def restore(self, profile: str) -> dict[str, Any]:
        return {"connected": True, "internet": True, "attempts": 1}

    async def status(self) -> dict[str, Any]:
        return {"internet": not self.connected, "connected": self.connected, "channels": [6]}


def netsh(*args: str) -> str:
    done = subprocess.run(["netsh", "wlan", *args], capture_output=True, text=True, check=False)
    return done.stdout


def interface_blocks() -> list[dict[str, str]]:
    """`netsh wlan show interfaces` as a list of key/value blocks (one per Wi-Fi adapter)."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in netsh("show", "interfaces").splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key == "name" and current:
            blocks.append(current)
            current = {}
        current[key] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def internet_up() -> bool:
    try:
        with socket.create_connection(PROBE, PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


class WinrtBackend:
    """Real radio code (unverified). Pairing is push-button; the PC asks to be group owner."""

    def __init__(self) -> None:
        self._device: Any = None
        self._info: Any = None
        self._prefix = ""
        self.seen: list[str] = []
        self._publisher: Any = None
        self._listener: Any = None
        self._token: Any = None
        self._requests: asyncio.Queue[Any] | None = None
        self._request: Any = None

    async def preflight(self) -> dict[str, Any]:
        for block in interface_blocks():
            if block.get("state", "").lower() == "connected":
                return {"profile": block.get("profile"), "band": block.get("band")}
        return {"profile": None, "band": None}

    async def advertise(self) -> None:
        """PC hosts: advertise as an autonomous group owner and queue connection requests."""
        from winrt.windows.devices.wifidirect import (
            WiFiDirectAdvertisementListenStateDiscoverability,
            WiFiDirectAdvertisementPublisher,
            WiFiDirectAdvertisementPublisherStatus,
            WiFiDirectConfigurationMethod,
            WiFiDirectConnectionListener,
        )

        loop = asyncio.get_running_loop()
        requests: asyncio.Queue[Any] = asyncio.Queue()
        listener = WiFiDirectConnectionListener()

        def requested(sender: Any, args: Any) -> None:
            loop.call_soon_threadsafe(requests.put_nowait, args.get_connection_request())

        self._token = listener.add_connection_requested(requested)
        publisher = WiFiDirectAdvertisementPublisher()
        advertisement = publisher.advertisement
        advertisement.is_autonomous_group_owner_enabled = True
        advertisement.listen_state_discoverability = (
            WiFiDirectAdvertisementListenStateDiscoverability.NORMAL
        )
        advertisement.supported_configuration_methods.append(
            WiFiDirectConfigurationMethod.PUSH_BUTTON
        )
        self._listener, self._publisher, self._requests = listener, publisher, requests
        publisher.start()
        for _ in range(int(ADVERTISE_START_S / 0.25)):
            if publisher.status == WiFiDirectAdvertisementPublisherStatus.STARTED:
                return
            if publisher.status == WiFiDirectAdvertisementPublisherStatus.ABORTED:
                break
            await asyncio.sleep(0.25)
        raise RuntimeError(f"advertisement {publisher.status.name}")

    async def _wait_for_request(self, timeout: float) -> str | None:
        assert self._requests is not None
        try:
            self._request = await asyncio.wait_for(self._requests.get(), timeout)
        except asyncio.TimeoutError:
            self.seen = []
            return None
        self._info = self._request.device_information
        self.seen = [str(self._info.name)[:12]]
        return str(self._info.id)

    async def find_peer(self, prefix: str, timeout: float) -> str | None:
        if self._requests is not None:
            return await self._wait_for_request(timeout)
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.devices.wifidirect import WiFiDirectDevice, WiFiDirectDeviceSelectorType

        self._prefix = prefix
        selector = WiFiDirectDevice.get_device_selector(
            WiFiDirectDeviceSelectorType.ASSOCIATION_ENDPOINT
        )
        loop = asyncio.get_running_loop()
        seen: dict[str, Any] = {}
        match: asyncio.Future[Any] = loop.create_future()

        def added(sender: Any, info: Any) -> None:
            def record() -> None:
                seen[str(info.id)] = info
                if str(info.name).startswith(prefix) and not match.done():
                    match.set_result(info)

            loop.call_soon_threadsafe(record)

        deadline = loop.time() + timeout
        while loop.time() < deadline:  # restart the watcher so every pass is a fresh active scan
            watcher = DeviceInformation.create_watcher_aqs_filter(selector)
            token = watcher.add_added(added)
            watcher.start()
            try:
                slice_s = min(RESCAN_S, max(deadline - loop.time(), 0.1))
                self._info = await asyncio.wait_for(asyncio.shield(match), slice_s)
                return str(self._info.id)
            except asyncio.TimeoutError:
                continue
            finally:
                watcher.remove_added(token)
                watcher.stop()
        self.seen = sorted(str(i.name)[:12] for i in seen.values())
        return None

    async def connect(self, peer: str, intent: int, timeout: float) -> dict[str, Any]:
        from winrt.windows.devices.enumeration import (
            DeviceInformation,
            DevicePairingProtectionLevel,
            DevicePairingResultStatus,
        )
        from winrt.windows.devices.wifidirect import (
            WiFiDirectConfigurationMethod,
            WiFiDirectConnectionParameters,
            WiFiDirectDevice,
            WiFiDirectDeviceSelectorType,
            WiFiDirectPairingProcedure,
        )

        parameters = WiFiDirectConnectionParameters()
        parameters.group_owner_intent = intent
        parameters.preferred_pairing_procedure = WiFiDirectPairingProcedure.GROUP_OWNER_NEGOTIATION
        custom = self._info.pairing.custom
        token = custom.add_pairing_requested(lambda sender, args: args.accept())
        kinds = WiFiDirectConnectionParameters.get_device_pairing_kinds(
            WiFiDirectConfigurationMethod.PUSH_BUTTON
        )
        try:
            result = await asyncio.wait_for(
                custom.pair_with_protection_level_and_settings_async(
                    kinds, DevicePairingProtectionLevel.DEFAULT, parameters
                ),
                timeout,
            )
        finally:
            custom.remove_pairing_requested(token)
        paired = (DevicePairingResultStatus.PAIRED, DevicePairingResultStatus.ALREADY_PAIRED)
        if result.status not in paired:
            raise RuntimeError(f"pairing {result.status.name}")
        selector = WiFiDirectDevice.get_device_selector(
            WiFiDirectDeviceSelectorType.DEVICE_INTERFACE
        )
        interfaces = (
            []
            if self._request is not None
            else await asyncio.wait_for(
                DeviceInformation.find_all_async_aqs_filter(selector), timeout
            )
        )
        if self._request is not None:
            device_id = str(self._request.device_information.id)
        else:
            match = next((i for i in interfaces if str(i.name).startswith(self._prefix)), None)
            if match is None:
                raise RuntimeError("paired, but no device interface appeared")
            device_id = str(match.id)
        self._device = await asyncio.wait_for(
            WiFiDirectDevice.from_id_async(device_id, parameters), timeout
        )
        pairs = self._device.get_connection_endpoint_pairs()
        ip = str(pairs[0].remote_host_name.canonical_name) if len(pairs) else None
        return {"band": None, "channel": None, "glasses_ip": ip}

    async def disconnect(self) -> None:
        device, self._device = self._device, None
        if device is not None:
            device.close()
        request, self._request = self._request, None
        if request is not None:
            request.close()
        publisher, self._publisher = self._publisher, None
        if publisher is not None:
            publisher.stop()
        listener, self._listener = self._listener, None
        if listener is not None and self._token is not None:
            listener.remove_connection_requested(self._token)
        self._requests = None
        info, self._info = self._info, None
        if info is not None:  # forget the pairing so the next run starts clean
            try:
                await asyncio.wait_for(info.pairing.unpair_async(), UNPAIR_TIMEOUT_S)
            except Exception:  # best effort: a missing pairing is fine
                return

    async def restore(self, profile: str) -> dict[str, Any]:
        """Reconnect the saved profile by name, wait for it, and check the internet."""
        for attempts in range(1, RESTORE_ATTEMPTS + 1):
            netsh("connect", f"name={profile}")
            for _ in range(int(RESTORE_WAIT_S / POLL_S)):
                await asyncio.sleep(POLL_S)
                joined = any(
                    b.get("state", "").lower() == "connected" and b.get("profile") == profile
                    for b in interface_blocks()
                )
                if joined and await asyncio.to_thread(internet_up):
                    return {"connected": True, "internet": True, "attempts": attempts}
        raise RuntimeError("could not reconnect the saved Wi-Fi profile")

    async def status(self) -> dict[str, Any]:
        blocks = interface_blocks()
        channels = sorted({int(b["channel"]) for b in blocks if b.get("channel", "").isdigit()})
        online = await asyncio.to_thread(internet_up)
        return {"internet": online, "connected": self._device is not None, "channels": channels}


async def handle(backend: Any, request: dict[str, Any]) -> dict[str, Any]:
    command = request["cmd"]
    reply: dict[str, Any] = {}
    if command == "preflight":
        reply = await backend.preflight()
    elif command == "find_peer":
        peer = await backend.find_peer(request["prefix"], float(request["timeout"]))
        reply = {"peer": peer, "seen": getattr(backend, "seen", [])[:8]}
    elif command == "advertise":
        await backend.advertise()
    elif command == "connect":
        reply = await backend.connect(
            request["peer"], int(request["group_owner_intent"]), float(request["timeout"])
        )
    elif command == "disconnect":
        await backend.disconnect()
    elif command == "restore":
        reply = await backend.restore(request["profile"])
    elif command == "status":
        reply = await backend.status()
    else:
        raise ValueError(f"unknown command {command!r}")
    return reply


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
