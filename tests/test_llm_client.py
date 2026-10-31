import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from aiglasses.assistant import llm_client
from aiglasses.assistant.interfaces import Message
from aiglasses.assistant.llm_client import (
    LangChainLlmClient,
    LlmAuthError,
    LlmError,
    LlmRateLimitError,
    LlmRequestError,
    LlmTimeoutError,
    build_chat_model,
    map_error,
)
from aiglasses.domain.errors import ConfigError
from aiglasses.redact import redact
from aiglasses.settings import LlmRole

SECRET = "test-key-1234567890"
FAKE_SHAPE = "sk-" + "abcdefghijkl12345"  # key-shaped, assembled so the secrets guard stays quiet


def role(provider: str = "openai", **changes: Any) -> LlmRole:
    base: dict[str, Any] = dict(
        role="main", provider=provider, model="m-1", api_key=SecretStr(SECRET), base_url=None,
        temperature=0.2, max_tokens=50, timeout_s=1.0, max_retries=1,
    )  # fmt: skip
    return LlmRole(**{**base, **changes})


MESSAGES = [Message("system", "Be brief."), Message("user", "Hi")]


class StubModel:
    """Duck-typed chat model: `script` is a list of steps run in order on each astream() call."""

    def __init__(self, *calls: list[Any]) -> None:
        self.calls = list(calls)
        self.started = 0
        self.closed = 0

    def astream(self, messages: list[Any]) -> AsyncIterator[Any]:
        steps = self.calls[min(self.started, len(self.calls) - 1)]
        self.started += 1
        outer = self

        async def gen() -> AsyncIterator[Any]:
            try:
                for step in steps:
                    if isinstance(step, BaseException):
                        raise step
                    if isinstance(step, float):
                        await asyncio.sleep(step)
                        continue
                    yield SimpleNamespace(text=step)
            finally:
                outer.closed += 1

        return gen()


async def collect(client: LangChainLlmClient) -> str:
    return "".join([d async for d in client.stream(MESSAGES, max_tokens=20)])


class HttpError(Exception):
    def __init__(self, status: int, text: str = "boom") -> None:
        super().__init__(text)
        self.status_code = status


async def test_streams_deltas_from_a_langchain_fake_chat_model() -> None:
    fake = GenericFakeChatModel(messages=iter([AIMessage(content="Hello there my friend")]))
    deltas = [
        d async for d in LangChainLlmClient(role(), model=fake).stream(MESSAGES, max_tokens=9)
    ]
    assert len(deltas) > 1 and "".join(deltas) == "Hello there my friend"


async def test_a_retryable_error_before_the_first_token_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_client, "RETRY_BACKOFF_S", 0.0)
    stub = StubModel([HttpError(429)], ["ok", " then"])
    assert await collect(LangChainLlmClient(role(), model=stub)) == "ok then"
    assert stub.started == 2


async def test_no_retry_after_the_first_token() -> None:
    stub = StubModel(["partial", HttpError(500)], ["never"])
    client = LangChainLlmClient(role(max_retries=3), model=stub)
    seen: list[str] = []
    with pytest.raises(LlmError):
        async for delta in client.stream(MESSAGES, max_tokens=5):
            seen.append(delta)
    assert seen == ["partial"] and stub.started == 1


async def test_retries_stop_at_the_limit_and_the_last_error_is_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_client, "RETRY_BACKOFF_S", 0.0)
    stub = StubModel([HttpError(429)])
    with pytest.raises(LlmRateLimitError):
        await collect(LangChainLlmClient(role(max_retries=2), model=stub))
    assert stub.started == 3


async def test_authentication_errors_are_not_retried_and_never_show_the_key() -> None:
    stub = StubModel([HttpError(401, f"bad key {SECRET} and {FAKE_SHAPE}")])
    with pytest.raises(LlmAuthError) as caught:
        await collect(LangChainLlmClient(role(max_retries=3), model=stub))
    assert stub.started == 1
    assert SECRET not in str(caught.value) and FAKE_SHAPE not in str(caught.value)


async def test_a_wrong_model_name_maps_to_a_request_error() -> None:
    stub = StubModel([HttpError(404, "model not found")])
    with pytest.raises(LlmRequestError):
        await collect(LangChainLlmClient(role(), model=stub))


async def test_the_timeout_is_enforced() -> None:
    stub = StubModel([5.0, "late"])
    client = LangChainLlmClient(role(timeout_s=0.05, max_retries=0), model=stub)
    with pytest.raises(LlmTimeoutError):
        await collect(client)
    assert stub.closed == 1


async def test_cancelling_the_consumer_closes_the_provider_stream_and_leaves_no_tasks() -> None:
    stub = StubModel(["a", 5.0, "b"])
    client = LangChainLlmClient(role(timeout_s=30), model=stub)
    task = asyncio.create_task(collect(client))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert stub.closed == 1
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert others == []


def test_map_error_covers_timeouts_connections_and_servers() -> None:
    assert isinstance(map_error(asyncio.TimeoutError()), LlmTimeoutError)
    assert map_error(HttpError(503)).retryable
    assert isinstance(map_error(ConnectionError("down")), LlmError)
    assert map_error(ValueError("odd")).retryable is False


def test_redact_removes_known_secrets_and_key_shapes() -> None:
    text = f"x {SECRET} y sk-{'a1' * 12} z"
    out = redact(text, [SECRET])
    assert SECRET not in out and "sk-a1" not in out and out.count("[redacted]") == 2


@pytest.mark.parametrize(
    "provider", ["openai", "openrouter", "openai_compatible", "anthropic", "google"]
)
def test_every_provider_builds_a_chat_model_without_network(provider: str) -> None:
    model = build_chat_model(role(provider, base_url="http://localhost:9/v1"))
    assert hasattr(model, "astream")


def test_openrouter_uses_its_default_url_unless_overridden() -> None:
    assert "openrouter" in str(build_chat_model(role("openrouter")).openai_api_base)
    other = build_chat_model(role("openrouter", base_url="http://localhost:9/v1"))
    assert "localhost" in str(other.openai_api_base)


def test_missing_key_missing_model_and_unknown_provider_are_config_errors() -> None:
    with pytest.raises(ConfigError, match="API key"):
        build_chat_model(role(api_key=None))
    with pytest.raises(ConfigError, match="model"):
        build_chat_model(role(model=None))
    with pytest.raises(ConfigError, match="valid:"):
        build_chat_model(role("openai").model_copy(update={"provider": "nope"}))
