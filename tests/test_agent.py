import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from aiglasses.assistant.agent import AgentClient
from aiglasses.assistant.escalation import EscalationRules
from aiglasses.assistant.intents import Intents
from aiglasses.assistant.interfaces import Message
from aiglasses.assistant.memory import Memory
from aiglasses.assistant.tools.base import NoArgs, Risk, ToolContext, ToolRegistry, ToolResult
from aiglasses.assistant.tools.device import ReadBattery, TakePhoto
from aiglasses.assistant.tools.memory_tools import ForgetAllThat
from aiglasses.settings import AssistantSettings
from tests.test_lanes import Device

ALL_TOOLS = "read_battery,describe_scene,forget_all_that,take_photo"


class ScriptedChat(BaseChatModel):
    """A chat model that replays AIMessages (or raises the exceptions in its script)."""

    script: list[Any] = Field(default_factory=list)
    seen: list[list[Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChat":
        return self

    def _next(self, messages: list[Any]) -> AIMessage:
        self.seen.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[no-any-return]

    def _generate(
        self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _astream(
        self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        message = self._next(messages)
        calls = [
            {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
            for i, c in enumerate(message.tool_calls)
        ]
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=message.content, tool_call_chunks=calls)
        )


class Camera:
    name = "describe_scene"
    description = "Look through the glasses camera."
    risk = Risk.NETWORK
    args_model = NoArgs
    announcement = "Let me look."

    async def run(self, args: Any, ctx: ToolContext) -> ToolResult:
        return ToolResult("scene: a pack of oats", tainted=True)


def call(name: str, args: dict[str, Any] | None = None, ident: str = "c1") -> dict[str, Any]:
    return {"name": name, "args": args or {}, "id": ident}


class Rig:
    def __init__(self, local: list[Any], cloud: list[Any] | None = None, **values: Any) -> None:
        self.settings = AssistantSettings(
            _env_file=None, llm_model="m", llm_api_key="k", router_mode="local_first",
            tools_enabled=values.pop("tools_enabled", ALL_TOOLS),
            **values,
        )  # fmt: skip
        self.memory = Memory(10, 600, 1500)
        intents = Intents.load()
        self.registry = ToolRegistry(
            [
                ReadBattery(Device()),
                TakePhoto(Device(), min_interval_s=0, max_per_session=5),
                Camera(),
                ForgetAllThat(self.memory),
            ],
            intents=intents,
        )
        self.local, self.cloud = ScriptedChat(script=local), ScriptedChat(script=cloud or [])
        self.client = AgentClient(
            self.settings, local_model=self.local, cloud_model=self.cloud,
            registry=self.registry, intents=intents, rules=EscalationRules.load(),
            memory=self.memory,
        )  # fmt: skip

    async def say(self, text: str) -> str:
        messages = [Message("system", "You are EVA."), Message("user", text)]
        return "".join([piece async for piece in self.client.stream(messages, max_tokens=200)])


async def test_a_simple_question_is_answered_by_the_local_model_alone() -> None:
    rig = Rig([AIMessage("The capital of France is Paris.")], [])
    spoken = await rig.say("what is the capital of france")
    assert "Paris" in spoken and rig.client.report.lane == 1 and rig.cloud.seen == []


async def test_a_rule_answers_without_any_model() -> None:
    rig = Rig([], [])
    spoken = await rig.say("what time is it")
    assert spoken.strip() and rig.client.report.lane == 0 and rig.local.seen == []


async def test_a_multi_step_request_narrates_looks_and_then_answers() -> None:
    rig = Rig(
        [
            AIMessage("I'll take a look.", tool_calls=[call("describe_scene")]),
            AIMessage("It is a pack of oats."),
        ]
    )
    spoken = await rig.say("help me decide what to cook with these")
    assert spoken.index("take a look") < spoken.index("pack of oats")
    assert any(p in spoken for p in rig.registry.fillers.phrases("describe_scene"))
    assert rig.client.report.tools == [("describe_scene", "ok")]
    second = rig.local.seen[1][-1]
    assert second.type == "tool" and "a pack of oats" in second.content


async def test_a_failing_local_model_is_repeated_on_the_cloud_model() -> None:
    rig = Rig([ConnectionError("down")], [AIMessage("The cloud answered.")])
    spoken = await rig.say("tell me a story about a bird")
    assert "cloud answered" in spoken
    assert rig.client.report.lane == 2 and rig.client.report.category == "local_down"


async def test_think_harder_hands_the_rest_of_the_turn_to_the_cloud() -> None:
    rig = Rig(
        [AIMessage("", tool_calls=[call("think_harder", {"reason": "health advice"})])],
        [AIMessage("Here is a careful answer.")],
    )
    spoken = await rig.say("is this good for my health")
    assert "careful answer" in spoken and rig.client.report.lane == 2
    assert rig.client.report.category == "health advice"


async def test_research_requests_start_on_the_cloud_model() -> None:
    rig = Rig([], [AIMessage("A detailed answer.")])
    await rig.say("research the history of tea in depth")
    assert rig.local.seen == [] and rig.client.report.lane == 2


async def test_the_step_limit_stops_a_looping_model() -> None:
    loop = [AIMessage("", tool_calls=[call("read_battery", ident=f"c{i}")]) for i in range(12)]
    rig = Rig(loop, agent_max_steps=3, tool_max_calls_per_turn=10)
    spoken = await rig.say("keep checking the battery for me please")
    assert len(rig.local.seen) <= 4 and spoken.strip()


async def test_tool_rules_still_apply_inside_the_agent() -> None:
    rig = Rig(
        [
            AIMessage("", tool_calls=[call("take_photo")]),
            AIMessage("Done then."),
        ]
    )
    await rig.say("how are you doing")
    outcome = rig.local.seen[1][-1].content
    assert "Failed" in outcome and "did not ask" in outcome


@pytest.mark.parametrize("name", ["think_harder", "read_battery", "describe_scene"])
def test_the_agent_exposes_the_enabled_tools_and_the_escalation_tool(name: str) -> None:
    rig = Rig([])
    assert name in {t.name for t in rig.client._tools}


@pytest.mark.parametrize(
    "phrase,plan",
    [
        ("can you read the label on this bottle for me", ["describe_scene"]),
        ("look at this packet and tell me if it is healthy", ["describe_scene", "search_web"]),
        ("is this product any good, check reviews", ["describe_scene", "search_web"]),
        ("do I need an umbrella in Mumbai", ["get_weather"]),
        ("what is the gold price in India right now", ["search_web"]),
        ("what is the capital of france", []),
        ("how are you feeling today", []),
    ],
)
def test_the_words_decide_which_tools_the_first_steps_must_call(
    phrase: str, plan: list[str]
) -> None:
    from aiglasses.assistant.agent.hints import Hints

    every = ("describe_scene", "search_web", "get_weather")
    assert Hints.load().plan(phrase, every) == plan
    assert Hints.load().plan(phrase, ("read_battery",)) == []


async def test_the_planned_tool_is_announced_before_the_model_is_asked() -> None:
    rig = Rig(
        [AIMessage("", tool_calls=[call("describe_scene")]), AIMessage("It is a pack of oats.")]
    )
    spoken = await rig.say("help me decide what to cook with these")
    line = next(p for p in rig.registry.fillers.phrases("describe_scene") if p in spoken)
    assert spoken.index(line) < spoken.index("pack of oats") and spoken.count(line) == 1
    assert rig.client.report.tools == [("describe_scene", "ok")]


async def test_narration_is_spoken_at_once_even_when_it_is_a_short_line() -> None:
    from aiglasses.assistant.clauses import FLUSH, ClauseStream

    stream = ClauseStream(6)
    assert stream.feed("Searching now. " + FLUSH) == ["Searching now."]
    assert ClauseStream(6).feed("Searching now. ") == []  # without the marker it waits


async def test_a_bracketed_tool_call_is_not_spoken_and_the_cloud_repeats_the_turn() -> None:
    rig = Rig(
        [AIMessage("[getweather: location=Waliyad] I'm checking the weather for Waliyad now.")],
        [AIMessage("It is warm today.")],
    )
    spoken = await rig.say("what should I wear in Waliyad")
    assert "getweather" not in spoken and "warm today" in spoken
    assert rig.client.report.category == "bad_tool_call" and rig.client.report.lane == 2


def test_a_short_call_that_ends_with_the_name_wakes_it() -> None:
    from aiglasses.assistant.wake import WakePhrases

    wake = WakePhrases.load()
    assert wake.strip("Hi there, Eva.").only_wake  # noqa: B005
    assert wake.strip("hei iwa").only_wake  # noqa: B005
    assert not wake.strip("I told the team to wait for the report today").woke  # noqa: B005
