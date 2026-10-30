from aiglasses.protocol.bcframe import START_BYTE, crc16_modbus


def build_bc_frame(cmd: int, payload: bytes) -> bytes:
    """Test fixture builder only. The library never constructs frames to send."""
    header = bytes([START_BYTE, cmd]) + len(payload).to_bytes(2, "little")
    return header + crc16_modbus(payload).to_bytes(2, "little") + payload
