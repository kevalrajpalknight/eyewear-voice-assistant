def ascii_of(data: bytes) -> str:
    """Printable ASCII view of a payload; non-printable bytes become dots."""
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)
