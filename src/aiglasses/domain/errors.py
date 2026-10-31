class GlassesError(Exception):
    """Base class for all library errors."""


class NotSupportedError(GlassesError):
    """A capability that has not been verified on this unit."""


class DeviceNotFoundError(GlassesError):
    """The glasses were not seen while scanning."""


class BluetoothUnavailableError(GlassesError):
    """The host Bluetooth radio is missing or powered off."""


class ConnectionFailedError(GlassesError):
    """The link could not be established (phone still connected, out of range, busy)."""


class PairingRequiredError(GlassesError):
    """The operation needs bonding. Never pair without asking the user first."""


class GlassesTimeoutError(GlassesError):
    """A BLE operation did not complete in time."""


class ConfirmationRequired(GlassesError):
    """A state-changing call needs an explicit confirm=True from the person present."""


class WriteBlockedError(GlassesError):
    """WriteGuard refused a BLE write."""


class ProtocolError(GlassesError):
    """A frame or command definition is malformed."""


class ConfigError(GlassesError):
    """A setting is missing or invalid. The message never contains a secret."""
