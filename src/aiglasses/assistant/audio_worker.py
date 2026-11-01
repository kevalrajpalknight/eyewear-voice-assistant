"""Speaker worker process: plays PCM chunks received on stdin. Run as `python -m ...audio_worker`.

It exists because sounddevice must never be imported in the process that talks BLE (ADR 0004).
Messages are 1 type byte, a 4-byte little-endian length and the payload.
In:  C = 4-byte sample rate + int16 mono samples, E = end of turn, S = stop now, Q = quit
Out: R = ready, P = playback started, D = turn finished, T = stop acknowledged, X = error text
"""

import signal
import struct
import sys
import threading
from collections import deque
from typing import Any

import numpy as np

HEADER = struct.Struct("<cI")


_send_lock = threading.Lock()


def send(kind: bytes, payload: bytes = b"") -> None:
    with _send_lock:
        out = sys.stdout.buffer
        out.write(HEADER.pack(kind, len(payload)) + payload)
        out.flush()


class Player:
    def __init__(self) -> None:
        import sounddevice as sd

        self._sd: Any = sd
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._ended = False
        self._started = False
        self._stream: Any = None
        self._rate = 0

    def _callback(self, outdata: np.ndarray, frames: int, _time: Any, _status: Any) -> None:
        outdata.fill(0)
        filled = 0
        notify: list[bytes] = []
        with self._lock:
            while filled < frames and self._chunks:
                chunk = self._chunks[0]
                take = min(len(chunk), frames - filled)
                outdata[filled : filled + take, 0] = chunk[:take]
                filled += take
                if take == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[take:]
            if filled and not self._started:
                self._started = True
                notify.append(b"P")
            if self._ended and not self._chunks and self._started:
                self._ended = False
                self._started = False
                notify.append(b"D")
        for kind in notify:
            send(kind)

    def add(self, rate: int, samples: np.ndarray) -> None:
        with self._lock:
            if self._stream is not None and rate != self._rate and not self._chunks:
                self._stream.close()
                self._stream = None
            if self._stream is None:
                self._rate = rate
                self._stream = self._sd.OutputStream(
                    samplerate=rate, channels=1, dtype="int16", callback=self._callback
                )
                self._stream.start()
            self._chunks.append(samples)

    def end(self) -> None:
        with self._lock:
            self._ended = True

    def stop(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._ended = False
            self._started = False
        send(b"T")

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()


def read_exact(count: int) -> bytes:
    data = sys.stdin.buffer.read(count)
    return data if len(data) == count else b""


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is the parent's to handle; we exit at EOF
    player = Player()
    send(b"R")
    while True:
        header = read_exact(HEADER.size)
        if not header:
            break
        kind, length = HEADER.unpack(header)
        payload = read_exact(length) if length else b""
        try:
            if kind == b"C":
                rate = struct.unpack("<I", payload[:4])[0]
                player.add(rate, np.frombuffer(payload[4:], dtype=np.int16))
            elif kind == b"E":
                player.end()
            elif kind == b"S":
                player.stop()
            elif kind == b"Q":
                break
        except Exception as exc:
            send(b"X", str(exc).encode())
    player.close()


if __name__ == "__main__":
    main()
