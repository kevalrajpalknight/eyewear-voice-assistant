import pytest

from aiglasses.assistant.fakes import FakeLlm, FakeStt
from aiglasses.assistant.wake import WakePhrases
from tests.test_conversation import Rig, utterance


@pytest.mark.parametrize(
    ("said", "rest", "woke"),
    [
        ("EVA, what time is it", "what time is it", True),
        ("Hey EVA what is the weather", "what is the weather", True),
        ("hello Eva, tell me a joke", "tell me a joke", True),
        ("Eva listen. Turn it down", "Turn it down", True),
        ("Ava, are you there", "", True),
        ("hey Evo what's up", "what's up", True),
        ("tell me about Eve and the apple", "tell me about Eve and the apple", False),
        ("what time is it", "what time is it", False),
        ("every day is a gift", "every day is a gift", False),
    ],
)
def test_wake_phrases_are_stripped_and_ordinary_speech_is_left_alone(
    said: str, rest: str, woke: bool
) -> None:
    result = WakePhrases.load().strip(said)
    assert (result.text, result.woke) == (rest, woke)


def test_only_the_wake_phrase_is_recognised_as_such() -> None:
    for said in ("EVA", "Hey EVA.", "hello Eva, listen"):
        result = WakePhrases.load().strip(said)
        assert result.woke and result.only_wake and result.text == ""


async def test_the_wake_phrase_is_removed_before_the_model_sees_the_request() -> None:
    rig = Rig(stt=FakeStt("Hey EVA, how are you today"), llm=FakeLlm([("Fine, thanks.",)]))
    rig.pipeline.wake = WakePhrases.load()
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.woke and rig.llm.requests[0][-1].content == "how are you today"


async def test_only_the_wake_phrase_gets_a_short_acknowledgement_and_no_model_call() -> None:
    rig = Rig(stt=FakeStt("hey eva"))
    rig.pipeline.wake = WakePhrases.load()
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.outcome == "wake_ack" and rig.tts.spoken == [rig.settings.wake_ack]
    assert rig.llm.requests == []


async def test_when_a_wake_phrase_is_required_other_speech_is_ignored_silently() -> None:
    rig = Rig(stt=FakeStt("what time is it"), wake_required=True)
    rig.pipeline.wake = WakePhrases.load()
    record = await rig.pipeline.run_turn(utterance(), 1)
    assert record.outcome == "ignored" and rig.tts.spoken == [] and rig.llm.requests == []
    woke = Rig(stt=FakeStt("EVA what time is it"), wake_required=True)
    woke.pipeline.wake = WakePhrases.load()
    assert (await woke.pipeline.run_turn(utterance(), 1)).outcome == "spoken"


def test_the_persona_names_the_assistant_from_settings() -> None:
    from aiglasses.assistant.app import load_persona
    from aiglasses.assistant.conversation import system_prompt
    from tests.test_conversation import FIXED_NOW, settings

    prompt = system_prompt(
        load_persona(settings()), None, FIXED_NOW, 3, ("EVA", "Eyewear Voice Assistant")
    )
    assert "Your name is EVA, short for Eyewear Voice Assistant" in prompt
    assert "{" not in prompt
