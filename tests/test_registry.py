from pathlib import Path

import pytest

from aiglasses.domain.errors import ProtocolError
from aiglasses.domain.models import Confidence
from aiglasses.protocol import RawCodec, allowlist_from, load_commands

CHAR = "0000ae01-0000-1000-8000-00805f9b34fb"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "commands.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_packaged_registry_is_empty() -> None:
    assert load_commands() == []


def test_load_and_allowlist(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        f"""commands:
  - {{name: a, characteristic: {CHAR}, bytes: "01", direction: write,
     status: CONFIRMED, source: cap-1, safe_to_send: true}}
  - {{name: b, characteristic: {CHAR}, bytes: "02", direction: write,
     status: INFERRED, safe_to_send: true}}
  - {{name: c, characteristic: {CHAR}, bytes: "03", direction: write,
     status: CONFIRMED, source: cap-1, safe_to_send: false}}
""",
    )
    commands = load_commands(path)
    assert [c.status for c in commands] == [
        Confidence.CONFIRMED,
        Confidence.INFERRED,
        Confidence.CONFIRMED,
    ]
    assert [c.name for c in allowlist_from(commands)] == ["a"]


def test_confirmed_requires_source(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        f'commands: [{{name: a, characteristic: {CHAR}, bytes: "01", direction: write, '
        "status: CONFIRMED}]",
    )
    with pytest.raises(ProtocolError):
        load_commands(path)


def test_bad_hex_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        f'commands: [{{name: a, characteristic: {CHAR}, bytes: "zz", direction: write, '
        "status: INFERRED}]",
    )
    with pytest.raises(ProtocolError):
        load_commands(path)


def test_raw_codec_roundtrip() -> None:
    codec = RawCodec()
    assert codec.decode(codec.encode(b"\x01\x02")).payload == b"\x01\x02"
