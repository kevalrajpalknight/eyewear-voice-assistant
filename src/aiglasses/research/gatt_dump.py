import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiglasses.domain.errors import GlassesError
from aiglasses.hexfmt import ascii_of
from aiglasses.transport.base import BleTransport

log = logging.getLogger("aiglasses.gatt_dump")


async def dump_gatt(transport: BleTransport) -> dict[str, Any]:
    """Read-only GATT dump: reads only readable characteristics, logging each read first."""
    services: list[dict[str, Any]] = []
    for svc in transport.services():
        chars: list[dict[str, Any]] = []
        for ch in svc.characteristics:
            entry: dict[str, Any] = {
                "uuid": ch.uuid,
                "handle": ch.handle,
                "properties": ch.properties,
                "descriptors": [{"uuid": d.uuid, "handle": d.handle} for d in ch.descriptors],
            }
            if "read" in ch.properties:
                log.info("reading %s (handle %d)", ch.uuid, ch.handle)
                try:
                    value = await transport.read(ch.uuid)
                except GlassesError as exc:
                    entry["read_error"] = str(exc)
                else:
                    entry["value_hex"] = value.hex()
                    entry["value_ascii"] = ascii_of(value)
            chars.append(entry)
        services.append({"uuid": svc.uuid, "handle": svc.handle, "characteristics": chars})
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mtu": transport.mtu,
        "connection": transport.connection_info(),
        "services": services,
    }


def write_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
