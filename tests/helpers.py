import struct

from aiglasses.protocol.bcframe import START_BYTE, crc16_modbus
from aiglasses.research.btsnoop import EPOCH_DELTA_US

GLASSES = "D0:4B:1B:C3:23:24"
BASE_TS = 1_761_851_000.0


def build_bc_frame(cmd: int, payload: bytes) -> bytes:
    """Test fixture builder only. The library never constructs frames to send."""
    header = bytes([START_BYTE, cmd]) + len(payload).to_bytes(2, "little")
    return header + crc16_modbus(payload).to_bytes(2, "little") + payload


def _addr(address: str) -> bytes:
    return bytes(int(p, 16) for p in reversed(address.split(":")))


def btsnoop_file(packets: list[tuple[float, bool, bytes]]) -> bytes:
    """packets: (seconds after BASE_TS, sent by host, H4 packet including the type byte)."""
    out = b"btsnoop\x00" + struct.pack(">II", 1, 1002)
    for offset, sent, data in packets:
        ts_us = int((BASE_TS + offset) * 1e6) + EPOCH_DELTA_US
        flags = (0 if sent else 1) | (2 if data[0] in (1, 4) else 0)
        out += struct.pack(">IIIIq", len(data), len(data), flags, 0, ts_us) + data
    return out


def le_connection_complete(handle: int, address: str) -> bytes:
    head = bytes([0x01, 0x00]) + struct.pack("<H", handle) + bytes([0, 0])
    params = head + _addr(address) + bytes(7)
    return bytes([0x04, 0x3E, len(params)]) + params


def classic_connection_complete(handle: int, address: str) -> bytes:
    params = bytes([0x00]) + struct.pack("<H", handle) + _addr(address) + bytes([1, 0])
    return bytes([0x04, 0x03, len(params)]) + params


def acl(handle: int, cid: int, payload: bytes, pb: int = 0, l2cap_len: int | None = None) -> bytes:
    """One ACL packet. For a continuation fragment pass pb=1 and the raw continuation bytes."""
    body = payload if pb == 1 else struct.pack("<HH", l2cap_len or len(payload), cid) + payload
    return bytes([0x02]) + struct.pack("<HH", handle | (pb << 12), len(body)) + body


def att_op(opcode: int, handle: int, value: bytes) -> bytes:
    return bytes([opcode]) + struct.pack("<H", handle) + value
