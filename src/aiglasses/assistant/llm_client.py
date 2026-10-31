"""The only module that imports LangChain. Everything else talks to the `LlmClient` protocol.

Adding a provider means one factory function and one entry in `PROVIDER_FACTORIES`. Model names
are never written here: they come from settings. Provider base URLs that are fixed facts of a
service (not tuning) live in `PROVIDER_BASE_URLS` and LLM_BASE_URL overrides them.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from aiglasses.assistant.interfaces import Message
from aiglasses.domain.errors import ConfigError, GlassesError
from aiglasses.redact import redact
from aiglasses.settings import LlmRole

PROVIDER_BASE_URLS = {"openrouter": "https://openrouter.ai/api/v1"}
PROVIDER_PACKAGES = {
    "openai": "langchain_openai",
    "openrouter": "langchain_openai",
    "openai_compatible": "langchain_openai",
    "anthropic": "langchain_anthropic",
    "google": "langchain_google_genai",
}
RETRY_BACKOFF_S = 0.5
MAX_ERROR_CHARS = 200


class LlmError(GlassesError):
    """Base class for LLM failures. Messages never contain a key."""

    retryable = False


class LlmAuthError(LlmError):
    """The provider rejected the key or the account (HTTP 401 or 403)."""


class LlmRequestError(LlmError):
    """The provider rejected the request, usually a wrong model name (HTTP 400 or 404)."""


class LlmRateLimitError(LlmError):
    retryable = True


class LlmConnectionError(LlmError):
    retryable = True


class LlmServerError(LlmError):
    retryable = True


class LlmTimeoutError(LlmError):
    retryable = True


def map_error(exc: BaseException, secrets: Sequence[str] = ()) -> LlmError:
    """Turn a provider or network exception into one of our typed errors."""
    if isinstance(exc, LlmError):
        return exc
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    status = status if isinstance(status, int) else None
    kind = type(exc).__name__.lower()
    detail = redact(f"{type(exc).__name__}: {exc}", secrets)[:MAX_ERROR_CHARS]
    if isinstance(exc, asyncio.TimeoutError | TimeoutError) or "timeout" in kind:
        return LlmTimeoutError(detail)
    if status in (401, 403) or "authentication" in kind or "permissiondenied" in kind:
        return LlmAuthError(detail)
    if status == 429 or "ratelimit" in kind:
        return LlmRateLimitError(detail)
    if status is not None and status >= 500:
        return LlmServerError(detail)
    if status in (400, 404, 422) or "badrequest" in kind or "notfound" in kind:
        return LlmRequestError(detail)
    if "connection" in kind or isinstance(exc, OSError):
        return LlmConnectionError(detail)
    return LlmError(detail)


def _require_key(role: LlmRole) -> str:
    if role.api_key is None:
        raise ConfigError(f"no API key for the {role.role} LLM (set LLM_API_KEY)")
    return role.api_key.get_secret_value()


def _require_model(role: LlmRole) -> str:
    if role.model is None:
        raise ConfigError(f"no model for the {role.role} LLM (set LLM_MODEL)")
    return role.model


def _effort(role: LlmRole) -> dict[str, str]:
    """Reasoning effort goes to the provider only when the user set it."""
    return {"reasoning_effort": role.reasoning_effort} if role.reasoning_effort else {}


def _openai_like(role: LlmRole) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=_require_model(role), api_key=_require_key(role),
        base_url=role.base_url or PROVIDER_BASE_URLS.get(role.provider),
        temperature=role.temperature, max_completion_tokens=role.max_tokens,
        timeout=role.timeout_s, max_retries=0, **_effort(role),
    )  # fmt: skip


def _anthropic(role: LlmRole) -> Any:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=_require_model(role), api_key=_require_key(role), base_url=role.base_url,
        temperature=role.temperature, max_tokens=role.max_tokens,
        timeout=role.timeout_s, max_retries=0, **_effort(role),
    )  # fmt: skip


def _google(role: LlmRole) -> Any:
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=_require_model(role), api_key=_require_key(role), base_url=role.base_url,
        temperature=role.temperature, max_tokens=role.max_tokens,
        timeout=role.timeout_s, retries=0, **_effort(role),
    )  # fmt: skip


PROVIDER_FACTORIES: dict[str, Callable[[LlmRole], Any]] = {
    "openai": _openai_like,
    "openrouter": _openai_like,
    "openai_compatible": _openai_like,
    "anthropic": _anthropic,
    "google": _google,
}


def build_chat_model(role: LlmRole) -> Any:
    factory = PROVIDER_FACTORIES.get(role.provider)
    if factory is None:
        valid = ", ".join(sorted(PROVIDER_FACTORIES))
        raise ConfigError(f"unknown LLM provider {role.provider!r}; valid: {valid}")
    return factory(role)


def _to_langchain(messages: list[Message]) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    kinds = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
    return [kinds[m.role](content=m.content) for m in messages]


class LangChainLlmClient:
    """Streams text deltas. Cancelling the consuming task closes the provider stream."""

    def __init__(self, role: LlmRole, *, model: Any = None) -> None:
        self.role = role
        self.name = f"{role.role}:{role.provider}"
        self._injected = model
        self._secrets = [role.api_key.get_secret_value()] if role.api_key else []

    def _model(self, max_tokens: int) -> Any:
        if self._injected is not None:
            return self._injected
        return build_chat_model(self.role.model_copy(update={"max_tokens": max_tokens}))

    async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
        model = self._model(max_tokens)
        prepared = _to_langchain(messages)
        loop = asyncio.get_running_loop()
        attempt = 0
        while True:
            started = False
            deadline = loop.time() + self.role.timeout_s
            iterator = model.astream(prepared).__aiter__()
            try:
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise LlmTimeoutError(f"no complete reply within {self.role.timeout_s} s")
                    try:
                        chunk = await asyncio.wait_for(iterator.__anext__(), remaining)
                    except StopAsyncIteration:
                        return
                    text = str(chunk.text)
                    if text:
                        started = True
                        yield text
            except asyncio.CancelledError:
                raise
            except GeneratorExit:
                raise
            except Exception as exc:
                error = map_error(exc, self._secrets)
                if started or not error.retryable or attempt >= self.role.max_retries:
                    raise error from None
                attempt += 1
                await asyncio.sleep(RETRY_BACKOFF_S * attempt)
            finally:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await asyncio.shield(_quiet(close()))


async def _quiet(awaitable: Any) -> None:
    with contextlib.suppress(Exception):  # closing a half-read stream can fail harmlessly
        await awaitable
