"""Decode 0x73 device events (INFERRED from the official app's parsing; matches our captures)."""

from dataclasses import dataclass, field

from aiglasses.domain.models import GlassesEvent
from aiglasses.protocol.bcframe import BcFrame
from aiglasses.protocol.voice import VOICE_CMD

EVENT_CMD = 0x73


@dataclass(frozen=True)
class DeviceEvent:
    kind: str
    text: str
    fields: dict[str, int | bool | str] = field(default_factory=dict)


def _le16(data: bytes) -> int:
    return int.from_bytes(data, "little")


def _byte(payload: bytes, index: int, default: int = 0) -> int:
    return payload[index] if index < len(payload) else default


def decode_event(payload: bytes) -> DeviceEvent:
    """Describe a 0x73 payload; the first byte is the event type."""
    if not payload:
        return DeviceEvent("empty", "empty event")
    kind = payload[0]
    if kind == 1 and len(payload) >= 7:
        photos, videos, audio = _le16(payload[1:3]), _le16(payload[3:5]), _le16(payload[5:7])
        list_type = _byte(payload, 7)
        only_ap = _byte(payload, 8) == 1
        return DeviceEvent(
            "media_counts",
            f"media counts: photos {photos}, videos {videos}, audio recordings {audio}, "
            f"file-list type {list_type}, only access-point import {only_ap}",
            {"photos": photos, "videos": videos, "audio": audio, "file_list_type": list_type},
        )
    if kind == 2:
        return DeviceEvent(
            "image_capture_ready", "AI image capture ready (thumbnail can be fetched)"
        )
    if kind == 3:
        started = _byte(payload, 1) == 1
        return DeviceEvent(
            "ai_voice", f"AI voice {'start' if started else 'stop'}", {"on": started}
        )
    if kind == 4:
        return DeviceEvent("update_prompt", "glasses ask for the firmware-update screen (ignore)")
    if kind == 5 and len(payload) >= 3:
        percent, charging = payload[1], payload[2] == 1
        return DeviceEvent(
            "battery",
            f"battery {percent}%, charging {charging}",
            {"percent": percent, "charging": charging},
        )
    if kind == 8 and len(payload) >= 5:
        ip = ".".join(str(b) for b in payload[1:5])
        return DeviceEvent("ip_address", f"glasses IP {ip}", {"ip": ip})
    if kind == 9:
        return DeviceEvent("error", f"error {_byte(payload, 1)}/{_byte(payload, 2)}")
    if kind == 0x0A:
        return DeviceEvent("ai_voice_end", "AI voice / recognition ended")
    if kind == 0x0B and len(payload) >= 2:
        return DeviceEvent("temperature", f"temperature {payload[1]} C", {"celsius": payload[1]})
    if kind == 0x0C:
        return DeviceEvent("interrupt", "user interrupted the spoken answer")
    if kind == 0x0D:
        return DeviceEvent("unbind", "unbind request")
    if kind == 0x12:
        groups = [tuple(payload[i : i + 4]) for i in (1, 5, 9) if i + 3 < len(payload)]
        return DeviceEvent("volume", f"volume settings (id, a, b, current) {groups}")
    return DeviceEvent("unknown", f"event type 0x{kind:02x}, payload {payload.hex()}")


def frame_to_event(frame: BcFrame, timestamp: float) -> GlassesEvent | None:
    """A typed event for 0x73 device events and 0x59 voice frames; replies to requests give None."""
    if frame.cmd == EVENT_CMD:
        event = decode_event(frame.payload)
        return GlassesEvent(event.kind, frame.payload, timestamp, event.text, dict(event.fields))
    if frame.cmd == VOICE_CMD:
        return GlassesEvent(
            "voice_frame", frame.payload, timestamp, "voice frame (40-byte Opus packet)"
        )
    return None
