import httpx
import pytest

from aiglasses.assistant.conversation import AssistantTurnRecord  # noqa: F401
from aiglasses.assistant.fakes import FakeLlm
from aiglasses.assistant.fragments import Fragments
from aiglasses.assistant.intents import Intents, normalise
from aiglasses.assistant.search import (
    FakeSearch,
    FallbackSearch,
    SearchError,
    SearchHit,
    WikipediaSearch,
)
from aiglasses.assistant.wake import learn_names, read_learned, save_learned
from tests.test_conversation import Rig, utterance


@pytest.mark.parametrize(
    "phrase,tool",
    [
        ("Use the glasses camera to find this product.", "describe_scene"),
        ("find this product", "describe_scene"),
        ("What is this product? Can you take the picture?", "describe_scene"),
        ("Use the glasses scanner to find this product", "describe_scene"),
        ("take a picture", "take_photo"),
        ("open the camera", "take_photo"),
        ("What's the temperature in Delhi right now?", "get_weather"),
    ],
)
def test_the_camera_and_weather_phrases_reach_the_right_tool(phrase: str, tool: str) -> None:
    found = Intents.load().find(phrase)
    assert found is not None and found.tool == tool


def test_the_temperature_question_names_the_city() -> None:
    found = Intents.load().find("What's the temperature in Delhi right now?")
    assert found is not None
    assert found.args_for(normalise("What's the temperature in Delhi right now?")) == {
        "location": "delhi"
    }


def test_sounds_and_thanks_are_told_apart_from_requests() -> None:
    fragments = Fragments.load()
    assert fragments.kind("?") == "ignore" and fragments.kind("Okay.") == "ignore"
    assert fragments.kind("Um,") == "ignore" and fragments.kind("Thank you!") == "thanks"
    assert fragments.kind("what time is it") is None


def test_ordinary_words_are_never_learned_as_the_wake_name(tmp_path) -> None:  # type: ignore[no-untyped-def]
    said = ["How", "Error", "Hey Eva.", "Favour.", "Hey you are.", "Several.", "Siva."]
    assert learn_names(said, ["hey"]) == ["eva", "favour", "siva"]
    path = tmp_path / "learned.yaml"
    save_learned(path, ["how", "you", "here you are", "favour", "siva"])
    assert read_learned(path) == ["favour", "siva"]


async def test_the_microphone_ignores_sounds_and_answers_thanks_without_the_model() -> None:
    rig = Rig(llm=FakeLlm([("Never used.",)]))
    rig.pipeline.input_is_microphone = True
    ignored: list[tuple[str, str]] = []
    rig.pipeline.on_ignored = lambda text, why: ignored.append((text, why))
    rig.stt._texts = ["Okay."]
    first = await rig.pipeline.run_turn(utterance(), 1)
    assert first.outcome == "ignored" and ignored == [("Okay.", "just a sound")]
    rig.stt._texts = ["Thank you."]
    thanks = await rig.pipeline.run_turn(utterance(), 2)
    assert thanks.outcome == "spoken" and rig.llm.requests == []
    assert any(w in " ".join(rig.tts.spoken) for w in ("welcome", "Anytime", "help", "problem"))


async def test_search_retries_once_then_uses_the_second_source() -> None:
    class Flaky(FakeSearch):
        async def search(self, query, *, max_results, region, timeout_s):  # type: ignore[no-untyped-def]
            self.queries.append(query)
            raise SearchError("timeout")

    first, second = Flaky(), FakeSearch([SearchHit("Tata Nano", "Launched in 2008.")])
    hits = await FallbackSearch(first, second, retries=1).search(
        "tata nano", max_results=3, region="x", timeout_s=1
    )
    assert len(first.queries) == 2 and hits[0].title == "Tata Nano"
    with pytest.raises(SearchError, match="timeout"):
        await FallbackSearch(Flaky(), None).search("q", max_results=3, region="x", timeout_s=1)


async def test_the_wikipedia_source_reads_titles_and_strips_markup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["srsearch"] == "tata nano"
        rows = [
            {
                "title": "Tata Nano",
                "snippet": 'The <span class="searchmatch">Nano</span> was launched in 2008.',
            }
        ]
        return httpx.Response(200, json={"query": {"search": rows}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    hits = await WikipediaSearch("https://example.invalid/w", client).search(
        "tata nano", max_results=3, region="x", timeout_s=2
    )
    assert hits == [SearchHit("Tata Nano", "The Nano was launched in 2008.")]
