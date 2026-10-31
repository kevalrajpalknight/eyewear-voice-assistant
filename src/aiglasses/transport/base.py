import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from aiglasses.domain.models import GattService

NotifyHandler = Callable[[str, bytes], None]


class BleTransport(Protocol):
    @property
    def is_connected(self) -> bool: ...

    @property
    def mtu(self) -> int: ...

    @property
    def disconnected(self) -> asyncio.Event:
        """Set when the link drops. Replaced with a fresh event on every connect()."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    def services(self) -> list[GattService]:
        """GATT structure of the connected device (no values)."""

    def connection_info(self) -> dict[str, object]:
        """OS/backend-reported details about the current connection."""

    async def read(self, char: str) -> bytes: ...

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None: ...

    async def subscribe(self, char: str, handler: NotifyHandler) -> None:
        """Enable notifications/indications (writes the standard CCCD descriptor only)."""


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
    index: int = -1
    channels: int = 1
    hostapi: str = ""
    profile: str = "unknown"  # "a2dp" | "hfp" | "unknown" (guessed from the Windows endpoint name)


Samples = npt.NDArray[np.float32]


class AudioIO(Protocol):
    def list_endpoints(self, name_hint: str) -> list[AudioEndpoint]: ...

    async def play(self, samples: Samples, sample_rate: int, endpoint: AudioEndpoint) -> None:
        """Play (frames, channels) float32 samples; blocks until done."""

    async def record(self, seconds: float, endpoint: AudioEndpoint) -> Samples:
        """Record mono float32 samples at endpoint.sample_rate."""
