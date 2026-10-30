from pathlib import Path

from aiglasses.domain.errors import NotSupportedError
from aiglasses.transport.base import AudioEndpoint, AudioIO


class AudioService:
    def __init__(self, audio: AudioIO | None) -> None:
        self._audio = audio

    def endpoints(self) -> list[AudioEndpoint]:
        raise NotSupportedError("audio endpoint matching arrives in Phase 2")

    async def play_file(self, path: Path) -> None:
        raise NotSupportedError("audio playback arrives in Phase 2")

    async def record(self, seconds: float, path: Path) -> None:
        raise NotSupportedError("audio recording arrives in Phase 2")
