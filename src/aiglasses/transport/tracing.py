import json
import time
from pathlib import Path

from aiglasses.transport.base import BleTransport, NotifyHandler


class TracingTransport:
    """Wraps a BleTransport and appends every read/write/notify to trace.jsonl."""

    def __init__(self, inner: BleTransport, trace_path: Path) -> None:
        self._inner = inner
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = trace_path

    def _log(self, op: str, char: str, data: bytes) -> None:
        entry = {"ts": time.time(), "op": op, "uuid": char.lower(), "hex": data.hex()}
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    async def connect(self) -> None:
        await self._inner.connect()

    async def disconnect(self) -> None:
        await self._inner.disconnect()

    async def read(self, char: str) -> bytes:
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

        await self._inner.subscribe(char, traced)
