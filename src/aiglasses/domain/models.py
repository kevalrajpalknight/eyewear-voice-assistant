from dataclasses import dataclass, field
from enum import Enum


class Confidence(str, Enum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"


class Capability(str, Enum):
    BLE_SCAN = "ble_scan"
    BLE_CONNECT = "ble_connect"
    GATT_DUMP = "gatt_dump"
    NOTIFICATIONS = "notifications"
    DEVICE_INFO = "device_info"
    BATTERY = "battery"
    PHOTO = "photo"
    VIDEO = "video"
    MEDIA_DOWNLOAD = "media_download"
    WIFI_HOTSPOT = "wifi_hotspot"
    AUDIO_PLAYBACK = "audio_playback"
    AUDIO_RECORD = "audio_record"
    DEVICE_AUDIO_RECORDING = "device_audio_recording"
    AI_TRANSLATION = "ai_translation"
    EVENTS = "events"


@dataclass(frozen=True)
class ScanResult:
    address: str
    name: str | None
    rssi: int
    service_uuids: list[str] = field(default_factory=list)
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_data: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class GattDescriptor:
    uuid: str
    handle: int


@dataclass(frozen=True)
class GattCharacteristic:
    uuid: str
    handle: int
    properties: list[str]
    descriptors: list[GattDescriptor] = field(default_factory=list)


@dataclass(frozen=True)
class GattService:
    uuid: str
    handle: int
    characteristics: list[GattCharacteristic] = field(default_factory=list)


@dataclass(frozen=True)
class DeviceInfo:
    name: str | None
    address: str
    hardware: str | None = None
    firmware: str | None = None
    serial: str | None = None


@dataclass(frozen=True)
class BatteryStatus:
    percent: int
    charging: bool | None = None


@dataclass(frozen=True)
class MediaItem:
    name: str
    kind: str
    size: int | None = None
    url: str | None = None


@dataclass(frozen=True)
class GlassesEvent:
    kind: str
    raw: bytes
    timestamp: float
