import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import sounddevice as sd

from aiglasses.transport.base import AudioEndpoint, Samples

WASAPI = "Windows WASAPI"


def guess_profile(name: str) -> str:
    """Windows names the A2DP endpoint "Headphones (...)" and the HFP one "Headset (...)"."""
    lowered = name.lower()
    if lowered.startswith("headphones"):
        return "a2dp"
    if lowered.startswith("headset") or "hands-free" in lowered:
        return "hfp"
    return "unknown"


def select_endpoints(
    devices: Sequence[Mapping[str, Any]],
    hostapis: Sequence[Mapping[str, Any]],
    name_hint: str,
    hostapi_name: str = WASAPI,
) -> list[AudioEndpoint]:
    found: list[AudioEndpoint] = []
    for index, dev in enumerate(devices):
        if hostapis[dev["hostapi"]]["name"] != hostapi_name:
            continue
        if name_hint.lower() not in str(dev["name"]).lower():
            continue
        for kind, channels in (
            ("output", dev["max_output_channels"]),
            ("input", dev["max_input_channels"]),
        ):
            if channels > 0:
                found.append(
                    AudioEndpoint(
                        name=str(dev["name"]),
                        kind=kind,
                        sample_rate=int(dev["default_samplerate"]),
                        index=index,
                        channels=int(channels),
                        hostapi=hostapi_name,
                        profile=guess_profile(str(dev["name"])),
                    )
                )
    return found


async def _wait_for_stream() -> None:
    """Poll instead of blocking: WASAPI streams fail to start from asyncio worker threads."""
    stream = sd.get_stream()
    while stream.active:  # noqa: ASYNC110 (PortAudio exposes no awaitable)
        await asyncio.sleep(0.05)
    sd.wait()


class SoundDeviceAudio:
    """WASAPI shared-mode playback and recording through sounddevice."""

    def list_endpoints(self, name_hint: str) -> list[AudioEndpoint]:
        return select_endpoints(list(sd.query_devices()), list(sd.query_hostapis()), name_hint)

    async def play(self, samples: Samples, sample_rate: int, endpoint: AudioEndpoint) -> None:
        sd.play(
            samples,
            samplerate=sample_rate,
            device=endpoint.index,
            extra_settings=sd.WasapiSettings(auto_convert=True),
        )
        await _wait_for_stream()

    async def record(self, seconds: float, endpoint: AudioEndpoint) -> Samples:
        data = sd.rec(
            int(seconds * endpoint.sample_rate),
            samplerate=endpoint.sample_rate,
            channels=1,
            dtype="float32",
            device=endpoint.index,
            extra_settings=sd.WasapiSettings(auto_convert=True),
        )
        await _wait_for_stream()
        return np.asarray(data[:, 0], dtype=np.float32)
