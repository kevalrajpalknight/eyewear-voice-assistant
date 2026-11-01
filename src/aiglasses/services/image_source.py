"""The glasses as a camera for the assistant: one picture over Bluetooth, no Wi-Fi."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Protocol

from aiglasses.assistant.vision import ImageUnavailable
from aiglasses.domain.errors import GlassesError
from aiglasses.services.control import APPROVED_AI_PHOTO_FRAME
from aiglasses.services.thumbnail import ThumbnailResult

log = logging.getLogger("aiglasses.photo")


class ThumbnailDevice(Protocol):
    async def capture_ai_photo(self, *, approved_frame: str) -> None: ...

    async def fetch_thumbnail(self) -> ThumbnailResult: ...

    async def wait_for_ai_capture(self, timeout: float) -> None: ...


class BleThumbnailImageSource:
    """`ImageSource` over Bluetooth only: the glasses' AI-photo thumbnail (a few KB, about 1 s).

    mode "ai_photo": ask the glasses for an AI-vision photo (hands-free), then read the thumbnail.
    mode "wait": wait for the person to double-click the rear button, then read it. A thumbnail
    identical to the previous one is refused as stale.
    """

    def __init__(
        self, device: ThumbnailDevice, mode: str, wait_s: float, save_dir: Path | None = None
    ) -> None:
        self._device, self._mode, self._wait_s = device, mode, wait_s
        self._save_dir = save_dir
        self._last = b""

    async def capture(self) -> bytes:
        try:
            if self._mode == "wait":
                await self._device.wait_for_ai_capture(self._wait_s)
            else:
                await self._device.capture_ai_photo(approved_frame=APPROVED_AI_PHOTO_FRAME)
            result = await self._device.fetch_thumbnail()
        except GlassesError as exc:
            log.warning("picture from the glasses failed: %s: %s", type(exc).__name__, exc)
            raise ImageUnavailable(
                f"thumbnail failed: {type(exc).__name__}",
                "I couldn't get the picture from the glasses.",
            ) from exc
        if not result.valid:
            raise ImageUnavailable("thumbnail is not a complete JPEG", "That picture was damaged.")
        if result.jpeg == self._last:
            raise ImageUnavailable("thumbnail unchanged", "I didn't get a new picture.")
        self._last = result.jpeg
        if self._save_dir is not None:
            await asyncio.to_thread(self._save, result.jpeg)
        return result.jpeg

    def _save(self, jpeg: bytes) -> None:
        assert self._save_dir is not None
        self._save_dir.mkdir(parents=True, exist_ok=True)
        (self._save_dir / f"Thumb_{time.strftime('%Y%m%d_%H%M%S')}.jpg").write_bytes(jpeg)
