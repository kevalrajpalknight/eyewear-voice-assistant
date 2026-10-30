from pathlib import Path

from aiglasses.domain.errors import NotSupportedError
from aiglasses.domain.models import MediaItem
from aiglasses.safety import WriteGuard


class CameraService:
    def __init__(self, guard: WriteGuard) -> None:
        self._guard = guard

    async def take_photo(self) -> None:
        raise NotSupportedError("photo needs a captured command (Phase 3/4)")

    async def start_video(self) -> None:
        raise NotSupportedError("video needs a captured command (Phase 3/4)")

    async def stop_video(self) -> None:
        raise NotSupportedError("video needs a captured command (Phase 3/4)")

    async def list_media(self) -> list[MediaItem]:
        raise NotSupportedError("media listing needs the Wi-Fi link (Phase 4)")

    async def pull_media(self, item: MediaItem, dest: Path) -> Path:
        raise NotSupportedError("media download needs the Wi-Fi link (Phase 4)")
