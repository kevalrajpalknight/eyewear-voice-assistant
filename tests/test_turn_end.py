import numpy as np

from aiglasses.assistant.listening.turn_end import is_complete, trailing_silence_ms
from aiglasses.services.voice import EndReason, VoiceUtterance


def utt(tail_silence_s: float, reason: EndReason = EndReason.END_MARKER) -> VoiceUtterance:
    pcm = np.concatenate(
        [
            np.full(16000 * 2, 6000, dtype=np.int16),
            np.zeros(int(16000 * tail_silence_s), dtype=np.int16),
        ]
    )
    return VoiceUtterance(pcm, 16000, 0.0, 1.0, 100, reason, None, 1.0)


def test_trailing_silence_is_measured_in_milliseconds() -> None:
    assert trailing_silence_ms(utt(0.5).pcm, 16000) == 500
    assert trailing_silence_ms(utt(0.0).pcm, 16000) == 0
    assert trailing_silence_ms(np.zeros(16000, dtype=np.int16), 16000) == 1000
    assert trailing_silence_ms(np.zeros(0, dtype=np.int16), 16000) == 0


def test_a_quiet_ending_means_finished_and_a_loud_one_means_cut_off() -> None:
    assert is_complete(utt(0.5), 300) and not is_complete(utt(0.1), 300)


def test_a_timeout_is_always_cut_off() -> None:
    assert not is_complete(utt(1.0, EndReason.TIMEOUT), 300, "All done.")


def test_sentence_punctuation_finishes_a_request_with_a_loud_tail() -> None:
    cut = utt(0.0)
    assert not is_complete(cut, 300)
    assert is_complete(cut, 300, "What is the weather like?")
    assert not is_complete(cut, 300, "What is the weather like and")
    assert not is_complete(cut, 300, "")
