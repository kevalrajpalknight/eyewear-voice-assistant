import asyncio
import time
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel

from aiglasses.assistant.app import AssistantApp
from aiglasses.assistant.conversation import AssistantTurnRecord, ConversationPipeline
from aiglasses.assistant.escalation import EscalationRules
from aiglasses.assistant.fakes import (
    FakeCloudLlm,
    FakeLocalLlm,
    FakeSink,
    FakeStt,
    FakeTts,
)
from aiglasses.assistant.intents import Intents
from aiglasses.assistant.interfaces import ToolCall, TtsOptions
from aiglasses.assistant.lanes import LocalFirstClient
from aiglasses.assistant.local_llm import InvalidStep, LocalUnavailable, Step
from aiglasses.assistant.memory import Exchange, Memory
from aiglasses.assistant.router import LocalState
from aiglasses.assistant.search import FakeSearch, SearchHit, SearchWeb
from aiglasses.assistant.tools.base import NoArgs, Risk, ToolContext, ToolRegistry, ToolResult
from aiglasses.assistant.tools.device import ReadBattery, TakePhoto
from aiglasses.assistant.tools.memory_tools import ForgetAllThat
from aiglasses.domain.models import BatteryStatus, MediaCounts
from aiglasses.services.control import PhotoResult
from aiglasses.services.voice import EndReason, VoiceUtterance
from aiglasses.settings import AssistantSettings

NOW = datetime(2026, 10, 31, 16, 35, tzinfo=ZoneInfo("Asia/Kolkata"))
ALL_TOOLS = "read_battery,forget_all_that,search_web,take_photo,silent"


class Silent:
    """A read tool that returns data but nothing to say, so the model must read the result."""

    name: ClassVar[str] = "silent"
    description: ClassVar[str] = "Returns data."
    risk: ClassVar[Risk] = Risk.READ
    args_model: ClassVar[type[BaseModel]] = NoArgs
    announcement: ClassVar[str | None] = None

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        return ToolResult("some data")


class Device:
    async def read_battery(self) -> BatteryStatus:
        return BatteryStatus(percent=61, charging=False)

    async def read_media_counts(self) -> MediaCounts:
        return MediaCounts(1, 1, 1)

    async def take_photo(self, *, confirm: bool) -> PhotoResult:
        return PhotoResult(MediaCounts(1, 1, 1), MediaCounts(2, 1, 1), "ok", "00")


def utterance() -> VoiceUtterance:
    now = time.time()
    pcm = np.full(32000, 3000, dtype=np.int16)
    pcm[16000:] = 0
    return VoiceUtterance(pcm, 16000, now - 2, now, 100, EndReason.END_MARKER, None, now)


class Rig:
    def __init__(
        self,
        script: list[Step | Exception],
        *,
        said: str | list[str] = "tell me a joke",
        cloud: FakeCloudLlm | None = None,
        search: FakeSearch | None = None,
        local_up: bool = True,
        no_local: bool = False,
        clock: list[float] | None = None,
        **values: Any,
    ) -> None:
        self.settings = AssistantSettings(
            _env_file=None, llm_model="m", llm_api_key="k", router_mode="local_first",
            tools_enabled=values.pop("tools_enabled", ALL_TOOLS), **values,
        )  # fmt: skip
        self.clock = clock if clock is not None else [0.0]
        self.memory = Memory(
            self.settings.memory_max_turns, self.settings.memory_ttl_s,
            self.settings.memory_max_tokens, lambda: self.clock[0],
        )  # fmt: skip
        self.local = None if no_local else FakeLocalLlm(script, up=local_up)
        self.cloud = cloud or FakeCloudLlm([("Cloud answer here.",)])
        self.search = search or FakeSearch([SearchHit("Rain", "Heavy rain expected tonight.")])
        intents = Intents.load()
        self.registry = ToolRegistry(
            [
                ReadBattery(Device()),
                TakePhoto(Device(), min_interval_s=0, max_per_session=5),
                ForgetAllThat(self.memory),
                SearchWeb(self.search, self.settings),
                Silent(),
            ],
            intents=intents,
        )
        self.client = LocalFirstClient(
            self.settings, local=self.local, cloud=self.cloud, registry=self.registry,
            intents=intents, rules=EscalationRules.load(), memory=self.memory,
            state=LocalState(timezone="Asia/Kolkata", clock=lambda _tz: NOW),
            clock=lambda: self.clock[0],
        )  # fmt: skip
        self.stt = FakeStt(said)
        self.tts, self.sink = FakeTts(), FakeSink()
        self.records: list[AssistantTurnRecord] = []
        self.pipeline = ConversationPipeline(
            stt=self.stt, tts=self.tts, sink=self.sink, llm=self.client,
            options=TtsOptions(voice="v", speed=1.0), settings=self.settings, persona="PERSONA",
            on_record=self.records.append, clock=lambda _tz: NOW,
        )  # fmt: skip
        self.pipeline.history = self.memory
        self.app = AssistantApp(self.pipeline, self.settings)

    async def turn(self, n: int = 1) -> AssistantTurnRecord:
        return await self.pipeline.run_turn(utterance(), n)

    @property
    def spoken(self) -> str:
        return " ".join(self.tts.spoken)


def answer(text: str) -> Step:
    return Step(action="answer", text=text)


async def test_a_local_answer_never_touches_the_cloud() -> None:
    rig = Rig([answer("Why did the robot cross the road? To recharge.")])
    record = await rig.turn()
    assert "recharge" in rig.spoken and rig.cloud.requests == []
    assert (record.tier, record.route_role, record.action, record.steps) == (
        1,
        "local",
        "answer",
        1,
    )
    assert record.local_first_token_s == 0.05


async def test_the_local_prompt_has_a_stable_prefix_and_the_time_after_the_history() -> None:
    rig = Rig([answer("One."), answer("Two.")], said=["tell me a joke", "what is two plus two"])
    await rig.turn(1)
    await rig.turn(2)
    assert rig.local is not None
    first, second = (call[0] for call in rig.local.calls)
    assert first[0] == second[0]  # the system prefix is identical, so the KV cache is reused
    assert "Current date and time" not in first[0].content
    assert "Current date and time" in second[-1].content and second[-1].role == "user"
    assert "read_battery" in first[0].content and "escalate" in first[0].content


async def test_a_local_tool_call_runs_the_tool_and_speaks_its_result() -> None:
    rig = Rig([Step(action="tool", tool="read_battery")], said="how is the gadget doing")
    record = await rig.turn()
    assert "sixty one percent" in rig.spoken and rig.cloud.requests == []
    assert rig.memory.last.notes == ["read_battery: battery 61 percent"]  # type: ignore[union-attr]
    assert record.route_reason == "tool_done"


async def test_forget_all_that_by_rule_clears_memory_and_stays_out_of_it() -> None:
    rig = Rig([], said=["what is the capital of france", "forget everything"])
    rig.memory.add(Exchange("a", "b"))
    rig.local = rig.client.local = FakeLocalLlm([answer("Paris is the capital of France.")])
    await rig.turn(1)
    assert len(rig.memory) == 2
    record = await rig.turn(2)
    assert len(rig.memory) == 0 and "fresh" in rig.spoken
    assert record.tier == 0 and rig.local.calls[-1][0][0].role == "system"


async def test_forget_by_the_model_works_for_any_phrasing() -> None:
    step = Step(action="tool", tool="forget_all_that", args={"scope": "last_exchange"})
    rig = Rig(
        [answer("It is blue."), step], said=["what colour is the sky", "disregard my last remark"]
    )
    await rig.turn(1)
    assert len(rig.memory) == 1
    await rig.turn(2)
    assert len(rig.memory) == 0 and "forgotten" in rig.spoken


async def test_forget_also_clears_a_pending_turn_join() -> None:
    rig = Rig([answer("Fine.")], said=["forget that"])
    rig.app.joiner._previous = utterance()
    await rig.app.handle(utterance())
    await rig.app.runner.wait()
    assert rig.app.joiner._previous is None


async def test_the_user_repeating_the_question_escalates_to_the_cloud() -> None:
    rig = Rig([answer("It is Paris.")], said=["what is the capital of france please"] * 2)
    await rig.turn(1)
    record = await rig.turn(2)
    assert record.tier == 2 and record.route_reason == "dissatisfied"
    assert rig.settings.escalation_filler.split()[0] in rig.spoken and "Cloud answer" in rig.spoken
    note = rig.cloud.requests[0][0].content
    assert "local assistant passed this" in note and "the same question again" in note


async def test_a_correction_in_the_users_words_escalates_when_there_is_history() -> None:
    rig = Rig(
        [answer("It is Paris.")],
        said=["what is the capital of france", "no I meant the old capital"],
    )
    await rig.turn(1)
    record = await rig.turn(2)
    assert record.route_reason == "dissatisfied" and record.tier == 2
    assert len(rig.local.calls) == 1  # type: ignore[union-attr]  # the local model was not asked again


async def test_a_correction_with_no_history_is_just_a_question() -> None:
    rig = Rig([answer("Okay.")], said="no I meant tea")
    record = await rig.turn()
    assert record.tier == 1


async def test_when_the_local_model_is_not_sure_it_escalates_instead_of_guessing() -> None:
    rig = Rig([Step(action="escalate", reason="I am not sure about this")], said="who painted it")
    record = await rig.turn()
    assert record.tier == 2 and record.route_reason == "unsure" and "Cloud answer" in rig.spoken


async def test_a_time_sensitive_question_without_search_goes_to_the_cloud() -> None:
    rig = Rig([], said="who won the match today", tools_enabled="read_battery,forget_all_that")
    record = await rig.turn()
    assert record.route_reason == "current_facts" and record.tier == 2 and rig.local.calls == []  # type: ignore[union-attr]


async def test_a_time_sensitive_question_uses_search_then_answers_locally() -> None:
    rig = Rig([answer("Heavy rain is expected tonight.")], said="what is the weather tonight")
    record = await rig.turn()
    assert rig.search.queries == ["what is the weather tonight"] and "rain" in rig.spoken.lower()
    assert record.tier == 1 and rig.cloud.requests == []
    assert any("Tool result: search results" in m.content for m in rig.local.calls[0][0])  # type: ignore[union-attr]


async def test_search_failure_gets_a_short_spoken_line_and_never_crashes() -> None:
    rig = Rig([], said="what is the weather tonight", search=FakeSearch(error="rate_limit"))
    record = await rig.turn()
    assert "busy" in rig.spoken and record.outcome == "spoken" and rig.cloud.requests == []
    rig2 = Rig([], said="what is the weather tonight", search=FakeSearch(error="blocked"))
    await rig2.turn()
    assert "can't search" in rig2.spoken


async def test_a_local_answer_to_a_current_facts_question_without_a_search_is_not_spoken() -> None:
    rig = Rig([Step(action="answer", text="It will rain.")], said="is it raining right now",
              tools_enabled="read_battery,search_web,forget_all_that")  # fmt: skip
    rig.client.rules = EscalationRules.load()
    record = await rig.turn()
    assert record.tier in (1, 2)
    assert "It will rain" not in rig.spoken or rig.search.queries  # never an unaided guess


async def test_research_requests_skip_the_local_model() -> None:
    rig = Rig([], said="do some deep research on solar panels")
    record = await rig.turn()
    assert record.route_reason == "research" and record.tier == 2 and rig.local.calls == []  # type: ignore[union-attr]


async def test_the_step_cap_escalates() -> None:
    steps: list[Step | Exception] = []
    rig = Rig(
        steps, said="check the silent thing for me", agent_max_steps=2, tool_max_calls_per_turn=5
    )
    rig.local = rig.client.local = FakeLocalLlm([Step(action="tool", tool="silent")] * 5)
    record = await rig.turn()
    assert record.route_reason == "step_cap" and record.tier == 2 and len(rig.local.calls) == 2


async def test_two_malformed_replies_escalate() -> None:
    rig = Rig([InvalidStep("bad"), InvalidStep("bad")])
    record = await rig.turn()
    assert record.route_reason == "invalid_json" and record.tier == 2


async def test_one_malformed_reply_is_retried() -> None:
    rig = Rig([InvalidStep("bad"), answer("Hello there.")], said="hello")
    record = await rig.turn()
    assert record.tier == 1 and "Hello there" in rig.spoken


async def test_the_time_budget_escalates() -> None:
    clock = [0.0]
    rig = Rig([answer("late")], clock=clock, agent_time_budget_s=8)
    original = rig.local.step  # type: ignore[union-attr]

    async def slow_step(*args: Any, **kwargs: Any) -> Any:
        clock[0] += 9
        return await original(*args, **kwargs)

    rig.local.step = slow_step  # type: ignore[method-assign,union-attr]
    rig.local._script.insert(0, Step(action="tool", tool="silent"))  # type: ignore[union-attr]
    record = await rig.turn()
    assert record.route_reason == "time_budget" and record.tier == 2


async def test_a_server_that_is_down_or_slow_falls_back_to_the_cloud() -> None:
    down = Rig([], local_up=False)
    assert (await down.turn()).route_reason == "local_down"
    missing = Rig([], no_local=True)
    assert (await missing.turn()).route_reason == "local_down"
    slow = Rig([LocalUnavailable("no first token in time")])
    record = await slow.turn()
    assert record.route_reason == "local_slow" and "Cloud answer" in slow.spoken
    dead = Rig([LocalUnavailable("ConnectError")])
    assert (await dead.turn()).route_reason == "local_down"


async def test_memory_entries_expire_with_the_ttl_and_the_next_turn_starts_clean() -> None:
    rig = Rig(
        [answer("One."), answer("Two.")],
        said=["tell me a joke", "tell me another one"],
        memory_ttl_s=60,
    )
    await rig.turn(1)
    rig.clock[0] += 61
    await rig.turn(2)
    history = [m for m in rig.local.calls[1][0][1:-1]]  # type: ignore[union-attr]
    assert history == []


async def test_the_cloud_lane_can_call_tools_and_continues_with_the_result() -> None:
    call = ToolCall("c1", "read_battery", {})
    cloud = FakeCloudLlm([["", call], ["Your battery is fine."]])
    rig = Rig(
        [Step(action="escalate", reason="research needed")],
        said="think hard about my gear",
        cloud=cloud,
    )
    record = await rig.turn()
    assert "battery is fine" in rig.spoken and record.tier == 2
    second = cloud.requests[1]
    assert second[-1].role == "tool" and "61 percent" in second[-1].content
    assert second[-2].tool_calls[0].name == "read_battery"
    assert any(t["tool"] == "read_battery" for t in cloud.tool_lists[0])


async def test_web_content_taints_the_turn_and_blocks_a_photo_the_model_asks_for() -> None:
    cloud = FakeCloudLlm(
        [
            ["", ToolCall("a", "search_web", {"query": "x"})],
            ["", ToolCall("b", "take_photo", {})],
            ["Done."],
        ]
    )
    rig = Rig([], said="research cameras then take a photo", cloud=cloud)
    await rig.turn()
    outcomes = [m.content for m in cloud.requests[-1] if m.role == "tool"]
    assert any("search results" in o for o in outcomes)
    assert any("untrusted" in o for o in outcomes)  # the photo was refused after the web content


async def test_a_new_turn_cancelling_the_previous_one_leaves_no_tasks_behind() -> None:
    rig = Rig([answer("Hello."), answer("Again.")], said=["hello", "hello again friend"])
    await rig.app.handle(utterance())
    await rig.app.handle(utterance())
    await rig.app.runner.wait()
    await rig.app.close()
    assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []
