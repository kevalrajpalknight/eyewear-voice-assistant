"""Read DEX string tables with the standard library only (read-only analysis)."""

import re
import struct
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from aiglasses.domain.errors import ProtocolError

DEX_MAGIC = b"dex\n"
STRING_IDS_SIZE_OFFSET = 0x38
STRING_IDS_OFF_OFFSET = 0x3C


def _uleb128(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def dex_strings(data: bytes) -> list[str]:
    if data[:4] != DEX_MAGIC:
        raise ProtocolError("not a DEX file")
    count = struct.unpack_from("<I", data, STRING_IDS_SIZE_OFFSET)[0]
    table = struct.unpack_from("<I", data, STRING_IDS_OFF_OFFSET)[0]
    strings: list[str] = []
    for i in range(count):
        offset = struct.unpack_from("<I", data, table + 4 * i)[0]
        _length, start = _uleb128(data, offset)
        end = data.index(b"\x00", start)
        strings.append(data[start:end].decode("utf-8", "replace"))
    return strings


def apk_strings(apk: Path) -> Iterator[tuple[str, list[str]]]:
    """Yield (dex file name, strings) for every classes*.dex in the APK."""
    with zipfile.ZipFile(apk) as zf:
        for name in sorted(n for n in zf.namelist() if re.fullmatch(r"classes\d*\.dex", n)):
            yield name, dex_strings(zf.read(name))


def class_packages(strings: list[str], prefix: str, depth: int = 3) -> Counter[str]:
    """Count class descriptors (`Lcom/x/Y;`) under a dotted package prefix, grouped by package."""
    wanted = "L" + prefix.replace(".", "/")
    counts: Counter[str] = Counter()
    for s in strings:
        if s.startswith(wanted) and s.endswith(";") and " " not in s:
            package = "/".join(s[1:-1].split("/")[:-1][:depth])
            counts[package.replace("/", ".")] += 1
    return counts


TYPE_IDS_SIZE_OFFSET = 0x40
TYPE_IDS_OFF_OFFSET = 0x44
METHOD_IDS_SIZE_OFFSET = 0x58
METHOD_IDS_OFF_OFFSET = 0x5C


def dex_method_names(data: bytes) -> dict[str, set[str]]:
    """Map class descriptor to the method names declared or referenced on it in this DEX."""
    strings = dex_strings(data)
    type_count, type_off = struct.unpack_from("<II", data, TYPE_IDS_SIZE_OFFSET)
    method_count, method_off = struct.unpack_from("<II", data, METHOD_IDS_SIZE_OFFSET)
    types = [
        strings[struct.unpack_from("<I", data, type_off + 4 * i)[0]] for i in range(type_count)
    ]
    methods: dict[str, set[str]] = {}
    for i in range(method_count):
        class_idx, _proto_idx, name_idx = struct.unpack_from("<HHI", data, method_off + 8 * i)
        methods.setdefault(types[class_idx], set()).add(strings[name_idx])
    return methods


def apk_method_names(apk: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    with zipfile.ZipFile(apk) as zf:
        for name in sorted(n for n in zf.namelist() if re.fullmatch(r"classes\d*\.dex", n)):
            for cls, names in dex_method_names(zf.read(name)).items():
                result.setdefault(cls, set()).update(names)
    return result
