from aiglasses.assistant.listening.echo_guard import EchoGuard


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def rig() -> tuple[EchoGuard, Clock]:
    clock = Clock()
    guard = EchoGuard(clock=clock)
    guard.said("The Prime Minister of India is Narendra Modi.")
    guard.audio(4.0)
    return guard, clock


def test_its_own_words_heard_during_playback_are_echo() -> None:
    guard, _ = rig()
    assert guard.is_echo("Prime Minister", started_at=1001.0)
    assert guard.is_echo("It is", started_at=1002.0) is False  # words it never said
    assert guard.is_echo("?", started_at=1002.0)  # noise while it talks


def test_a_real_request_during_playback_is_not_echo() -> None:
    guard, _ = rig()
    assert not guard.is_echo("what is the weather in Mumbai", started_at=1001.0)


def test_speech_after_playback_is_never_echo_even_with_the_same_words() -> None:
    guard, clock = rig()
    clock.now = 1010.0
    assert not guard.is_echo("Prime Minister", started_at=1005.5)  # 1.5 s after it stopped
    assert guard.is_echo("Prime Minister", started_at=1004.5)  # inside the grace period


def test_old_sentences_are_forgotten() -> None:
    guard, clock = rig()
    clock.now = 1100.0
    guard.audio(1.0)
    assert not guard.is_echo("Prime Minister", started_at=1100.5)


def test_it_is_settling_only_just_after_it_starts_talking() -> None:
    guard, clock = rig()
    assert guard.settling()
    clock.now = 1002.0
    assert not guard.settling()  # still talking, but its echo level is known by now
    clock.now = 1010.0
    assert not guard.settling()
