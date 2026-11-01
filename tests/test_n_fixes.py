from typing import Any

import pytest

from aiglasses.assistant.clauses import ClauseStream
from aiglasses.assistant.diagnose import diagnose
from aiglasses.assistant.fakes import FakeCloudLlm, FakeLlm, FakeTts
from aiglasses.assistant.intents import Intents, normalise
from aiglasses.assistant.speech_chunks import split_speech
from aiglasses.settings import AssistantSettings
from tests.test_lanes import Rig, answer, utterance


def settings(**values: Any) -> AssistantSettings:
    return AssistantSettings(_env_file=None, llm_model="m", llm_api_key="k", **values)


# --- chunking ----------------------------------------------------------------------------------


def test_the_first_chunk_is_the_first_sentence_or_a_comma_after_the_minimum() -> None:
    text = (
        "Sure, I can help. The capital of France is Paris, a city on the river Seine in the north."
    )
    assert split_speech(text, 8)[0] == "Sure, I can help."
    long_first = (
        "Well, if you really want the full story of how this happened, it goes back a long way."
    )
    assert (
        split_speech(long_first, 8)[0]
        == "Well, if you really want the full story of how this happened,"
    )
    assert split_speech("Hi there. How are you doing today? Fine.", 8) == [
        "Hi there. How are you doing today?",
        "Fine.",
    ]


def test_short_sentences_merge_forward_and_nothing_is_lost() -> None:
    text = "Yes. Okay. The weather is nice today. Bye."
    chunks = split_speech(text, 8)
    assert " ".join(chunks) == text and chunks[0].startswith("Yes. Okay. The weather")


def test_a_very_long_sentence_is_split_at_a_comma_into_whole_pieces() -> None:
    words = ["alpha"] * 12
    text = " ".join(words) + ", " + " ".join(words) + ", " + " ".join(words) + "."
    chunks = split_speech(text, 20)
    assert len(chunks) >= 2 and " ".join(chunks) == text.replace(", ", ", ")


def test_a_finished_sentence_is_spoken_at_once_and_nothing_is_lost_at_the_end() -> None:
    stream = ClauseStream(8)
    assert stream.feed("One moment, let me think about that. ") == [
        "One moment, let me think about that."
    ]
    assert stream.feed("Cloud answer here.") == []  # a short sentence waits for what follows
    assert stream.finish() == ["Cloud answer here."]


def test_finish_can_drop_an_unfinished_tail() -> None:
    stream = ClauseStream(8)
    first = stream.feed("This is a complete sentence for you. And then it")
    assert first == ["This is a complete sentence for you."]
    assert stream.finish(drop_unfinished=True) == []
    keep = ClauseStream(8)
    keep.feed("This is a complete sentence for you. And then it")
    assert keep.finish() == ["And then it"]


# --- replies that finish naturally ------------------------------------------------------------


class LengthLlm(FakeLlm):
    last_finish_reason = "length"


async def test_a_provider_that_ran_out_of_tokens_never_speaks_a_half_sentence() -> None:
    from tests.test_conversation import Rig as ConvRig

    rig = ConvRig(
        router=False,
        llm=LengthLlm(
            [("The first sentence is complete and fine. The second is cut off in the mi",)]
        ),
    )
    rig.pipeline.llm = rig.llm
    record = await rig.pipeline.run_turn(utterance(), 1)
    spoken = rig.tts.spoken
    assert spoken[0].endswith("complete and fine.") and spoken[-1] == rig.settings.wrap_up_line
    assert not any("cut off" in s for s in spoken) and record.truncation == "provider_length"


async def test_the_time_soft_cap_waits_for_a_sentence_end_and_is_logged() -> None:
    from tests.test_conversation import Rig as ConvRig

    long_reply = tuple(f"This sentence number {i} has enough words in it. " for i in range(9))
    rig = ConvRig(
        router=False, tts=FakeTts(chunk_seconds=1.0), max_reply_seconds=2.5,
        reply_soft_max_sentences=20, llm=FakeLlm([long_reply]),
    )  # fmt: skip
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.truncation == "time_soft_cap" and rig.tts.spoken[-2].endswith(".")
    assert rig.tts.spoken[-1] == rig.settings.wrap_up_line


async def test_a_barge_in_is_recorded_as_the_cause() -> None:
    import asyncio

    rig = Rig([answer("One sentence here. " * 3)], said="tell me a long story")
    rig.tts = FakeTts(delay=0.5)
    rig.pipeline.tts = rig.tts
    task = asyncio.create_task(rig.pipeline.run_turn(utterance(), 1))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rig.records[-1].truncation == "barge_in"


# --- search -----------------------------------------------------------------------------------


async def test_an_explicit_search_request_searches_even_when_the_local_model_is_down() -> None:
    cloud = FakeCloudLlm([("Here is what I found.",)])
    rig = Rig([], said="search for the best pizza in Mumbai", cloud=cloud, local_up=False)
    record = await rig.turn()
    assert (
        rig.search.queries == ["the best pizza in mumbai"] and record.route_reason == "local_down"
    )
    note = cloud.requests[0][0].content
    assert "Web results already fetched" in note and "Heavy rain expected" in note
    assert "Here is what I found" in rig.spoken


async def test_search_results_survive_a_slow_local_model() -> None:
    from aiglasses.assistant.local_llm import LocalUnavailable

    cloud = FakeCloudLlm([("It will rain tonight.",)])
    rig = Rig(
        [LocalUnavailable("no first token in time")], said="what is the news today", cloud=cloud
    )
    record = await rig.turn()
    assert rig.search.queries and record.route_reason == "local_slow"
    assert "Web results already fetched" in cloud.requests[0][0].content


# --- camera and identify intents --------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "tool", "args"),
    [
        ("Open the camera", "take_photo", {"intro": True}),
        ("please open my camera", "take_photo", {"intro": True}),
        ("snap", "take_photo", {}),
        ("capture", "take_photo", {}),
        ("take a picture", "take_photo", {}),
        ("identify this object", "describe_scene", {}),
        ("what is this", "describe_scene", {}),
        ("what's that", "describe_scene", {}),
        ("search for solar panel prices", "search_web", {"query": "solar panel prices"}),
        ("look up how tall mount everest is", "search_web", {"query": "how tall mount everest is"}),
    ],
)
def test_camera_identify_and_search_phrases_map_to_tools(
    said: str, tool: str, args: dict[str, Any]
) -> None:
    found = Intents.load().find(said)
    assert found is not None and found.tool == tool
    assert found.args_for(normalise(said)) == args


@pytest.mark.parametrize(
    "said",
    [
        "I opened the camera app yesterday",
        "how do I open the camera",
        "what is this song called on the radio today please",
        "check the battery history",
        "search your memory",
        "did you snap that",
    ],
)
def test_near_misses_for_the_new_intents(said: str) -> None:
    found = Intents.load().find(said)
    assert found is None or found.tool in ("read_battery",)


async def test_open_the_camera_explains_then_photographs_or_asks_first() -> None:
    from aiglasses.assistant.tools.base import ToolContext
    from aiglasses.assistant.tools.device import PhotoArgs, TakePhoto
    from tests.test_tools import FakeDevice

    said: list[str] = []

    async def announce(text: str) -> None:
        said.append(text)

    device = FakeDevice()
    tool = TakePhoto(device, min_interval_s=0, max_per_session=3)
    await tool.run(
        PhotoArgs(intro=True), ToolContext(utterance="open the camera", announce=announce)
    )
    assert said == ["The camera is always ready. Taking a photo."] and device.confirms == [True]
    asking = TakePhoto(device, min_interval_s=0, max_per_session=3, ask_first_on_open=True)
    result = await asking.run(PhotoArgs(intro=True), ToolContext(utterance="open the camera"))
    assert "Want me to take a photo" in (result.spoken or "") and device.confirms == [True]


# --- diagnose ---------------------------------------------------------------------------------


async def test_diagnose_traces_phrases_without_touching_anything() -> None:
    phrases = ("open the camera", "identify this object", "search for cats", "tell me a joke")
    rows = await diagnose(settings(tools_enabled="take_photo,describe_scene,search_web"), phrases)
    by_phrase = {r.phrase: r for r in rows}
    assert (
        by_phrase["open the camera"].tool == "take_photo" and by_phrase["open the camera"].enabled
    )
    assert by_phrase["identify this object"].stopped_at.startswith("error")
    assert "fetch the photo" in by_phrase["identify this object"].spoken
    assert by_phrase["tell me a joke"].lane == "local model decides"
    off = await diagnose(settings(tools_enabled="get_time"), ("take a photo",))
    assert off[0].enabled is False and off[0].stopped_at.startswith("disabled")


def test_the_example_tool_list_enables_everything_the_task_names() -> None:
    names = set(settings().enabled_tools)
    assert {"search_web", "get_time", "get_weather", "take_photo", "read_battery",
            "read_media_counts", "forget_all_that", "describe_scene"} <= names  # fmt: skip


def test_speech_normalisation_reads_units_abbreviations_and_symbols() -> None:
    from aiglasses.assistant.cleaner import clean_reply

    assert clean_reply("It is 12 km away at 30 km/h.") == (
        "It is twelve kilometres away at thirty kilometres per hour."
    )
    assert clean_reply("Dr. Rao and Mr. Shah, e.g. today, vs. tomorrow.") == (
        "Doctor Rao and Mister Shah, for example, today, versus tomorrow."
    )
    assert clean_reply("2 + 3 = 5 and yes/no") == "two plus three equals five and yes or no"
    assert clean_reply("Mail me @ noon, about ~5 kg") == "Mail me at noon, about five kilograms"
