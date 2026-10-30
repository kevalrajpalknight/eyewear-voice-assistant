from pathlib import Path

from aiglasses.transport.base import AudioEndpoint, NotifyHandler


class FakeBleTransport:
    def __init__(self, reads: dict[str, bytes] | None = None) -> None:
        self.reads = {k.lower(): v for k, v in (reads or {}).items()}
        self.writes: list[tuple[str, bytes, bool]] = []
        self.connected = False
        self._handlers: dict[str, NotifyHandler] = {}

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def read(self, char: str) -> bytes:
        return self.reads[char.lower()]

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        self.writes.append((char.lower(), data, response))

    async def subscribe(self, char: str, handler: NotifyHandler) -> None:
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
        self.played: list[Path] = []
        self.recorded: list[Path] = []

    def list_endpoints(self) -> list[AudioEndpoint]:
        return list(self.endpoints)

    async def play_file(self, path: Path, endpoint: AudioEndpoint) -> None:
        self.played.append(path)

    async def record(self, seconds: float, endpoint: AudioEndpoint, path: Path) -> None:
        self.recorded.append(path)
