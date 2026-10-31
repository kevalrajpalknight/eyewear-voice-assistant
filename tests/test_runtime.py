import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import typer

from aiglasses.assistant import engines, registry, runtime, session
from aiglasses.assistant.fakes import FakeLlm, FakeStt, FakeTts
from aiglasses.assistant.recorder import Sentence
from aiglasses.assistant.router import RoutedLlmClient
from aiglasses.assistant.speaker import NullSink, SubprocessSpeakerSink
from aiglasses.config import Settings
from aiglasses.domain.errors import ConfigError, GlassesError
from aiglasses.services.voice import EndReason, VoiceUtterance
from aiglasses.settings import AssistantSettings


def assistant(**values: object) -> AssistantSettings:
    return AssistantSettings(_env_file=None, llm_model="m", llm_api_key="k", **values)  # type: ignore[arg-type]


def ble(tmp_path: Path) -> Settings:
    return Settings(log_dir=tmp_path / "logs", captures_dir=tmp_path / "captures")


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    path = tmp_path / "in.wav"
    sf.write(path, np.full(16000, 3000, dtype=np.int16), 16000)
    return path


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    made: dict[str, Any] = {
        "llm": FakeLlm([("Hello there, nice to meet you. ",)]),
        "tts": FakeTts(),
    }
    stt = FakeStt("hello assistant")
    for module in (runtime, session):
        monkeypatch.setattr(module, "build_stt", lambda s, normalize=False: stt)
        monkeypatch.setattr(module, "build_tts", lambda s: made["tts"])
    monkeypatch.setattr(
        runtime, "build_llm",
        lambda s, force_role, no_router: RoutedLlmClient(s, made["llm"]),
    )  # fmt: skip
    made["stt"] = stt
    return made


async def test_run_assistant_with_a_wav_runs_the_whole_pipeline_silently(
    tmp_path: Path, wav: Path, fakes: dict[str, Any]
) -> None:
    await runtime.run_assistant(
        ble(tmp_path), assistant(), output="null", force_role=None, no_router=False, wav=wav
    )
    assert fakes["stt"].calls and fakes["llm"].requests
    assert "Hello there" in " ".join(fakes["tts"].spoken)
    row = json.loads((tmp_path / "logs" / "assistant_turns.jsonl").read_text())
    assert row["outcome"] == "spoken" and "hello" not in json.dumps(row).lower()


async def test_run_assistant_with_transcripts_on_prints_and_logs_the_text(
    tmp_path: Path, wav: Path, fakes: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    await runtime.run_assistant(
        ble(tmp_path), assistant(log_transcripts=True), output="null", force_role=None,
        no_router=False, wav=wav,
    )  # fmt: skip
    row = json.loads((tmp_path / "logs" / "assistant_turns.jsonl").read_text())
    assert row["heard"] == "hello assistant" and "Hello there" in row["reply"]


def fake_glasses(clicks: list[VoiceUtterance]) -> SimpleNamespace:
    @asynccontextmanager
    async def connect(settings: Settings) -> AsyncIterator[SimpleNamespace]:
        async def voice_requests(on_start: Any = None) -> AsyncIterator[VoiceUtterance]:
            for click in clicks:
                yield click

        yield SimpleNamespace(voice_requests=voice_requests)

    return SimpleNamespace(connect=connect)


def click() -> VoiceUtterance:
    now = time.time()
    return VoiceUtterance(
        np.full(16000, 3000, dtype=np.int16),
        16000,
        now - 1,
        now,
        50,
        EndReason.END_MARKER,
        None,
        now,
    )


async def test_run_assistant_serves_clicks_until_the_turn_limit(
    tmp_path: Path, fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "Glasses", fake_glasses([click()]))
    await runtime.run_assistant(
        ble(tmp_path), assistant(max_turns_per_session=1), output="null", force_role=None,
        no_router=False, wav=None,
    )  # fmt: skip
    assert fakes["llm"].requests


async def test_run_echo_speaks_back_what_it_heard_and_applies_the_glossary(
    tmp_path: Path, wav: Path, fakes: dict[str, Any]
) -> None:
    glossary = tmp_path / "glossary.txt"
    glossary.write_text("Zorvex Quill\n", encoding="utf-8")
    fakes["stt"]._texts = ["ask zorvexquill now please"]
    await session.run_echo(
        ble(tmp_path), assistant(glossary_enabled=True, glossary_path=glossary),
        output="null", normalize=False, debug=False, wav=wav,
    )  # fmt: skip
    assert " ".join(fakes["tts"].spoken[1:]) == "ask Zorvex Quill now please"  # [0] is warm-up
    corrections = (tmp_path / "logs" / "glossary_corrections.jsonl").read_text()
    assert "zorvexquill" in corrections


def test_glossary_settings_are_validated(tmp_path: Path) -> None:
    assert session.load_glossary(assistant()) is None
    with pytest.raises(ConfigError, match="does not exist"):
        session.load_glossary(assistant(glossary_enabled=True, glossary_path=tmp_path / "no.txt"))
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="no terms"):
        session.load_glossary(assistant(glossary_enabled=True, glossary_path=empty))


def test_wav_input_must_be_16k(tmp_path: Path) -> None:
    bad = tmp_path / "bad.wav"
    sf.write(bad, np.zeros(100, dtype=np.int16), 8000)
    with pytest.raises(GlassesError, match="16000 Hz"):
        session.utterance_from_wav(bad)


def test_sinks_follow_the_audio_settings() -> None:
    assert isinstance(session.make_sink(assistant(), "null"), NullSink)
    pc = session.make_sink(assistant(audio_output="glasses", audio_volume_limit=0.5), None)
    assert isinstance(pc, SubprocessSpeakerSink) and pc._volume == 0.5


def test_registry_factories_pass_the_settings_to_the_engines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    class Recorder:
        name = "recorder"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen[type(self).__name__] = (args, kwargs)

    for name in ("MoonshineStt", "ParakeetStt", "FasterWhisperStt", "KokoroTts"):
        monkeypatch.setattr(engines, name, type(name, (Recorder,), {}))
    glossary = tmp_path / "g.txt"
    glossary.write_text("Zorvex Quill\n", encoding="utf-8")
    base = {"stt_model_dir": tmp_path, "tts_model_dir": tmp_path / "t"}
    registry.build_stt(assistant(stt_model="tiny", **base))
    registry.build_stt(assistant(stt_engine="parakeet", stt_threads=2, **base))
    registry.build_stt(
        assistant(
            stt_engine="faster-whisper",
            stt_compute_type="int8_float16",
            stt_threads=3,
            stt_hints_enabled=True,
            glossary_path=glossary,
            **base,
        )
    )
    registry.build_tts(assistant(tts_variant="fp32", **base))
    assert seen["MoonshineStt"][0] == (tmp_path, "tiny")
    assert seen["ParakeetStt"][1]["threads"] == 2
    fw = seen["FasterWhisperStt"]
    assert fw[0][1] == "base.en" and fw[1]["compute_type"] == "int8_float16"
    assert fw[1]["hints"] == ["Zorvex Quill"] and fw[1]["threads"] == 3
    assert seen["KokoroTts"][0] == (tmp_path / "t", "fp32")


async def test_bench_record_glue_saves_takes_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    future = time.time() + 1e6
    take = VoiceUtterance(
        np.full(16000, 3000, dtype=np.int16), 16000, future, future + 1, 50,
        EndReason.END_MARKER, None, future,
    )  # fmt: skip
    monkeypatch.setattr(session, "Glasses", fake_glasses([take]))
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: False)
    await session.run_bench_record(ble(tmp_path), tmp_path / "out", [Sentence("a", "short", "Hi.")])
    assert (tmp_path / "out" / "a.wav").exists()
