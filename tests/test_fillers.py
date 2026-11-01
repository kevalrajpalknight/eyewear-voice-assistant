import itertools
import random

from aiglasses.assistant.conversation.fillers import Fillers
from aiglasses.assistant.conversation.pipeline import AssistantTurnRecord
from aiglasses.assistant.run.runtime import brain_colour, describe_rich


def test_the_shipped_pools_are_large_short_and_never_repeat_back_to_back() -> None:
    fillers = Fillers.load(rng=random.Random(1))
    thinking = fillers.phrases("thinking")
    assert len(thinking) >= 50 and len(fillers.phrases("search_web")) >= 10
    assert all(len(p.split()) <= 8 for p in thinking)
    picks = [fillers.pick("thinking") for _ in range(200)]
    assert all(a != b for a, b in itertools.pairwise(picks))
    assert len(set(picks)) > 20
    assert fillers.pick("no such pool") is None


def test_the_turn_summary_is_coloured_by_who_answered() -> None:
    record = AssistantTurnRecord(1, "end_marker", 1.0, 0.0, 0.0)
    record.outcome, record.tools = "spoken", "search_web:ok"
    for brain, colour in (
        ("cloud openai:gpt-5-nano", "magenta"),
        ("local model gemma", "green"),
        ("rules (no model)", "yellow"),
        ("", "dim"),
    ):
        record.brain = brain
        assert brain_colour(record) == colour
        assert colour in describe_rich(record) and "turn 1" in describe_rich(record)
