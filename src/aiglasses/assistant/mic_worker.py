"""Microphone worker process: streams 16 kHz mono PCM to stdout. Run as `python -m ...mic_worker`.

It exists because sounddevice must never be imported in the process that talks BLE (ADR 0004).
Out: R = ready (4-byte sample rate), F = one frame of int16 mono samples, X = error text.
It listens only; it plays nothing. It exits when its stdin closes (the parent went away).
Arguments: --device <name part, optional>  --rate <Hz, default 16000>  --block-ms <ms, default 20>
"""

import argparse
import signal
import struct
import sys
import threading
from typing import Any

import numpy as np

HEADER = struct.Struct("<cI")
_send_lock = threading.Lock()


def send(kind: bytes, payload: bytes = b"") -> None:
    with _send_lock:
        out = sys.stdout.buffer
        out.write(HEADER.pack(kind, len(payload)) + payload)
        out.flush()


def pick_device(sd: Any, wanted: str | None) -> int | None:
    """The first input whose name contains `wanted` (case-insensitive); None = system default."""
    if not wanted:
        return None
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and wanted.lower() in str(device["name"]).lower():
            return index
    raise ValueError(f"no input device name contains {wanted!r}")


def resample(block: np.ndarray, source: int, target: int) -> np.ndarray:
    if source == target:
        return block
    positions = np.linspace(0, len(block) - 1, max(1, len(block) * target // source))
    return np.interp(positions, np.arange(len(block)), block.astype(np.float32)).astype(np.int16)


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is the parent's to handle; we exit at EOF
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default=None)
    parser.add_argument("--rate", type=int, default=16000)
    parser.add_argument("--block-ms", type=int, default=20)
    args = parser.parse_args()
    try:
        import sounddevice as sd

        device = pick_device(sd, args.device)
        native = int(sd.query_devices(device, "input")["default_samplerate"])
        for rate in (args.rate, native):
            try:
                stream = sd.InputStream(
                    device=device, samplerate=rate, channels=1, dtype="int16",
                    blocksize=rate * args.block_ms // 1000,
                    callback=lambda data, frames, _t, _s, rate=rate: send(
                        b"F", resample(data[:, 0].copy(), rate, args.rate).tobytes()
                    ),
                )  # fmt: skip
                stream.start()
                break
            except sd.PortAudioError:
                if rate == native:
                    raise
        send(b"R", struct.pack("<I", args.rate))
    except Exception as exc:
        send(b"X", f"{type(exc).__name__}: {exc}"[:200].encode())
        return
    while sys.stdin.buffer.read(1024):
        pass  # the parent never writes; EOF means it is gone
    stream.stop()
    stream.close()


if __name__ == "__main__":
    main()
