from aiglasses.domain.models import Capability, Confidence

# CONFIRMED rows come from prototype scripts run against this unit (see docs/CAPABILITIES.md).
CAPABILITIES: dict[Capability, Confidence] = {
    Capability.BLE_SCAN: Confidence.CONFIRMED,
    Capability.BLE_CONNECT: Confidence.CONFIRMED,
    Capability.GATT_DUMP: Confidence.CONFIRMED,
    Capability.NOTIFICATIONS: Confidence.CONFIRMED,
    Capability.DEVICE_INFO: Confidence.CONFIRMED,
    Capability.BATTERY: Confidence.UNVERIFIED,
    Capability.PHOTO: Confidence.UNVERIFIED,
    Capability.VIDEO: Confidence.UNVERIFIED,
    Capability.MEDIA_DOWNLOAD: Confidence.UNVERIFIED,
    Capability.WIFI_HOTSPOT: Confidence.UNVERIFIED,
    Capability.AUDIO_PLAYBACK: Confidence.INFERRED,
    Capability.AUDIO_RECORD: Confidence.UNVERIFIED,
    Capability.DEVICE_AUDIO_RECORDING: Confidence.UNVERIFIED,
    Capability.AI_TRANSLATION: Confidence.UNVERIFIED,
    Capability.EVENTS: Confidence.UNVERIFIED,
}
