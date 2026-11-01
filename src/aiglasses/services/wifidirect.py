"""Wi-Fi Direct preparation: interfaces, preflight checks and rollback. No real radio code yet.

The real implementation needs the winrt packages, which stay unpinned until 2026-11-07 (ADR 0006).
The session only drives the PC side; it never sends anything to the glasses over BLE.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from aiglasses.domain.errors import GlassesError, GlassesTimeoutError

PEER_TIMEOUT_SECONDS = 30.0
CONNECT_TIMEOUT_SECONDS = 15.0
MAX_RETRIES = 1
GROUP_OWNER_INTENT = 15  # PC is group owner, the glasses join as client (the phone's roles)


class WifiDirectError(GlassesError):
    """The Wi-Fi Direct step failed or was refused by a preflight check."""


@dataclass(frozen=True)
class GroupInfo:
    band: str | None
    channel: int | None
    glasses_ip: str | None = None


class WifiDirect(Protocol):
    async def find_peer(self, name_prefix: str, timeout: float) -> str | None: ...

    async def connect(self, peer: str, *, group_owner_intent: int, timeout: float) -> GroupInfo: ...

    async def disconnect(self) -> None: ...


class HostNetwork(Protocol):
    """The PC's normal Wi-Fi: what to restore after the test. The profile name stays private."""

    def current_profile(self) -> str | None: ...

    def band(self) -> str | None: ...

    async def restore(self, profile: str) -> None: ...

    async def has_internet(self) -> bool: ...


class Severity(Enum):
    OK = "ok"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class Check:
    name: str
    severity: Severity
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.severity is not Severity.BLOCK for c in self.checks)


async def preflight(host: HostNetwork, *, approved: bool, pins_installed: bool) -> PreflightReport:
    """Read-only checks before Step 2. Blocks without approval or without the pinned packages."""
    checks = [
        Check(
            "approval",
            Severity.OK if approved else Severity.BLOCK,
            "Step 2 approved by the user" if approved else "Step 2 is not approved",
        ),
        Check(
            "winrt packages",
            Severity.OK if pins_installed else Severity.BLOCK,
            "pinned set installed"
            if pins_installed
            else "not installed; nothing is pinned before 2026-11-07 (ADR 0006)",
        ),
    ]
    profile = host.current_profile()
    if profile is None:
        checks.append(Check("wifi profile", Severity.WARN, "not connected; nothing to restore"))
    else:
        checks.append(Check("wifi profile", Severity.OK, "current network recorded (kept private)"))
        online = await host.has_internet()
        checks.append(
            Check(
                "internet",
                Severity.OK if online else Severity.BLOCK,
                "internet works before the test"
                if online
                else "internet is already down; fix it first so rollback can be verified",
            )
        )
    band = host.band()
    if band is not None and not band.startswith("2.4"):
        checks.append(
            Check(
                "band",
                Severity.WARN,
                f"PC is on {band}; the glasses are probably 2.4 GHz only (unverified). "
                "Switch to 2.4 GHz or disconnect Wi-Fi first.",
            )
        )
    return PreflightReport(checks)


@dataclass
class RollbackReport:
    group_disconnected: bool = False
    profile_restored: bool | None = None  # None: there was nothing to restore
    internet_ok: bool | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return (
            self.group_disconnected
            and self.profile_restored is not False
            and self.internet_ok is not False
            and not self.errors
        )


@dataclass
class SessionResult:
    group: GroupInfo | None
    rollback: RollbackReport
    attempts: int


class WifiDirectSession:
    """Find the peer, connect with a retry limit, and always roll back the PC's Wi-Fi."""

    def __init__(self, wifi: WifiDirect, host: HostNetwork, *, peer_prefix: str) -> None:
        self._wifi = wifi
        self._host = host
        self._peer_prefix = peer_prefix
        self.group: GroupInfo | None = None
        self.attempts = 0
        self.rollback: RollbackReport | None = None
        self._profile: str | None = None

    async def __aenter__(self) -> "WifiDirectSession":
        self._profile = self._host.current_profile()
        try:
            await self._connect()
        except BaseException:
            await self._roll_back()
            raise
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._roll_back()

    async def _connect(self) -> None:
        last: Exception | None = None
        for _ in range(1 + MAX_RETRIES):
            self.attempts += 1
            try:
                peer = await self._wifi.find_peer(self._peer_prefix, PEER_TIMEOUT_SECONDS)
                if peer is None:
                    raise GlassesTimeoutError(
                        f"no peer named {self._peer_prefix}* within {PEER_TIMEOUT_SECONDS:.0f}s"
                    )
                self.group = await asyncio.wait_for(
                    self._wifi.connect(
                        peer,
                        group_owner_intent=GROUP_OWNER_INTENT,
                        timeout=CONNECT_TIMEOUT_SECONDS,
                    ),
                    CONNECT_TIMEOUT_SECONDS + 1,
                )
                return
            except (GlassesError, asyncio.TimeoutError) as exc:
                last = exc
        raise WifiDirectError(f"Wi-Fi Direct failed after {self.attempts} attempt(s): {last}")

    async def _roll_back(self) -> None:
        if self.rollback is not None:
            return
        report = RollbackReport()
        self.rollback = report
        try:
            await self._wifi.disconnect()
            report.group_disconnected = True
        except Exception as exc:
            report.errors.append(f"disconnect failed: {exc}")
        if self._profile is not None:
            try:
                await self._host.restore(self._profile)
                report.profile_restored = True
            except Exception as exc:
                report.profile_restored = False
                report.errors.append(f"restore failed: {exc}")
            report.internet_ok = await self._host.has_internet()


class FakeWifiDirect:
    """Test double: scripted peer discovery and connect results."""

    def __init__(
        self, *, peers: list[str | None] | None = None, connect_errors: int = 0, hang: bool = False
    ) -> None:
        self._peers = list(peers if peers is not None else ["AIMB-G2_TEST"])
        self._connect_errors = connect_errors
        self._hang = hang
        self.connected = False
        self.connect_calls: list[tuple[str, int]] = []
        self.disconnects = 0

    async def find_peer(self, name_prefix: str, timeout: float) -> str | None:
        peer = self._peers.pop(0) if len(self._peers) > 1 else self._peers[0]
        return peer if peer is not None and peer.startswith(name_prefix) else None

    async def connect(self, peer: str, *, group_owner_intent: int, timeout: float) -> GroupInfo:
        self.connect_calls.append((peer, group_owner_intent))
        if self._hang:
            await asyncio.sleep(3600)
        if self._connect_errors > 0:
            self._connect_errors -= 1
            raise WifiDirectError("connect refused")
        self.connected = True
        return GroupInfo(band="2.4 GHz", channel=6, glasses_ip="192.168.49.2")

    async def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False


class FakeHostNetwork:
    def __init__(
        self,
        *,
        profile: str | None = "home",
        band: str | None = "2.4 GHz",
        internet: list[bool] | None = None,
        restore_error: bool = False,
    ) -> None:
        self._profile = profile
        self._band = band
        self._internet = list(internet if internet is not None else [True])
        self._restore_error = restore_error
        self.restored: list[str] = []

    def current_profile(self) -> str | None:
        return self._profile

    def band(self) -> str | None:
        return self._band

    async def restore(self, profile: str) -> None:
        if self._restore_error:
            raise WifiDirectError("profile missing")
        self.restored.append(profile)

    async def has_internet(self) -> bool:
        return self._internet.pop(0) if len(self._internet) > 1 else self._internet[0]
