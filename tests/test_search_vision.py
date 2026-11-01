import io
import sys
import types
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw

from aiglasses.assistant.llm.llm_client import CloudVision
from aiglasses.assistant.tools import search as search_module
from aiglasses.assistant.tools.base import NoArgs, ToolContext, ToolRefused
from aiglasses.assistant.tools.search import (
    CachedSearch,
    DdgsSearch,
    FakeSearch,
    SearchError,
    SearchHit,
    SearchWeb,
    build_search,
    clean_text,
)
from aiglasses.assistant.tools.vision import (
    VISION_PROMPT,
    DescribeSceneTool,
    FakeImageSource,
    FakeVision,
    GlassesWifiImageSource,
    LocalVision,
    VisionError,
    build_vision,
    shrink,
)
from aiglasses.domain.errors import ConfigError, NotSupportedError
from aiglasses.settings import AssistantSettings, LlmRole


def settings(**values: Any) -> AssistantSettings:
    return AssistantSettings(_env_file=None, llm_model="m", llm_api_key="k", **values)


def picture(width: int = 640, height: int = 480) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).rectangle((100, 100, 300, 300), fill=(220, 20, 20))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


# --- search ----------------------------------------------------------------------------------


def test_snippets_lose_links_and_are_short() -> None:
    text = clean_text(
        "Read more at https://example.com/page and www.example.org now " + "word " * 80
    )
    assert "http" not in text and "www" not in text and len(text) <= 165 and text.endswith("...")


async def test_the_search_tool_returns_compact_tainted_results_without_urls() -> None:
    fake = FakeSearch(
        [SearchHit("Title one", "Snippet one"), SearchHit("Title two", "Snippet two")]
    )
    tool = SearchWeb(fake, settings(search_max_results=1))
    result = await tool.run(tool.args_model(query="x"), ToolContext(utterance="u"))
    assert result.tainted and result.summary == "search results: 1. Title one: Snippet one"
    assert fake.queries == ["x"]


@pytest.mark.parametrize(
    ("category", "words"),
    [
        ("rate_limit", "busy"),
        ("timeout", "too long"),
        ("empty", "nothing"),
        ("blocked", "can't search"),
    ],
)
async def test_every_search_failure_becomes_a_short_spoken_line(category: str, words: str) -> None:
    tool = SearchWeb(FakeSearch(error=category), settings())
    with pytest.raises(ToolRefused) as caught:
        await tool.run(tool.args_model(query="x"), ToolContext(utterance="u"))
    assert words in caught.value.spoken


async def test_the_cache_reuses_answers_within_the_ttl_and_never_caches_failures() -> None:
    clock = [0.0]
    fake = FakeSearch([SearchHit("A", "a")])
    cached = CachedSearch(fake, 60, lambda: clock[0])
    kwargs: dict[str, Any] = {"max_results": 5, "region": "r", "timeout_s": 1.0}
    await cached.search("Hello", **kwargs)
    await cached.search("hello ", **kwargs)
    assert len(fake.queries) == 1
    clock[0] = 61
    await cached.search("hello", **kwargs)
    assert len(fake.queries) == 2
    failing = CachedSearch(FakeSearch(error="blocked"), 60, lambda: clock[0])
    for _ in range(2):
        with pytest.raises(SearchError):
            await failing.search("q", **kwargs)


def test_unknown_providers_list_the_valid_ones() -> None:
    assert isinstance(build_search(settings(search_provider="fake")), CachedSearch)
    with pytest.raises(ConfigError, match="valid: ddgs, fake"):
        build_search(settings(search_provider="nope"))


def install_fake_ddgs(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> None:
    class Rate(Exception):
        pass

    class Timeout(Exception):
        pass

    class Client:
        def __init__(self, timeout: int = 5) -> None:
            pass

        def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
            result: list[dict[str, str]] = behaviour(Rate, Timeout, query)
            return result

    module = types.ModuleType("ddgs")
    module.DDGS = Client  # type: ignore[attr-defined]
    errors = types.ModuleType("ddgs.exceptions")
    errors.RatelimitException = Rate  # type: ignore[attr-defined]
    errors.TimeoutException = Timeout  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ddgs", module)
    monkeypatch.setitem(sys.modules, "ddgs.exceptions", errors)


async def test_the_ddgs_provider_maps_results_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs: dict[str, Any] = {"max_results": 3, "region": "wt-wt", "timeout_s": 1.0}
    install_fake_ddgs(monkeypatch, lambda R, T, q: [{"title": "T", "body": "see https://x.y body"}])
    hits = await DdgsSearch().search("q", **kwargs)
    assert hits == [SearchHit("T", "see body")]
    for exception, category in (
        ("rate", "rate_limit"),
        ("timeout", "timeout"),
        ("other", "blocked"),
    ):

        def boom(R: type, T: type, q: str, kind: str = exception) -> list[dict[str, str]]:
            raise {"rate": R, "timeout": T, "other": ValueError}[kind]("x")

        install_fake_ddgs(monkeypatch, boom)
        with pytest.raises(SearchError) as caught:
            await DdgsSearch().search("q", **kwargs)
        assert caught.value.category == category
    install_fake_ddgs(monkeypatch, lambda R, T, q: [])
    with pytest.raises(SearchError) as empty:
        await DdgsSearch().search("q", **kwargs)
    assert empty.value.category == "empty"
    assert search_module.SEARCH_PROVIDERS["ddgs"] is DdgsSearch


# --- vision ----------------------------------------------------------------------------------


def test_images_are_shrunk_to_the_longest_side_as_jpeg() -> None:
    small = Image.open(io.BytesIO(shrink(picture(640, 480), 160)))
    assert small.format == "JPEG" and max(small.size) == 160
    untouched = Image.open(io.BytesIO(shrink(picture(100, 80), 160)))
    assert untouched.size == (100, 80)


async def test_the_glasses_image_source_is_a_stub_that_refuses() -> None:
    with pytest.raises(NotSupportedError, match="Wi-Fi Direct"):
        await GlassesWifiImageSource().capture()


async def test_describe_scene_looks_describes_and_taints_the_turn() -> None:
    source, vision = FakeImageSource(picture()), FakeVision("A red square.")
    tool = DescribeSceneTool(source, vision)
    result = await tool.run(NoArgs(), ToolContext(utterance="what do you see"))
    assert result.tainted and result.summary == "scene: A red square."
    assert source.captures == 1 and vision.calls[0][1] == VISION_PROMPT
    assert "two short" in VISION_PROMPT and tool.announcement == "Let me look."


async def test_describe_scene_failure_modes_are_spoken_not_raised() -> None:
    cases = [
        (DescribeSceneTool(FakeImageSource(b""), None), "switched off"),
        (DescribeSceneTool(GlassesWifiImageSource(), FakeVision()), "from the glasses"),
    ]
    for tool, words in cases:
        with pytest.raises(ToolRefused) as caught:
            await tool.run(NoArgs(), ToolContext(utterance="u"))
        assert words in caught.value.spoken

    class Broken:
        async def describe(self, image: bytes, prompt: str) -> str:
            raise VisionError("down")

    with pytest.raises(ToolRefused, match="vision failed"):
        await DescribeSceneTool(FakeImageSource(picture()), Broken()).run(
            NoArgs(), ToolContext(utterance="u")
        )


async def test_local_vision_posts_a_shrunk_image_to_the_local_server_only() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"choices": [{"message": {"content": " A red box. "}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    vision = LocalVision("http://127.0.0.1:8081", 64, client=client)
    assert await vision.describe(picture(), "describe") == "A red box."
    assert (
        seen["url"].startswith("http://127.0.0.1:8081")
        and b"data:image/jpeg;base64" in seen["body"]
    )
    failing = LocalVision(
        "http://127.0.0.1:8081",
        64,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    with pytest.raises(VisionError):
        await failing.describe(picture(), "describe")


def test_vision_is_built_only_when_the_setting_asks_for_it() -> None:
    assert build_vision(settings(), "http://127.0.0.1:1") is None
    assert build_vision(settings(vision_provider="cloud"), "http://127.0.0.1:1") is None
    assert isinstance(
        build_vision(settings(vision_provider="local"), "http://127.0.0.1:1"), LocalVision
    )


async def test_cloud_vision_sends_the_image_in_a_multimodal_message() -> None:
    class Model:
        def __init__(self) -> None:
            self.sent: list[Any] = []

        async def ainvoke(self, messages: list[Any]) -> Any:
            self.sent = messages
            return types.SimpleNamespace(text=" A red square. ")

    model = Model()
    role = LlmRole(
        role="main", provider="openai", model="m", api_key="k", base_url=None,
        temperature=0.0, max_tokens=10, timeout_s=5.0, max_retries=0,
    )  # fmt: skip
    text = await CloudVision(role, 64, model=model).describe(picture(), "describe")
    assert text == "A red square."
    parts = model.sent[0].content
    assert parts[0]["type"] == "text" and parts[1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )

    class Failing:
        async def ainvoke(self, messages: list[Any]) -> Any:
            raise ConnectionError("down")

    with pytest.raises(VisionError):
        await CloudVision(role, 64, model=Failing()).describe(picture(), "describe")
