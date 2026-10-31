import pytest

from aiglasses.protocol.bcframe import parse_bc_frame
from aiglasses.protocol.events import decode_event


def test_media_counts_from_the_frame_seen_after_connecting() -> None:
    frame = parse_bc_frame(bytes.fromhex("bc73080054070104000000010001"))
    event = decode_event(frame.payload)
    assert event.kind == "media_counts"
    assert (event.fields["photos"], event.fields["videos"], event.fields["audio"]) == (4, 0, 1)
    assert event.fields["file_list_type"] == 1
    assert "only access-point import False" in event.text


@pytest.mark.parametrize(
    ("payload", "kind", "needle"),
    [
        ("0107000000000001", "media_counts", "photos 7, videos 0"),
        ("0108000200000001", "media_counts", "photos 8, videos 2"),
        ("0301", "ai_voice", "start"),
        ("0a01", "ai_voice_end", "ended"),
        ("0b2c", "temperature", "44 C"),
        ("0200170200", "image_capture_ready", "thumbnail"),
        ("120100100f02000f0f0300100a03", "volume", "(1, 0, 16, 15)"),
        ("0564 01".replace(" ", ""), "battery", "battery 100%, charging True"),
        ("08c0a83101", "ip_address", "192.168.49.1"),
    ],
)
def test_known_payloads(payload: str, kind: str, needle: str) -> None:
    event = decode_event(bytes.fromhex(payload))
    assert event.kind == kind and needle in event.text


def test_unknown_and_short_payloads_do_not_crash() -> None:
    assert decode_event(b"").kind == "empty"
    assert decode_event(bytes([0x7F, 1])).kind == "unknown"
    assert decode_event(bytes([1, 2])).kind == "unknown"  # too short for media counts
