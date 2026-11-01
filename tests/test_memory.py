import pytest

from aiglasses.assistant.conversation.interfaces import Message
from aiglasses.assistant.conversation.memory import INTERRUPTED_NOTE, Exchange, Memory, fit_tokens
from aiglasses.assistant.listening.joiner import TurnJoiner
from aiglasses.assistant.tools.base import ToolContext
from aiglasses.assistant.tools.memory_tools import ForgetAllThat, ForgetArgs


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def memory(clock: Clock, turns: int = 3, ttl: float = 60.0) -> Memory:
    return Memory(turns, ttl, 1000, clock)


def test_the_oldest_exchange_goes_when_the_turn_limit_is_hit() -> None:
    clock = Clock()
    mem = memory(clock)
    for i in range(5):
        mem.add(Exchange(f"q{i}", f"a{i}"))
    assert [e.user for e in mem.exchanges()] == ["q2", "q3", "q4"]


def test_exchanges_expire_silently_after_the_ttl_with_a_fake_clock() -> None:
    clock = Clock()
    mem = memory(clock)
    mem.add(Exchange("old", "a"))
    clock.now += 40
    mem.add(Exchange("newer", "b"))
    clock.now += 30  # old is now 70 s, newer 30 s
    assert [e.user for e in mem.exchanges()] == ["newer"]
    clock.now += 31
    assert len(mem) == 0 and mem.messages() == [] and mem.last is None


def test_whichever_limit_hits_first_applies() -> None:
    clock = Clock()
    mem = memory(clock, turns=2, ttl=100)
    mem.add(Exchange("a", "1"))
    mem.add(Exchange("b", "2"))
    mem.add(Exchange("c", "3"))
    clock.now += 101
    assert len(mem) == 0


def test_notes_and_the_interrupted_flag_use_one_compact_format() -> None:
    mem = memory(Clock())
    mem.add(
        Exchange("how much battery", "It is at 83 percent.", notes=["read_battery: 83 percent"])
    )
    mem.add(Exchange("tell me a story", "Once upon", interrupted=True))
    messages = mem.messages()
    assert messages[1] == Message("assistant", "It is at 83 percent. (read_battery: 83 percent)")
    assert messages[3].content == "Once upon" + INTERRUPTED_NOTE


def test_forget_scopes_and_listeners() -> None:
    mem = memory(Clock())
    fired: list[int] = []
    mem.on_forget(lambda: fired.append(1))
    for name in "abc":
        mem.add(Exchange(name, name))
    assert mem.forget("last_exchange") == 1 and [e.user for e in mem.exchanges()] == ["a", "b"]
    assert mem.forget("everything") == 2 and len(mem) == 0
    assert mem.forget("last_exchange") == 0 and fired == [1, 1, 1]


def test_drop_turn_only_removes_the_matching_newest_exchange() -> None:
    mem = memory(Clock())
    mem.add(Exchange("a", "1", turn=1))
    mem.add(Exchange("b", "2", turn=2))
    assert not mem.drop_turn(1) and mem.drop_turn(2) and len(mem) == 1


def test_zero_turns_stores_nothing() -> None:
    mem = Memory(0, 60, 100, Clock())
    mem.add(Exchange("a", "b"))
    assert len(mem) == 0


def test_token_cap_drops_oldest_history_but_keeps_system_and_the_new_question() -> None:
    system, question = Message("system", "S" * 40), Message("user", "now")
    history = [Message("user" if i % 2 == 0 else "assistant", "x" * 400) for i in range(6)]
    fitted = fit_tokens([system, *history, question], 250)
    assert fitted[0] == system and fitted[-1] == question and 2 <= len(fitted) < 8
    assert len(fitted) % 2 == 0  # whole exchanges only
    assert fit_tokens([system, question], 1) == [system, question]


async def test_forget_all_that_clears_memory_and_pending_joins_without_touching_logs(
    tmp_path: pytest.TempPathFactory,
) -> None:
    mem = memory(Clock())
    joiner = TurnJoiner(4.0, 300)
    mem.on_forget(joiner.reset)
    mem.add(Exchange("a", "b"))
    joiner._previous = object()  # type: ignore[assignment]
    tool = ForgetAllThat(mem)
    result = await tool.run(ForgetArgs(scope="everything"), ToolContext(utterance="clear memory"))
    assert len(mem) == 0 and joiner._previous is None
    assert "fresh" in (result.spoken or "") and result.summary.startswith("forgot 1")
    assert ForgetArgs().scope == "last_exchange"
