"""Supervised Wi-Fi Direct runs A (connect only), B (plus one existing photo), C (full flow).

Every stage is timed. The Wi-Fi rollback runs on every exit path and its result is part of the
report. The report never contains Wi-Fi names, passphrases or IP addresses: they are masked before
anything is printed, so it can be pasted as it is.

The BLE enable frame is sent once per run through `enable`, which the caller wires to the one
approved frame. If the IP event never arrives the run STOPS: nothing scans the network.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiglasses.domain.errors import GlassesError
from aiglasses.domain.models import GlassesEvent
from aiglasses.research.glasses_http import JPEG_MARKER
from aiglasses.services.control import PhotoResult
from aiglasses.services.photo_fetch import FILE_PREFIX, IMAGE_NAME, LIST_PATH, HelperWifiDirect
from aiglasses.services.wifidirect import WifiDirectSession

IP_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
IP_WAIT_S = 15.0
VARIANTS = ("connect", "existing", "full")
MANUAL_RESTORE = (
    "Your Wi-Fi may not be back. Open Windows Settings > Network & internet > Wi-Fi, switch Wi-Fi "
    "off and on, and choose your usual network. If the glasses stay listed as a device, remove "
    "them under Bluetooth & devices > Devices."
)


class IpEventMissing(GlassesError):
    """The glasses never reported their address over BLE. The run stops; no scanning."""


@dataclass
class Stage:
    name: str
    seconds: float
    ok: bool
    note: str = ""


@dataclass
class RunReport:
    variant: str
    stages: list[Stage] = field(default_factory=list)
    band: str | None = None
    channel: str | None = None
    internet_during_group: bool | None = None
    wifi_back: bool | None = None
    internet_after: bool | None = None
    errors: list[str] = field(default_factory=list)
    saved: bool = False
    jpeg_ok: bool | None = None
    stopped_early: bool = False

    def render(self, secrets: tuple[str, ...] = ()) -> str:
        yes = {True: "yes", False: "NO", None: "n/a"}
        lines = [f"Wi-Fi Direct run: {self.variant}", "Stages:"]
        for stage in self.stages:
            mark = "ok " if stage.ok else "FAIL"
            note = f"  ({stage.note})" if stage.note else ""
            lines.append(f"  {mark} {stage.name:<22} {stage.seconds:6.2f} s{note}")
        lines += [
            f"Group band: {self.band or 'unknown'}, channel: {self.channel or 'unknown'}",
            f"Internet stayed up during the group: {yes[self.internet_during_group]}",
            f"Photo saved: {yes[self.saved] if self.variant != 'connect' else 'n/a'}; "
            f"JPEG valid: {yes[self.jpeg_ok]}",
            f"Wi-Fi came back: {yes[self.wifi_back]}; internet after: {yes[self.internet_after]}",
            f"Errors: {'; '.join(self.errors) if self.errors else 'none'}",
        ]
        if self.wifi_back is False or self.internet_after is False:
            lines.append(MANUAL_RESTORE)
        text = "\n".join(lines)
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[name]")
        return IP_PATTERN.sub("[address]", text)


class IpWatcher:
    """Collects the 0x73 type 8 event. Start it BEFORE the enable write so nothing is missed."""

    def __init__(self, events: AsyncIterator[GlassesEvent]) -> None:
        self._events = events
        self._task: asyncio.Task[str] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def _listen(self) -> str:
        async for event in self._events:
            if event.kind == "ip_address":
                return str(event.fields["ip"])
        raise IpEventMissing("the event stream ended without an address")

    async def wait(self, timeout: float) -> str:
        assert self._task is not None
        try:
            return await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except asyncio.TimeoutError as exc:
            raise IpEventMissing(f"no address event within {timeout:.0f} s") from exc

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


@dataclass
class WifiDirectRun:
    variant: str
    adapter: HelperWifiDirect
    enable: Callable[[], Awaitable[None]]  # the ONE approved BLE write
    ip: IpWatcher
    save_dir: Path
    peer_prefix: str
    http: Callable[[str, Path], Any]
    take_photo: Callable[[], Awaitable[PhotoResult]] | None = None

    async def run(self) -> RunReport:
        report = RunReport(self.variant)
        clock = time.perf_counter

        async def stage(name: str, work: Awaitable[Any]) -> Any:
            started = clock()
            try:
                result = await work
            except BaseException as exc:
                report.stages.append(Stage(name, clock() - started, False, type(exc).__name__))
                raise
            report.stages.append(Stage(name, clock() - started, True))
            return result

        session: WifiDirectSession | None = None
        try:
            await stage("preflight", self.adapter.prepare())
            report.band = self.adapter.band()
            if report.band and not report.band.startswith("2.4"):
                raise GlassesError("the PC Wi-Fi is not on 2.4 GHz: switch it or disconnect first")
            if self.variant == "full":
                assert self.take_photo is not None
                photo = await stage("take photo", self.take_photo())
                if photo.photos_added is not None and photo.photos_added < 1:
                    raise GlassesError("the media count did not rise")
            self.ip.start()
            await stage("enable (BLE, once)", self.enable())
            session = WifiDirectSession(self.adapter, self.adapter, peer_prefix=self.peer_prefix)
            await stage("find peer + join", session.__aenter__())
            address = await stage("read IP event", self.ip.wait(IP_WAIT_S))
            status = await stage("status", self.adapter.status())
            report.internet_during_group = bool(status.get("internet"))
            report.channel = ",".join(str(c) for c in status.get("channels") or []) or None
            if session.group and session.group.band:
                report.band = session.group.band
            if self.variant != "connect":
                path = await stage("download", asyncio.to_thread(self._download, address))
                report.saved, report.jpeg_ok = True, path.read_bytes().startswith(JPEG_MARKER)
        except BaseException as exc:
            report.errors.append(f"{type(exc).__name__}: {str(exc)[:100]}")
            if session is None or not session.group:
                seen = self.adapter.peers_seen
                report.errors.append(f"Wi-Fi Direct peers seen: {len(seen)} {seen}")
            report.stopped_early = isinstance(exc, IpEventMissing)
            if isinstance(exc, asyncio.CancelledError):
                await self._finish(session, report)
                raise
        await self._finish(session, report)
        return report

    async def _finish(self, session: WifiDirectSession | None, report: RunReport) -> None:
        """Always: stop listening, leave the group, restore Wi-Fi, and record the result."""
        await self.ip.stop()
        if session is None:
            return
        started = time.perf_counter()
        await session.__aexit__(None, None, None)
        roll = session.rollback
        if roll is not None:
            report.wifi_back, report.internet_after = roll.profile_restored, roll.internet_ok
            report.errors.extend(roll.errors)
            report.stages.append(
                Stage("disconnect + restore", time.perf_counter() - started, roll.clean)
            )

    def _download(self, address: str) -> Path:
        client = self.http(address, self.save_dir)
        names = [n.strip() for n in client.get(LIST_PATH).decode("utf-8", "replace").splitlines()]
        images = sorted(n for n in names if IMAGE_NAME.match(n))
        if not images:
            raise GlassesError("the glasses list no images")
        saved: Path = client.download(
            FILE_PREFIX + images[-1], self.save_dir / images[-1], expect_jpeg=True
        )
        return saved
