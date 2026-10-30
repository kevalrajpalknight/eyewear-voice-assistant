from dataclasses import dataclass

from aiglasses.domain.errors import ProtocolError

START_BYTE = 0xBC
HEADER_LEN = 6  # start, cmd, length (2, little-endian), crc16 (2, little-endian)


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class BcFrame:
    """Frame on de5bf729: BC | cmd | len16 LE | crc16-MODBUS LE of payload | payload.

    CONFIRMED on all 756 notifications captured from this unit (see docs/PROTOCOL.md).
    """

    cmd: int
    payload: bytes


def parse_bc_frame(raw: bytes) -> BcFrame:
    if len(raw) < HEADER_LEN or raw[0] != START_BYTE:
        raise ProtocolError(f"not a BC frame: {raw[:8].hex()}")
    length = int.from_bytes(raw[2:4], "little")
    payload = raw[HEADER_LEN:]
    if len(payload) != length:
        raise ProtocolError(f"length field {length} does not match payload size {len(payload)}")
    crc = int.from_bytes(raw[4:6], "little")
    if crc != crc16_modbus(payload):
        raise ProtocolError("CRC mismatch")
    return BcFrame(cmd=raw[1], payload=payload)
