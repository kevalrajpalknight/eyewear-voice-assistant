"""Byte-level denylist for writes that could update, wipe or brick the glasses.

Derived from reading the official app (INFERRED, see docs/PROTOCOL.md). These checks run on the raw
BC-framed payload and win over any allowlist entry or session approval. Nothing here is ever sent.
"""

from aiglasses.protocol.bcframe import HEADER_LEN, START_BYTE

GLASSES_CONTROL_CMD = 0x41

DENY_COMMANDS: dict[int, str] = {
    0x01: "firmware update (start)",
    0x02: "firmware update (init)",
    0x03: "firmware update (data)",
    0x04: "firmware update (check)",
    0x05: "firmware update (end)",
    0x25: "file transfer to the glasses",
    0x26: "file transfer to the glasses",
    0x30: "file transfer to the glasses",
    0x31: "file transfer to the glasses",
    0x32: "file transfer to the glasses",
    0x33: "file transfer to the glasses",
    0x34: "file transfer to the glasses",
    0x35: "file transfer to the glasses",
    0x80: "file transfer to the glasses",
    0x81: "file transfer to the glasses",
    0x82: "file transfer to the glasses",
    0x39: "delete a file on the glasses",
    0xF2: "factory test",
    0xFC: "update over the SoC (OTA)",
}

# glassesControl (cmd 0x41) payload prefixes: [write][device action][action id]
DENY_CONTROL_PREFIXES: dict[bytes, str] = {
    bytes([2, 1, 10]): "factory reset",
    bytes([2, 1, 14]): "restart the glasses",
    bytes(
        [2, 1, 9]
    ): "end of an import session (the glasses probably delete imported files; not proven safe)",
}


def denied_reason(data: bytes) -> str | None:
    """Why this write must never be sent, or None. Does not require a valid CRC."""
    if len(data) < 2 or data[0] != START_BYTE:
        return None
    cmd = data[1]
    if cmd in DENY_COMMANDS:
        return DENY_COMMANDS[cmd]
    if cmd == GLASSES_CONTROL_CMD:
        body = data[HEADER_LEN:]
        for prefix, reason in DENY_CONTROL_PREFIXES.items():
            if body.startswith(prefix):
                return reason
    return None
