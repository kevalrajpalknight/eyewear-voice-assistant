class GlassesError(Exception):
    """Base class for all library errors."""


class NotSupportedError(GlassesError):
    """A capability that has not been verified on this unit."""


class DeviceNotFoundError(GlassesError):
    """The glasses were not seen while scanning."""


class BluetoothUnavailableError(GlassesError):
    """The host Bluetooth radio is missing or powered off."""


class WriteBlockedError(GlassesError):
    """WriteGuard refused a BLE write."""


class ProtocolError(GlassesError):
    """A frame or command definition is malformed."""
