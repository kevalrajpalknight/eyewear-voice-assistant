import asyncio

import numpy as np

from aiglasses.domain.errors import ConnectionFailedError
from aiglasses.domain.models import GattService
from aiglasses.transport.base import AudioEndpoint, NotifyHandler, Samples


class FakeBleTransport:
    def __init__(
        self,
        reads: dict[str, bytes] | None = None,
        services: list[GattService] | None = None,
        *,
        fail_connects: int = 0,
        subscribe_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.reads = {k.lower(): v for k, v in (reads or {}).items()}
        self.writes: list[tuple[str, bytes, bool]] = []
        self.read_log: list[str] = []
        self.connected = False
        self.connect_calls = 0
        self._fail_connects = fail_connects
        self._services = services or []
        self._subscribe_errors = {k.lower(): v for k, v in (subscribe_errors or {}).items()}
        self._handlers: dict[str, NotifyHandler] = {}
        self._disconnected = asyncio.Event()

    @property
    def mtu(self) -> int:
        return 23

    @property
    def disconnected(self) -> asyncio.Event:
        return self._disconnected

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self._fail_connects:
            raise ConnectionFailedError("fake connect failure")
        self._disconnected = asyncio.Event()
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def drop_link(self) -> None:
        self.connected = False
        self._disconnected.set()

    def services(self) -> list[GattService]:
        return list(self._services)

    def connection_info(self) -> dict[str, object]:
        return {"address": "00:00:00:00:00:00", "fake": True}

    async def read(self, char: str) -> bytes:
        self.read_log.append(char.lower())
        return self.reads[char.lower()]

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        self.writes.append((char.lower(), data, response))

    async def subscribe(self, char: str, handler: NotifyHandler) -> None:
        error = self._subscribe_errors.get(char.lower())
        if error is not None:
            raise error
        self._handlers[char.lower()] = handler

    def emit(self, char: str, data: bytes) -> None:
        self._handlers[char.lower()](char.lower(), data)


class FakeWifiLink:
    def __init__(self, ssids: list[str] | None = None, current: str | None = None) -> None:
        self.ssids = ssids or []
        self.current = current
        self._previous: str | None = None

    async def current_ssid(self) -> str | None:
        return self.current

    async def list_ssids(self) -> list[str]:
        return list(self.ssids)

    async def join(self, ssid: str, password: str | None) -> None:
        self._previous, self.current = self.current, ssid

    async def restore(self) -> None:
        self.current = self._previous


class FakeAudioIO:
    def __init__(self, endpoints: list[AudioEndpoint] | None = None) -> None:
        self.endpoints = endpoints or []
        self.played: list[tuple[Samples, int, AudioEndpoint]] = []
        self.recorded: list[AudioEndpoint] = []

    def list_endpoints(self, name_hint: str) -> list[AudioEndpoint]:
        return [e for e in self.endpoints if name_hint.lower() in e.name.lower()]

    async def play(self, samples: Samples, sample_rate: int, endpoint: AudioEndpoint) -> None:
        self.played.append((samples, sample_rate, endpoint))

    async def record(self, seconds: float, endpoint: AudioEndpoint) -> Samples:
        self.recorded.append(endpoint)
        frames = int(seconds * endpoint.sample_rate)
        return np.full(frames, 0.1, dtype=np.float32)
