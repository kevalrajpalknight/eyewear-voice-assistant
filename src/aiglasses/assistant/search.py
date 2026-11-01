"""Web search behind a small provider interface.

`ddgs` scrapes public search pages without a key: it is unofficial and can be blocked or rate
limited, so every failure becomes a typed error that the tool turns into a short spoken line.
Results are titles and short snippets only; links never reach speech. Other providers (tavily,
brave) would be one more factory in `SEARCH_PROVIDERS`: design only, not implemented.
"""

import asyncio
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Protocol

from pydantic import BaseModel

from aiglasses.assistant.tools.base import Risk, ToolContext, ToolRefused, ToolResult
from aiglasses.domain.errors import ConfigError, GlassesError
from aiglasses.settings import AssistantSettings

SNIPPET_CHARS = 160
TITLE_CHARS = 80
URL = re.compile(r"(?:https?://|www\.)\S+")


class SearchError(GlassesError):
    """Search failed. `category` is blocked, rate_limit, timeout or empty."""

    def __init__(self, category: str) -> None:
        super().__init__(f"search failed: {category}")
        self.category = category


@dataclass(frozen=True)
class SearchHit:
    title: str
    snippet: str


class SearchProvider(Protocol):
    async def search(
        self, query: str, *, max_results: int, region: str, timeout_s: float
    ) -> list[SearchHit]: ...


def clean_text(text: str, limit: int = SNIPPET_CHARS) -> str:
    text = re.sub(r"\s+", " ", URL.sub("", text)).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


class FakeSearch:
    def __init__(self, hits: list[SearchHit] | None = None, error: str | None = None) -> None:
        self.hits, self.error = hits or [], error
        self.queries: list[str] = []

    async def search(
        self, query: str, *, max_results: int, region: str, timeout_s: float
    ) -> list[SearchHit]:
        self.queries.append(query)
        if self.error:
            raise SearchError(self.error)
        return self.hits[:max_results]


class DdgsSearch:
    async def search(
        self, query: str, *, max_results: int, region: str, timeout_s: float
    ) -> list[SearchHit]:
        from ddgs import DDGS
        from ddgs.exceptions import RatelimitException, TimeoutException

        def run() -> list[dict[str, str]]:
            client = DDGS(timeout=int(timeout_s))
            return list(client.text(query, region=region, max_results=max_results))

        try:
            raw = await asyncio.wait_for(asyncio.to_thread(run), timeout_s + 1)
        except (TimeoutException, asyncio.TimeoutError) as exc:
            raise SearchError("timeout") from exc
        except RatelimitException as exc:
            raise SearchError("rate_limit") from exc
        except Exception as exc:  # a scraper can fail in many ways; never crash the turn
            raise SearchError("blocked") from exc
        hits = [
            SearchHit(clean_text(r.get("title", ""), TITLE_CHARS), clean_text(r.get("body", "")))
            for r in raw
        ]
        if not hits:
            raise SearchError("empty")
        return hits


class CachedSearch:
    """Repeat queries within the TTL reuse the earlier answer; failures are not cached."""

    def __init__(
        self, inner: SearchProvider, ttl_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._inner, self._ttl, self._clock = inner, ttl_s, clock
        self._cache: dict[tuple[str, int, str], tuple[float, list[SearchHit]]] = {}

    async def search(
        self, query: str, *, max_results: int, region: str, timeout_s: float
    ) -> list[SearchHit]:
        key = (query.strip().lower(), max_results, region)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] <= self._ttl:
            return cached[1]
        hits = await self._inner.search(
            query, max_results=max_results, region=region, timeout_s=timeout_s
        )
        self._cache[key] = (now, hits)
        return hits


SEARCH_PROVIDERS: dict[str, Callable[[], SearchProvider]] = {"ddgs": DdgsSearch, "fake": FakeSearch}


def build_search(settings: AssistantSettings) -> SearchProvider:
    factory = SEARCH_PROVIDERS.get(settings.search_provider)
    if factory is None:
        valid = ", ".join(sorted(SEARCH_PROVIDERS))
        raise ConfigError(f"unknown SEARCH_PROVIDER {settings.search_provider!r}; valid: {valid}")
    return CachedSearch(factory(), settings.search_cache_ttl_s)


SPOKEN_FAILURE = {
    "rate_limit": "Search is busy right now, try again in a minute.",
    "timeout": "The search took too long.",
    "empty": "I found nothing for that.",
}
SPOKEN_FALLBACK = "I can't search right now."


class SearchArgs(BaseModel):
    query: str


class SearchWeb:
    name: ClassVar[str] = "search_web"
    description: ClassVar[str] = "Search the web for current facts. Returns titles and snippets."
    risk: ClassVar[Risk] = Risk.NETWORK
    args_model: ClassVar[type[BaseModel]] = SearchArgs
    announcement: ClassVar[str | None] = "Let me look that up."

    def __init__(self, provider: SearchProvider, settings: AssistantSettings) -> None:
        self._provider = provider
        self._settings = settings

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        assert isinstance(args, SearchArgs)
        try:
            hits = await self._provider.search(
                args.query,
                max_results=self._settings.search_max_results,
                region=self._settings.search_region,
                timeout_s=self._settings.search_timeout_s,
            )
        except SearchError as exc:
            spoken = SPOKEN_FAILURE.get(exc.category, SPOKEN_FALLBACK)
            raise ToolRefused(f"search {exc.category}", spoken) from exc
        lines = [f"{i}. {h.title}: {h.snippet}" for i, h in enumerate(hits, start=1)]
        text = " | ".join(lines)[: self._settings.search_context_chars]
        return ToolResult("search results: " + text, tainted=True)
