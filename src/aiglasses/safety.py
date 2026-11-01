import json
import re
from collections.abc import Iterable
from pathlib import Path

from aiglasses.domain.errors import WriteBlockedError
from aiglasses.protocol.denylist import denied_reason
from aiglasses.protocol.registry import Command
from aiglasses.timefmt import stamps
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
        self._sent: set[tuple[str, bytes]] = set()
        # Confirmed read-only commands may repeat; in-session approvals are sent at most once.
        self._repeatable = set(self._allowed)

    def approve(self, name: str, char: str, payload: bytes, *, repeatable: bool = False) -> None:
        """Explicit user approval of one exact payload for this session.

        Sent at most once unless `repeatable`, which the user must have approved for that frame.
        """
        key = (char.lower(), payload)
        self._allowed[key] = name
        if repeatable:
            self._repeatable.add(key)

    def _audit(self, outcome: str, name: str, char: str, payload: bytes) -> None:
        if self._audit_path is None:
            return
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            **stamps(),
            "outcome": outcome,
            "name": name,
            "uuid": char.lower(),
            "hex": payload.hex(),
        }
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    async def write(self, char: str, payload: bytes, *, response: bool = False) -> None:
        reason = denied_reason(payload)
        if reason:
            self._audit("blocked_denylist_bytes", reason, char, payload)
            raise WriteBlockedError(f"refusing to send: {reason}")
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
        if key in self._sent and key not in self._repeatable:
            self._audit("blocked_repeat", name, char, payload)
            raise WriteBlockedError(
                "already sent once this session; writes are never repeated or retried"
            )
        if not self._transport.is_connected or self._transport.disconnected.is_set():
            self._audit("blocked_link_down", name, char, payload)
            raise WriteBlockedError("the link is not up right now; nothing was written")
        self._sent.add(key)  # counted before the attempt: an ambiguous write is never retried
        self._audit("sent", name, char, payload)
        await self._transport.write(char, payload, response=response)
