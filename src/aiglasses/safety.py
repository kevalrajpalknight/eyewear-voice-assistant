import json
import re
import time
from collections.abc import Iterable
from pathlib import Path

from aiglasses.domain.errors import WriteBlockedError
from aiglasses.protocol.registry import Command
from aiglasses.transport.base import BleTransport

# Firmware/OTA commands share the control channel on Jieli devices and can brick the unit.
DENY_PATTERN = re.compile(r"ota|upgrade|update|dfu|flash|firmware|bootloader|reboot", re.IGNORECASE)


class WriteGuard:
    """Only exact payloads from the allowlist (or approved this session) reach the device."""

    def __init__(
        self,
        transport: BleTransport,
        allowlist: Iterable[Command] = (),
        *,
        dry_run: bool = True,
        audit_path: Path | None = None,
    ) -> None:
        self._transport = transport
        self._allowed: dict[tuple[str, bytes], str] = {
            (c.characteristic, c.payload): c.name for c in allowlist
        }
        self.dry_run = dry_run
        self._audit_path = audit_path

    def approve(self, name: str, char: str, payload: bytes) -> None:
        """Explicit user approval of one exact payload for this session."""
        self._allowed[(char.lower(), payload)] = name

    def _audit(self, outcome: str, name: str, char: str, payload: bytes) -> None:
        if self._audit_path is None:
            return
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "outcome": outcome,
            "name": name,
            "uuid": char.lower(),
            "hex": payload.hex(),
        }
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    async def write(self, char: str, payload: bytes, *, response: bool = False) -> None:
        key = (char.lower(), payload)
        name = self._allowed.get(key, "unlisted")
        if DENY_PATTERN.search(name):
            self._audit("blocked_denylist", name, char, payload)
            raise WriteBlockedError(f"{name!r} looks like a firmware/OTA command")
        if key not in self._allowed:
            self._audit("blocked_not_allowlisted", name, char, payload)
            raise WriteBlockedError(f"payload {payload.hex()} to {char} is not allowlisted")
        if self.dry_run:
            self._audit("dry_run", name, char, payload)
            return
        self._audit("sent", name, char, payload)
        await self._transport.write(char, payload, response=response)
