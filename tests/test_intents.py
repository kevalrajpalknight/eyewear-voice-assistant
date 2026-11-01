import pytest

from aiglasses.assistant.conversation.intents import Intents, normalise
from aiglasses.domain.errors import ConfigError


@pytest.fixture(scope="module")
def intents() -> Intents:
    return Intents.load()


@pytest.mark.parametrize(
    ("said", "tool"),
    [
        ("Take a photo", "take_photo"),
        ("please take a picture.", "take_photo"),
        ("Can you snap a photo", "take_photo"),
        ("How much battery is left?", "read_battery"),
        ("what's the battery level", "read_battery"),
        ("How many photos do I have?", "read_media_counts"),
        ("Forget that", "forget_all_that"),
        ("ignore what I just said", "forget_all_that"),
        ("Clear your memory", "forget_all_that"),
        ("let\u2019s start over", "forget_all_that"),
        ("What do you see?", "describe_scene"),
    ],
)
def test_clear_requests_match_their_tool(intents: Intents, said: str, tool: str) -> None:
    found = intents.find(said)
    assert found is not None and found.tool == tool


@pytest.mark.parametrize(
    "said",
    [
        "I took a photo yesterday",
        "I have taken a lot of photos",
        "who took this photo",
        "what is a photo",
        "don't take a photo",
        "the phone battery is low",
        "my laptop battery died",
        "how many photos are in the gallery on my phone",
        "tell me about the photo of the Taj Mahal",
        "what did you see yesterday",
        "Remind me to forget the milk",
    ],
)
def test_near_misses_do_not_trigger_anything(intents: Intents, said: str) -> None:
    assert intents.find(said) is None


def test_forget_scopes_come_from_the_file(intents: Intents) -> None:
    last, everything = intents.find("forget that"), intents.find("clear your memory")
    assert last is not None and last.args == {"scope": "last_exchange"}
    assert everything is not None and everything.args == {"scope": "everything"}


def test_requests_is_per_tool(intents: Intents) -> None:
    assert intents.requests("take_photo", "take a photo please")
    assert not intents.requests("take_photo", "how much battery")
    assert not intents.requests("take_photo", "I took a photo yesterday")


def test_normalise() -> None:
    assert normalise("  Let\u2019s START over!! ") == "let's start over"


def test_a_broken_file_is_a_config_error(tmp_path: "pytest.TempPathFactory") -> None:
    from pathlib import Path

    bad = Path(str(tmp_path)) / "bad.yaml"
    bad.write_text("intents:\n  x:\n    tool: t\n    match: ['(']\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        Intents.load(bad)
    with pytest.raises(ConfigError):
        Intents.load(Path(str(tmp_path)) / "missing.yaml")


@pytest.mark.parametrize(
    "phrase,tool",
    [
        ("click a picture", "take_photo"),
        ("Capture an image.", "take_photo"),
        ("take a pic", "take_photo"),
        ("Can you take a photo of this?", "take_photo"),
        ("start the camera", "take_photo"),
        ("Camera on.", "take_photo"),
        ("open the glasses camera please", "take_photo"),
    ],
)
def test_more_ways_to_ask_for_a_photo_or_the_camera(phrase: str, tool: str) -> None:
    match = Intents.load().find(phrase)
    assert match is not None and match.tool == tool


@pytest.mark.parametrize(
    "phrase",
    [
        "Here, please use glasses camera to identify the phone.",
        "Then you identify the phone in front of him.",
        "can you see what I am holding",
        "start now and describe this",
    ],
)
def test_a_model_may_look_when_the_words_ask_for_it_loosely(phrase: str) -> None:
    assert Intents.load().requests("describe_scene", normalise(phrase))


def test_loose_looking_words_do_not_allow_unrelated_requests() -> None:
    intents = Intents.load()
    assert not intents.requests("describe_scene", normalise("what is the weather today"))
    assert not intents.requests("take_photo", normalise("identify the phone"))
