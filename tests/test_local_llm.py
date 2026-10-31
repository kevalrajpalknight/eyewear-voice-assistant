import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from aiglasses.assistant import local_server
from aiglasses.assistant.interfaces import Message
from aiglasses.assistant.local_llm import InvalidStep, LocalLlm, LocalUnavailable, Step, step_schema
from aiglasses.assistant.models import Registry
from aiglasses.domain.errors import ConfigError
from aiglasses.settings import AssistantSettings

MESSAGES = [Message("system", "rules"), Message("user", "hello")]


def sse(*deltas: str) -> bytes:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}" for d in deltas]
    return ("\n\n".join([*lines, "data: [DONE]"]) + "\n\n").encode()


def llm(handler: object, first_token_timeout_s: float = 1.0, cache: bool = True) -> LocalLlm:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return LocalLlm(
        "http://127.0.0.1:9",
        first_token_timeout_s=first_token_timeout_s,
        prompt_cache=cache,
        client=client,
    )


async def test_a_valid_step_is_parsed_and_the_request_forces_json_and_the_cache() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        seen["url"] = str(request.url)
        return httpx.Response(200, content=sse('{"action": "answer", ', '"text": "Hi there."}'))

    step, timing = await llm(handler).step(MESSAGES, ["read_battery"], max_tokens=60, time_left_s=5)
    assert step == Step(action="answer", text="Hi there.")
    assert timing.first_token_s <= timing.total_s
    assert seen["url"] == "http://127.0.0.1:9/v1/chat/completions" and seen["cache_prompt"] is True
    schema = seen["response_format"]["json_schema"]["schema"]  # type: ignore[index]
    assert schema["properties"]["tool"]["enum"] == ["read_battery"] and seen["temperature"] == 0.0
    assert seen["max_tokens"] == 60 and seen["stream"] is True


async def test_the_prompt_cache_follows_the_setting() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=sse('{"action": "escalate", "reason": "unsure"}'))

    step, _ = await llm(handler, cache=False).step(MESSAGES, [], max_tokens=40, time_left_s=5)
    assert seen["cache_prompt"] is False and step.action == "escalate"


@pytest.mark.parametrize("output", ["not json", '{"action": "dance"}', '{"text": "no action"}', ""])
async def test_invalid_output_raises_invalid_step(output: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(output) if output else sse())

    with pytest.raises(InvalidStep):
        await llm(handler).step(MESSAGES, [], max_tokens=40, time_left_s=5)


async def test_server_errors_and_connection_failures_mean_unavailable() -> None:
    with pytest.raises(LocalUnavailable, match="503"):
        await llm(lambda r: httpx.Response(503)).step(MESSAGES, [], max_tokens=1, time_left_s=5)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(LocalUnavailable):
        await llm(refuse).step(MESSAGES, [], max_tokens=1, time_left_s=5)


class SlowBody(httpx.AsyncByteStream):
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.sleep(self.delay)
        yield sse('{"action": "answer"}')


async def test_a_late_first_token_is_unavailable_so_the_caller_escalates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowBody(2.0))

    with pytest.raises(LocalUnavailable, match="first token"):
        await llm(handler, first_token_timeout_s=0.05).step(
            MESSAGES, [], max_tokens=1, time_left_s=5
        )


async def test_the_time_budget_cuts_a_stuck_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowBody(5.0))

    with pytest.raises(LocalUnavailable):
        await llm(handler, first_token_timeout_s=10).step(
            MESSAGES, [], max_tokens=1, time_left_s=0.05
        )


async def test_cancellation_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=SlowBody(5.0))

    task = asyncio.create_task(
        llm(handler, first_token_timeout_s=10).step(MESSAGES, [], max_tokens=1, time_left_s=30)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_availability_uses_the_health_route() -> None:
    assert await llm(lambda r: httpx.Response(200, json={"status": "ok"})).available()
    assert not await llm(lambda r: httpx.Response(503)).available()


def test_schema_without_tools_has_no_tool_field() -> None:
    assert "tool" not in step_schema([])["properties"]
    assert step_schema(["a"])["required"] == ["action"]


def settings(**values: object) -> AssistantSettings:
    return AssistantSettings(_env_file=None, **values)  # type: ignore[arg-type]


def test_the_server_command_line_comes_from_settings(tmp_path: object) -> None:
    reg = Registry.load()
    name = next(iter(reg.models))
    s = settings(
        local_llm_model=name, local_llm_ctx=2048, local_llm_batch=256, local_llm_threads=3,
        local_llm_prompt_cache=False,
    )  # fmt: skip
    args = local_server.server_args(s, reg, 9100)
    assert (
        args[args.index("--host") + 1] == "127.0.0.1" and args[args.index("--port") + 1] == "9100"
    )
    assert args[args.index("-c") + 1] == "2048" and args[args.index("-b") + 1] == "256"
    assert args[args.index("-t") + 1] == "3" and "--no-cache-prompt" in args
    assert args[args.index("--reasoning") + 1] == "off"
    with pytest.raises(ConfigError, match="LOCAL_LLM_MODEL"):
        local_server.server_args(settings(), reg, 9100)
    with pytest.raises(ConfigError, match="valid"):
        local_server.server_args(settings(local_llm_model="nope"), reg, 9100)


def test_threads_default_to_physical_cores_and_vision_adds_the_projector() -> None:
    reg = Registry.load()
    gemma = next(n for n in reg.models if n.startswith("gemma"))
    args = local_server.server_args(settings(local_llm_model=gemma), reg, 9100, vision=True)
    assert args[args.index("-t") + 1] == str(local_server.physical_cores()) and "--mmproj" in args


def test_port_helpers() -> None:
    import socket

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        assert not local_server.port_is_free(port)
        assert local_server.free_port(port) != port
    assert local_server.port_is_free(port)
