"""Client for the local llama.cpp server: ONE constrained call per step, forced JSON.

This is a small httpx client, not LangChain: llama-server's `json_schema` response format and
`cache_prompt` are server features that the LangChain wrappers do not expose cleanly (ADR 0010).
It talks to 127.0.0.1 only and is the only place that knows the local server's request shape.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from aiglasses.assistant.interfaces import Message
from aiglasses.domain.errors import GlassesError

ROUTE = "/v1/chat/completions"
SCHEMA_NAME = "step"
REASON_MAX_CHARS = 80

HEALTH_TIMEOUT_S = 3.0  # a busy CPU can answer /health slowly; 1 s made a loaded server look dead


class LocalUnavailable(GlassesError):
    """Server down, unreachable, or no first token in time: the caller escalates to the cloud."""


class InvalidStep(GlassesError):
    """The model's output was not a valid step even though decoding was constrained."""


class Step(BaseModel):
    action: Literal["answer", "tool", "escalate"]
    text: str = ""
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class StepTiming:
    first_token_s: float
    total_s: float


def step_schema(tools: list[str]) -> dict[str, Any]:
    """JSON schema forcing the five step fields; `tool` may only be one of `tools`."""
    properties: dict[str, Any] = {
        "action": {"type": "string", "enum": ["answer", "tool", "escalate"]},
        "text": {"type": "string"},
        "reason": {"type": "string", "maxLength": REASON_MAX_CHARS},
    }
    if tools:
        properties["tool"] = {"type": "string", "enum": tools}
        properties["args"] = {"type": "object"}
    return {
        "type": "object",
        "properties": properties,
        "required": ["action"],
        "additionalProperties": False,
    }


class LocalLlm:
    def __init__(
        self,
        base_url: str,
        *,
        first_token_timeout_s: float,
        prompt_cache: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + ROUTE
        self._health = base_url.rstrip("/") + "/health"
        self._first_token_timeout = first_token_timeout_s
        self._cache = prompt_cache
        self._client = client or httpx.AsyncClient()
        self.last_error = ""

    async def available(self) -> bool:
        try:
            reply = await self._client.get(self._health, timeout=HEALTH_TIMEOUT_S)
        except httpx.HTTPError as exc:
            self.last_error = type(exc).__name__
            return False
        self.last_error = "" if reply.status_code == 200 else f"HTTP {reply.status_code}"
        return reply.status_code == 200

    def _body(self, messages: list[Message], tools: list[str], max_tokens: int) -> dict[str, Any]:
        return {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": step_schema(tools)},
            },
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "stream": True,
            "cache_prompt": self._cache,
        }

    async def step(
        self, messages: list[Message], tools: list[str], *, max_tokens: int, time_left_s: float
    ) -> tuple[Step, StepTiming]:
        """One constrained call. Raises LocalUnavailable or InvalidStep."""
        started = time.perf_counter()
        pieces: list[str] = []
        first: list[float] = []
        try:
            await asyncio.wait_for(
                self._read(messages, tools, max_tokens, pieces, first, started), time_left_s
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            raise LocalUnavailable(type(exc).__name__) from exc
        total = time.perf_counter() - started
        try:
            step = Step.model_validate(json.loads("".join(pieces)))
        except (ValueError, ValidationError) as exc:
            raise InvalidStep("output was not a valid step") from exc
        return step, StepTiming(first[0] if first else total, total)

    async def _read(
        self,
        messages: list[Message],
        tools: list[str],
        max_tokens: int,
        pieces: list[str],
        first: list[float],
        started: float,
    ) -> None:
        body = self._body(messages, tools, max_tokens)
        async with self._client.stream("POST", self._url, json=body) as reply:
            if reply.status_code != 200:
                raise LocalUnavailable(f"HTTP {reply.status_code}")
            lines = reply.aiter_lines().__aiter__()
            while True:
                limit = self._first_token_timeout if not pieces else None
                try:
                    line = await asyncio.wait_for(lines.__anext__(), limit)
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError as exc:
                    raise LocalUnavailable("no first token in time") from exc
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                delta = json.loads(line[5:])["choices"][0].get("delta", {}).get("content")
                if delta:
                    if not first:
                        first.append(time.perf_counter() - started)
                    pieces.append(delta)
