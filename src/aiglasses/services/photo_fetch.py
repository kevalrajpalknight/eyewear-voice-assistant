"""Get the photo the glasses just took onto the PC, over Wi-Fi Direct. Built and tested with fakes.

Flow: take the photo (the caller already checked the user asked) -> confirm the media count rose ->
enable discoverability (a BLE write that needs the user's approval of the exact bytes, so it is an
injected step that refuses until approved) -> a helper process joins the glasses' Wi-Fi Direct peer
-> the glasses report their IP in the 0x73 type 8 event -> guarded HTTP GET of the file list and
the newest image -> leave the group and restore the PC's Wi-Fi, on EVERY exit path.

Nothing here sends anything to the glasses by itself. The real helper (winrt, isolated
environment) is not verified on hardware.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aiglasses.assistant.vision import ImageUnavailable
from aiglasses.domain.errors import GlassesError, NotSupportedError
from aiglasses.research.glasses_http import GlassesHttpClient, HttpGuardError
from aiglasses.services.control import PhotoResult
from aiglasses.services.wifidirect import (
    GroupInfo,
    WifiDirect,
    WifiDirectError,
    WifiDirectSession,
)

LIST_PATH = "/files/media.config"
FILE_PREFIX = "/files/"
IMAGE_NAME = re.compile(r"^[\w.\-]+\.(?:jpg|jpeg)$", re.IGNORECASE)
IP_WAIT_S = 10.0


class PhotoFetchError(ImageUnavailable):
    """The photo could not be fetched. `spoken` is a short line for the user."""


class Helper(Protocol):
    """The isolated Wi-Fi Direct helper process, seen from the main program."""

    async def request(self, command: str, **arguments: Any) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class HelperWifiDirect:
    """`WifiDirect` and `HostNetwork` implemented by talking JSON lines to the helper."""

    def __init__(self, helper: Helper) -> None:
        self._helper = helper
        self._profile: str | None = None
        self._band: str | None = None

    async def prepare(self) -> None:
        """Ask the helper (read-only) for the PC's Wi-Fi profile and band; cached."""
        info = await self._helper.request("preflight")
        self._profile, self._band = info.get("profile"), info.get("band")

    # HostNetwork
    def current_profile(self) -> str | None:
        return self._profile

    def band(self) -> str | None:
        return self._band

    async def restore(self, profile: str) -> None:
        await self._helper.request("restore", profile=profile)

    async def status(self) -> dict[str, Any]:
        return await self._helper.request("status")

    async def has_internet(self) -> bool:
        return bool((await self.status()).get("internet"))

    # WifiDirect
    async def find_peer(self, name_prefix: str, timeout: float) -> str | None:
        reply = await self._helper.request("find_peer", prefix=name_prefix, timeout=timeout)
        peer = reply.get("peer")
        return str(peer) if peer else None

    async def connect(self, peer: str, *, group_owner_intent: int, timeout: float) -> GroupInfo:
        reply = await self._helper.request(
            "connect", peer=peer, group_owner_intent=group_owner_intent, timeout=timeout
        )
        return GroupInfo(reply.get("band"), reply.get("channel"), reply.get("glasses_ip"))

    async def disconnect(self) -> None:
        await self._helper.request("disconnect")


class SubprocessHelper:
    """Spawns the helper with the isolated environment's Python and speaks JSON lines to it."""

    def __init__(self, command: list[str], timeout_s: float = 30.0) -> None:
        self._command = command
        self._timeout = timeout_s
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 0

    async def request(self, command: str, **arguments: Any) -> dict[str, Any]:
        if self._process is None or self._process.returncode is not None:
            self._process = await asyncio.create_subprocess_exec(
                *self._command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
            )
        assert self._process.stdin is not None and self._process.stdout is not None
        self._next_id += 1
        line = json.dumps({"id": self._next_id, "cmd": command, **arguments}) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()
        raw = await asyncio.wait_for(self._process.stdout.readline(), self._timeout)
        if not raw:
            raise WifiDirectError("the Wi-Fi Direct helper exited")
        reply: dict[str, Any] = json.loads(raw)
        if not reply.get("ok"):
            raise WifiDirectError(str(reply.get("error", "helper error"))[:120])
        return reply

    async def close(self) -> None:
        process = self._process
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


class FakeHelper:
    """Scripted helper for tests: records commands, can fail named commands."""

    def __init__(
        self,
        *,
        profile: str | None = "home",
        band: str | None = "2.4 GHz",
        fail: set[str] | None = None,
    ) -> None:
        self.commands: list[str] = []
        self.fail = fail or set()
        self.internet = True
        self._profile, self._band = profile, band

    async def request(self, command: str, **arguments: Any) -> dict[str, Any]:
        self.commands.append(command)
        if command in self.fail:
            raise WifiDirectError(f"{command} failed")
        replies: dict[str, dict[str, Any]] = {
            "preflight": {"profile": self._profile, "band": self._band},
            "find_peer": {"peer": "AIMB-G2_TEST"},
            "connect": {"band": "2.4 GHz", "channel": 6, "glasses_ip": None},
            "status": {"internet": self.internet, "channels": [6]},
        }
        return replies.get(command, {})

    async def close(self) -> None:
        self.commands.append("close")


class Events(Protocol):
    async def wait_ip(self, timeout: float) -> str:
        """The glasses' IP from the 0x73 type 8 event."""
        ...


Enable = Callable[[], Awaitable[None]]
TakePhoto = Callable[[], Awaitable[PhotoResult]]
Http = Callable[[str], Any]


async def enable_not_approved() -> None:
    raise NotSupportedError("the Wi-Fi Direct enable command is not approved yet")


@dataclass
class PhotoFetcher:
    take_photo: TakePhoto
    enable_discovery: Enable
    wifi: WifiDirect
    host: Any  # HostNetwork
    events: Events
    save_dir: Path
    peer_prefix: str
    http: Callable[[str, Path], Any] = GlassesHttpClient

    async def fetch_latest(self) -> Path:
        try:
            photo = await self.take_photo()
        except GlassesError as exc:
            raise PhotoFetchError(
                f"photo failed: {type(exc).__name__}", "The photo failed."
            ) from exc
        if photo.photos_added is not None and photo.photos_added < 1:
            raise PhotoFetchError("media count did not rise", "The camera did not take a photo.")
        try:
            await self.enable_discovery()
        except NotSupportedError as exc:
            raise PhotoFetchError(
                "wifi direct enable not approved",
                "I can't fetch the photo from the glasses yet, so I can't look at it.",
            ) from exc
        try:
            async with WifiDirectSession(
                self.wifi, self.host, peer_prefix=self.peer_prefix
            ) as session:
                ip = (
                    session.group.glasses_ip if session.group else None
                ) or await self.events.wait_ip(IP_WAIT_S)
                path = await asyncio.to_thread(self._download, ip)
        except (WifiDirectError, asyncio.TimeoutError) as exc:
            raise PhotoFetchError(
                f"wifi direct failed: {exc}", "I couldn't connect to the glasses."
            ) from exc
        except HttpGuardError as exc:
            raise PhotoFetchError(f"download failed: {exc}", "I couldn't get the photo.") from exc
        if session.rollback is not None and not session.rollback.clean:
            raise PhotoFetchError("wifi rollback incomplete", "Your Wi-Fi may need reconnecting.")
        return path

    def _download(self, ip: str) -> Path:
        client = self.http(ip, self.save_dir)
        names = [n.strip() for n in client.get(LIST_PATH).decode("utf-8", "replace").splitlines()]
        images = sorted(n for n in names if IMAGE_NAME.match(n))
        if not images:
            raise HttpGuardError("the glasses list no images")
        newest = images[-1]  # INFERRED: names carry a timestamp, so the last sorts newest
        saved: Path = client.download(
            FILE_PREFIX + newest, self.save_dir / newest, expect_jpeg=True
        )
        return saved


class WifiDirectImageSource:
    """`ImageSource` backed by the fetcher: capture() = take a photo and bring it here."""

    def __init__(self, fetcher: PhotoFetcher) -> None:
        self._fetcher = fetcher

    async def capture(self) -> bytes:
        path = await self._fetcher.fetch_latest()
        return await asyncio.to_thread(path.read_bytes)
