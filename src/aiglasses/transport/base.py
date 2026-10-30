from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

NotifyHandler = Callable[[str, bytes], None]


class BleTransport(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def read(self, char: str) -> bytes: ...

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None: ...

    async def subscribe(self, char: str, handler: NotifyHandler) -> None: ...


class WifiLink(Protocol):
    async def current_ssid(self) -> str | None: ...

    async def list_ssids(self) -> list[str]: ...

    async def join(self, ssid: str, password: str | None) -> None: ...

    async def restore(self) -> None:
        """Rejoin the network that was active before join()."""


@dataclass(frozen=True)
class AudioEndpoint:
    name: str
    kind: str  # "output" | "input"
    sample_rate: int


class AudioIO(Protocol):
    def list_endpoints(self) -> list[AudioEndpoint]: ...

    async def play_file(self, path: Path, endpoint: AudioEndpoint) -> None: ...

    async def record(self, seconds: float, endpoint: AudioEndpoint, path: Path) -> None: ...
