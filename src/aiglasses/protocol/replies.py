"""Parse replies to our requests, following the official app's layouts."""

from dataclasses import dataclass

from aiglasses.domain.errors import ProtocolError
from aiglasses.domain.models import BatteryStatus, MediaCounts

ACTIVITY_NAMES = {
    1: "photo mode",
    2: "video recording",
    4: "transfer mode",
    5: "firmware update mode",
    7: "AI conversation mode",
    8: "audio recording",
    255: "none reported",
}


@dataclass(frozen=True)
class VersionStrings:
    firmware: str
    hardware: str
    wifi_firmware: str
    wifi_hardware: str


def _le16(data: bytes) -> int:
    return int.from_bytes(data, "little")


def parse_battery(payload: bytes) -> BatteryStatus:
    """Battery reply (cmd 0x42): [percent 0..100][charging 1/0]."""
    if len(payload) < 2:
        raise ProtocolError(f"battery reply too short ({len(payload)} bytes)")
    if not 0 <= payload[0] <= 100:
        raise ProtocolError(f"battery value {payload[0]} is not a percentage")
    return BatteryStatus(percent=payload[0], charging=payload[1] == 1)


def parse_device_info(payload: bytes) -> VersionStrings:
    """Device info reply (cmd 0x43): [1 byte][4 x len16][firmware, hardware, wifi fw, wifi hw]."""
    if len(payload) < 9:
        raise ProtocolError(f"device info reply too short ({len(payload)} bytes)")
    lengths = [_le16(payload[i : i + 2]) for i in (1, 3, 5, 7)]
    if 9 + sum(lengths) > len(payload):
        raise ProtocolError(f"string lengths {lengths} exceed the payload of {len(payload)} bytes")
    parts, pos = [], 9
    for length in lengths:
        parts.append(payload[pos : pos + length].decode("utf-8", errors="replace"))
        pos += length
    return VersionStrings(*parts)


def parse_media_counts(payload: bytes) -> MediaCounts:
    """Control reply of type 4 (reply to the album read): counts at bytes 2 to 7."""
    if len(payload) < 8 or payload[1] != 4:
        raise ProtocolError(f"not a media-count reply (raw {payload.hex()})")
    return MediaCounts(
        photos=_le16(payload[2:4]),
        videos=_le16(payload[4:6]),
        audio=_le16(payload[6:8]),
        file_list_type=payload[8] if len(payload) > 8 else None,
    )


def describe_control_reply(payload: bytes) -> str:
    """Control reply of type 1 (reply to a camera command). Meaning is inferred, not confirmed."""
    if len(payload) < 4 or payload[1] != 1:
        raise ProtocolError(f"unexpected control reply (raw {payload.hex()})")
    activity = payload[4] if len(payload) > 4 else None
    shown = ACTIVITY_NAMES.get(activity, "unknown") if activity is not None else "absent"
    return (
        f"function {payload[2]}, error code {payload[3]}, activity byte {activity} ({shown}); "
        "meaning inferred from the app, not confirmed"
    )
