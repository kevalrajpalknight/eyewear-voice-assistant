from pathlib import Path

import pytest
from rich.console import Console

from aiglasses.assistant import check as check_module
from aiglasses.assistant import registry
from aiglasses.assistant.check import reset_live_budget, run_check
from aiglasses.assistant.fakes import FakeStt, FakeTts
from aiglasses.assistant.router import RoutedLlmClient
from aiglasses.assistant.runtime import build_llm
from aiglasses.domain.errors import ConfigError
from aiglasses.settings import AssistantSettings

SECRET = "secret-value-for-tests-123"


def settings(**values: object) -> AssistantSettings:
    return AssistantSettings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def no_real_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_module, "audio_device_line", lambda: "fake speaker")


def test_engines_are_chosen_only_by_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def fake_stt(s: AssistantSettings, model: str, normalize: bool) -> FakeStt:
        seen.append((s.stt_engine, model))
        return FakeStt("x")

    monkeypatch.setitem(registry.STT_ENGINES, "fake", (fake_stt, "default-model", ()))
    monkeypatch.setitem(registry.TTS_ENGINES, "fake", lambda s: FakeTts())
    assert isinstance(registry.build_stt(settings(stt_engine="fake")), FakeStt)
    registry.build_stt(settings(stt_engine="fake", stt_model="other"))
    assert seen == [("fake", "default-model"), ("fake", "other")]
    assert isinstance(registry.build_tts(settings(tts_engine="fake")), FakeTts)


def test_unknown_engines_and_models_list_the_valid_names() -> None:
    with pytest.raises(ConfigError, match="valid: faster-whisper, moonshine, parakeet"):
        registry.build_stt(settings(stt_engine="nope"))
    with pytest.raises(ConfigError, match=r"STT_MODEL for moonshine.*valid: base"):
        registry.build_stt(settings(stt_model="huge"))
    with pytest.raises(ConfigError, match="TTS_ENGINE"):
        registry.build_tts(settings(tts_engine="nope"))
    found = registry.engine_problems(settings(stt_engine="nope", tts_engine="nope2"))
    assert len(found) == 2


def test_missing_model_folders_are_reported(tmp_path: Path) -> None:
    found = registry.model_dir_problems(
        settings(stt_model_dir=tmp_path / "a", tts_model_dir=tmp_path / "b")
    )
    assert (
        len(found) == 2
        and registry.model_dir_problems(settings(stt_model_dir=tmp_path, tts_model_dir=tmp_path))
        == []
    )


def test_build_llm_wraps_roles_in_the_router_and_honours_overrides() -> None:
    base = settings(llm_model="big", llm_api_key=SECRET, llm_fast_model="small")
    router = build_llm(base, force_role=None, no_router=False)
    assert isinstance(router, RoutedLlmClient) and router.fast is not None
    plain = build_llm(base, force_role=None, no_router=True)
    assert isinstance(plain, RoutedLlmClient) and plain.settings.router_mode == "off"
    forced = build_llm(base, force_role="fast", no_router=False)
    assert not isinstance(forced, RoutedLlmClient) and forced.name == "fast:openai"
    classifier = build_llm(
        base.model_copy(update={"router_mode": "model"}), force_role=None, no_router=False
    )
    assert isinstance(classifier, RoutedLlmClient) and classifier.classifier is not None


def test_build_llm_refuses_incomplete_settings_without_leaking_values() -> None:
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        build_llm(settings(llm_api_key=SECRET), force_role=None, no_router=False)


def report(assistant: AssistantSettings, tmp_path: Path, *, live: bool = False) -> tuple[int, str]:
    console = Console(record=True, width=200)
    code = run_check(assistant, live=live, log_dir=tmp_path, console=console)
    return code, console.export_text()


def test_check_never_prints_the_key_and_reports_what_is_missing(tmp_path: Path) -> None:
    code, text = report(settings(llm_api_key=SECRET), tmp_path)
    assert code == 1 and SECRET not in text
    assert "LLM_MODEL is not set" in text and "set" in text
    code, text = report(settings(llm_model="m"), tmp_path)
    assert code == 1 and "NOT set" in text and "LLM_API_KEY is not set" in text


def test_check_reports_unknown_engines_and_missing_dirs(tmp_path: Path) -> None:
    code, text = report(
        settings(
            llm_model="m", llm_api_key=SECRET, stt_engine="nope",
            stt_model_dir=tmp_path / "x", tts_model_dir=tmp_path / "y",
        ),
        tmp_path,
    )  # fmt: skip
    assert code == 1 and "STT_ENGINE" in text and "does not exist" in text and SECRET not in text


def test_a_complete_check_passes_and_shows_resolved_roles(tmp_path: Path) -> None:
    complete = settings(
        llm_model="big", llm_api_key=SECRET, llm_fast_model="small",
        stt_model_dir=tmp_path, tts_model_dir=tmp_path,
    )  # fmt: skip
    code, text = report(complete, tmp_path)
    assert "big" in text and "small" in text and "fake speaker" in text and SECRET not in text
    assert code in (0, 1)  # engine packages may be absent in a bare environment


def test_live_check_makes_one_call_per_role_and_stops_at_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_live(role: object) -> tuple[bool, str]:
        calls.append(getattr(role, "role", "?"))
        return True, "reply of 2 characters in 0.10 s"

    monkeypatch.setattr(check_module, "live_call", fake_live)
    monkeypatch.setattr(check_module, "engine_problems", lambda s: [])
    monkeypatch.setattr(check_module, "model_dir_problems", lambda s: [])
    monkeypatch.setattr(check_module, "has_module", lambda name: True)
    both = settings(llm_model="big", llm_api_key=SECRET, llm_fast_model="small")
    code, text = report(both, tmp_path, live=True)
    assert code == 0 and calls == ["main", "fast"] and "live main: ok" in text
    code, text = report(both, tmp_path, live=True)
    assert calls == ["main", "fast", "main"] and code == 1 and "budget" in text
    assert len(calls) == both.live_call_budget == 3


def test_live_check_is_skipped_while_the_settings_are_incomplete(tmp_path: Path) -> None:
    code, text = report(settings(), tmp_path, live=True)
    assert code == 1 and "Live check skipped" in text
    assert not (tmp_path / "live_calls.count").exists()


async def test_a_failed_live_call_gives_a_hint_and_never_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections.abc import AsyncIterator

    from aiglasses.assistant import llm_client
    from aiglasses.assistant.interfaces import Message
    from aiglasses.redact import redact

    class Rejecting:
        def __init__(self, role: object) -> None:
            pass

        async def stream(self, messages: list[Message], *, max_tokens: int) -> AsyncIterator[str]:
            raise llm_client.LlmAuthError(redact(f"bad key {SECRET}", [SECRET]))
            yield ""

    monkeypatch.setattr(check_module, "LangChainLlmClient", Rejecting)
    role = settings(llm_model="m", llm_api_key=SECRET).llm_role("main")
    ok, message = await check_module.live_call(role)
    assert not ok and "LLM_API_KEY" in message and SECRET not in message


def test_the_budget_comes_from_settings_and_can_be_reset(tmp_path: Path) -> None:
    from aiglasses.assistant.check import live_budget_left, spend_live_call

    counter = tmp_path / "live_calls.count"
    spend_live_call(counter)
    spend_live_call(counter)
    assert live_budget_left(counter, 3) == 1 and live_budget_left(counter, 2) == 0
    reset_live_budget(counter)
    assert live_budget_left(counter, 3) == 3
