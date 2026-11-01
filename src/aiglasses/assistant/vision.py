"""Vision: an image source (the glasses camera, later) and a model that describes the image.

`GlassesWifiImageSource` is a stub: images from the glasses need Wi-Fi Direct, which stays blocked
until it is approved, so it raises NotSupportedError. Cloud vision is off by default for privacy.
"""

import base64
import io
from typing import ClassVar, Protocol

import httpx
from pydantic import BaseModel

from aiglasses.assistant.tools.base import NoArgs, Risk, ToolContext, ToolRefused, ToolResult
from aiglasses.domain.errors import GlassesError, NotSupportedError
from aiglasses.settings import AssistantSettings

VISION_PROMPT = (
    "Describe what is in this picture in at most two short spoken sentences. "
    "Plain words, no lists, no markdown."
)
JPEG_QUALITY = 85
LOCAL_ROUTE = "/v1/chat/completions"
LOCAL_TIMEOUT_S = 60.0


class VisionError(GlassesError):
    pass


class ImageUnavailable(GlassesError):
    """The image could not be fetched. `spoken` is the short line to say."""

    def __init__(self, message: str, spoken: str) -> None:
        super().__init__(message)
        self.spoken = spoken


class ImageSource(Protocol):
    async def capture(self) -> bytes:
        """Return one image (JPEG or PNG bytes)."""
        ...


class VisionClient(Protocol):
    async def describe(self, image: bytes, prompt: str) -> str: ...


class GlassesWifiImageSource:
    """Placeholder until Wi-Fi Direct is approved (not before 2026-11-07, and with approval)."""

    async def capture(self) -> bytes:
        raise NotSupportedError("images from the glasses need Wi-Fi Direct, which is not enabled")


class FakeImageSource:
    def __init__(self, image: bytes) -> None:
        self.image = image
        self.captures = 0

    async def capture(self) -> bytes:
        self.captures += 1
        return self.image


class FakeVision:
    def __init__(self, reply: str = "A red square on a white wall.") -> None:
        self.reply = reply
        self.calls: list[tuple[int, str]] = []

    async def describe(self, image: bytes, prompt: str) -> str:
        self.calls.append((len(image), prompt))
        return self.reply


def shrink(image: bytes, max_side: int) -> bytes:
    """Scale so the longest side is at most `max_side` pixels; returns JPEG bytes."""
    from PIL import Image

    with Image.open(io.BytesIO(image)) as opened:
        rgb = opened.convert("RGB")
    rgb.thumbnail((max_side, max_side))
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue()


def data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


class LocalVision:
    """The local llama-server with a multimodal projector loaded."""

    def __init__(
        self, base_url: str, max_side: int, client: httpx.AsyncClient | None = None
    ) -> None:
        self._url = base_url.rstrip("/") + LOCAL_ROUTE
        self._max_side = max_side
        self._client = client or httpx.AsyncClient()

    async def describe(self, image: bytes, prompt: str) -> str:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri(shrink(image, self._max_side))}},
        ]
        body = {
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 120,
            "temperature": 0.2,
        }
        try:
            reply = await self._client.post(self._url, json=body, timeout=LOCAL_TIMEOUT_S)
            reply.raise_for_status()
            return str(reply.json()["choices"][0]["message"]["content"]).strip()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise VisionError(f"local vision failed: {type(exc).__name__}") from exc


def build_vision(settings: AssistantSettings, base_url: str) -> VisionClient | None:
    """None when VISION_PROVIDER is off. Cloud vision is built by `CloudVision` in llm_client."""
    if settings.vision_provider == "local":
        return LocalVision(base_url, settings.vision_max_image_side)
    return None


class DescribeSceneTool:
    """Looks through the glasses camera and describes it. Tainted: the text comes from a model that
    saw an outside scene, so device tools are disabled for the rest of the turn."""

    name: ClassVar[str] = "describe_scene"
    description: ClassVar[str] = "Look through the glasses camera and describe what is there."
    risk: ClassVar[Risk] = Risk.DEVICE_STATE
    args_model: ClassVar[type[BaseModel]] = NoArgs
    announcement: ClassVar[str | None] = "Let me look."

    def __init__(self, source: ImageSource, vision: VisionClient | None) -> None:
        self._source, self._vision = source, vision

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        if self._vision is None:
            raise ToolRefused("vision is off", "Looking is switched off.")
        try:
            image = await self._source.capture()
        except ImageUnavailable as exc:
            raise ToolRefused(str(exc), exc.spoken) from exc
        except NotSupportedError as exc:
            raise ToolRefused(
                "image source not supported",
                "I can't fetch the photo from the glasses yet, so I can't look at it.",
            ) from exc
        try:
            text = await self._vision.describe(image, VISION_PROMPT)
        except VisionError as exc:
            raise ToolRefused("vision failed", "I couldn't make that out.") from exc
        return ToolResult(f"scene: {text}", tainted=True)
