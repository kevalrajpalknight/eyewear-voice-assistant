import struct
import zipfile
from pathlib import Path

import pytest

from aiglasses.domain.errors import ProtocolError
from aiglasses.research.dexstrings import apk_strings, class_packages, dex_strings


def make_dex(strings: list[str]) -> bytes:
    """Minimal DEX with only a header and a string table (enough for the string reader)."""
    header_len = 0x70
    table_off = header_len
    data_off = table_off + 4 * len(strings)
    blobs = b""
    offsets = []
    for s in strings:
        encoded = s.encode("utf-8")
        offsets.append(data_off + len(blobs))
        blobs += bytes([len(s)]) + encoded + b"\x00"
    header = bytearray(header_len)
    header[0:8] = b"dex\n035\x00"
    struct.pack_into("<II", header, 0x38, len(strings), table_off)
    return bytes(header) + struct.pack(f"<{len(strings)}I", *offsets) + blobs


STRINGS = [
    "AM01G1",
    "Lcom/jieli/jl_rcsp/Cmd;",
    "Lcom/jieli/jl_rcsp/Other;",
    "Lcom/jieli/ota/Up;",
    "x y",
]


def test_dex_strings_roundtrip() -> None:
    assert dex_strings(make_dex(STRINGS)) == STRINGS


def test_rejects_non_dex() -> None:
    with pytest.raises(ProtocolError):
        dex_strings(b"PK\x03\x04" + bytes(200))


def test_apk_strings_and_packages(tmp_path: Path) -> None:
    apk = tmp_path / "x.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.writestr("classes.dex", make_dex(STRINGS))
        zf.writestr("classes2.dex", make_dex(["Lcom/jieli/ota/Two;"]))
        zf.writestr("assets/not_dex.bin", b"nothing")
    found = dict(apk_strings(apk))
    assert list(found) == ["classes.dex", "classes2.dex"]
    counts = class_packages(found["classes.dex"] + found["classes2.dex"], "com.jieli")
    assert counts == {"com.jieli.jl_rcsp": 2, "com.jieli.ota": 2}


def make_dex_with_methods(
    strings: list[str], types: list[int], methods: list[tuple[int, int]]
) -> bytes:
    """DEX with a string table, type ids and method ids (class type index, name string index)."""
    base = bytearray(make_dex(strings))
    string_table_end = len(base)
    type_off = string_table_end
    base += struct.pack(f"<{len(types)}I", *types)
    method_off = len(base)
    for class_idx, name_idx in methods:
        base += struct.pack("<HHI", class_idx, 0, name_idx)
    struct.pack_into("<II", base, 0x40, len(types), type_off)
    struct.pack_into("<II", base, 0x58, len(methods), method_off)
    return bytes(base)


def test_method_names_per_class() -> None:
    from aiglasses.research.dexstrings import dex_method_names

    strings = ["Lcom/x/Handler;", "Lcom/x/Other;", "glassesControl", "getBattery", "close"]
    data = make_dex_with_methods(strings, types=[0, 1], methods=[(0, 2), (0, 3), (1, 4)])
    assert dex_method_names(data) == {
        "Lcom/x/Handler;": {"glassesControl", "getBattery"},
        "Lcom/x/Other;": {"close"},
    }
