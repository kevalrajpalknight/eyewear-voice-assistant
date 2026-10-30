import json
from datetime import datetime
from pathlib import Path

from aiglasses.domain.errors import ProtocolError
from aiglasses.protocol.bcframe import BcFrame, parse_bc_frame


def load_frames(trace_path: Path, uuid_prefix: str = "de5bf729") -> list[tuple[float, BcFrame]]:
    """Parse notification entries from trace.jsonl into (epoch seconds, frame); skips non-frames."""
    frames: list[tuple[float, BcFrame]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["op"] != "notify" or not entry["uuid"].startswith(uuid_prefix):
            continue
        try:
            frame = parse_bc_frame(bytes.fromhex(entry["hex"]))
        except ProtocolError:
            continue
        frames.append((datetime.fromisoformat(entry["ts"]).timestamp(), frame))
    return frames
