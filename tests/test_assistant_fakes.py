import numpy as np

from aiglasses.assistant import TtsOptions
from aiglasses.assistant.conversation.interfaces import (
    AudioSink,
    LlmClient,
    Message,
    SpeechToText,
    TextToSpeech,
)
from aiglasses.assistant.testing.fakes import FakeLlm, FakeSink, FakeStt, FakeTts


def test_fakes_satisfy_the_interfaces() -> None:
    stt: SpeechToText = FakeStt()
    tts: TextToSpeech = FakeTts()
    llm: LlmClient = FakeLlm()
    sink: AudioSink = FakeSink()
    assert (stt.name, tts.name, llm.name) == ("fake-stt", "fake-tts", "fake-llm") and sink


async def test_stt_reports_audio_length_and_real_time_factor() -> None:
    result = await FakeStt("what time is it").transcribe(np.zeros(32000, dtype=np.int16))
    assert result.text == "what time is it" and result.audio_seconds == 2.0
    assert result.real_time_factor == 0.0


async def test_llm_streams_deltas_and_records_requests() -> None:
    llm = FakeLlm([("a", "b")])
    deltas = [d async for d in llm.stream([Message("user", "hi")], max_tokens=50)]
    assert deltas == ["a", "b"] and llm.max_tokens == [50]


async def test_tts_and_sink_roundtrip() -> None:
    tts, sink = FakeTts(), FakeSink()
    await sink.play(tts.synthesize_stream("Hi.", TtsOptions(voice="v", speed=1.0)))
    assert tts.spoken == ["Hi."] and len(sink.played) == 1
    assert sink.played[0].seconds == 0.1
