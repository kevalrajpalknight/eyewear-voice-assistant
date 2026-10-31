def short_uuid(value: int) -> str:
    """Expand a 16-bit Bluetooth SIG UUID to its 128-bit string form."""
    return f"0000{value:04x}-0000-1000-8000-00805f9b34fb"


DEVICE_INFO_SERVICE = short_uuid(0x180A)
BATTERY_SERVICE = short_uuid(0x180F)
DEVICE_NAME = short_uuid(0x2A00)
SERIAL_NUMBER = short_uuid(0x2A25)
FIRMWARE_REVISION = short_uuid(0x2A26)
HARDWARE_REVISION = short_uuid(0x2A27)
BATTERY_LEVEL = short_uuid(0x2A19)

# Custom channel used by the official app (CONFIRMED on this unit)
GLASSES_WRITE = "de5bf72a-d711-4e47-af26-65e3012a5dc7"
GLASSES_NOTIFY = "de5bf729-d711-4e47-af26-65e3012a5dc7"
