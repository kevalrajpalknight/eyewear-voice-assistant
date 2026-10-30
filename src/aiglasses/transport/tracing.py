import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from aiglasses.domain.models import GattService
from aiglasses.transport.base import BleTransport, NotifyHandler


class TracingTransport:
    """Wraps a BleTransport and appends every read/write/notify/CCCD enable to trace.jsonl."""

    def __init__(self, inner: BleTransport, trace_path: Path) -> None:
        self._inner = inner
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = trace_path

    def _handle_of(self, uuid: str) -> int | None:
        try:
            for svc in self._inner.services():
                for ch in svc.characteristics:
                    if ch.uuid == uuid.lower():
                        return ch.handle
        except Exception:  # not connected yet: handle is simply unknown
            return None
        return None

    def _log(self, op: str, char: str, data: bytes) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "op": op,
            "handle": self._handle_of(char),
            "uuid": char.lower(),
            "hex": data.hex(),
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def mark(self, label: str) -> None:
        """Write a user-typed label into the trace so actions can be matched to notifications."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "op": "mark",
            "label": label,
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    @property
    def mtu(self) -> int:
        return self._inner.mtu

    @property
    def disconnected(self) -> asyncio.Event:
        return self._inner.disconnected

    async def connect(self) -> None:
        await self._inner.connect()

    async def disconnect(self) -> None:
        await self._inner.disconnect()

    def services(self) -> list[GattService]:
        return self._inner.services()

    def connection_info(self) -> dict[str, object]:
        return self._inner.connection_info()

    async def read(self, char: str) -> bytes:
        self._log("read_request", char, b"")
        data = await self._inner.read(char)
        self._log("read", char, data)
        return data

    async def write(self, char: str, data: bytes, *, response: bool = False) -> None:
        self._log("write", char, data)
        await self._inner.write(char, data, response=response)

    async def subscribe(self, char: str, handler: NotifyHandler) -> None:
        def traced(uuid: str, data: bytes) -> None:
            self._log("notify", uuid, data)
            handler(uuid, data)

        self._log("cccd_enable", char, b"")
        await self._inner.subscribe(char, traced)
