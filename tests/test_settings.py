from pathlib import Path

import pytest
from pydantic import ValidationError

from aiglasses.settings import AssistantSettings


def make(**values: object) -> AssistantSettings:
    """Explicit settings for tests: no .env file is read."""
    return AssistantSettings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in AssistantSettings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_there_is_no_default_model_and_the_problems_say_so_without_a_key() -> None:
    settings = make()
    assert settings.llm_model is None
    assert any("LLM_MODEL" in p for p in settings.problems())
    assert any("LLM_API_KEY" in p for p in settings.problems())


def test_a_complete_setup_has_no_problems() -> None:
    assert make(llm_model="m-1", llm_api_key="k-1").problems() == []


def test_fast_and_router_roles_inherit_every_unset_field_from_main() -> None:
    settings = make(llm_model="big", llm_api_key="secret-1", llm_temperature=0.3, llm_max_retries=5)
    fast = settings.llm_role("fast")
    assert (fast.model, fast.temperature, fast.max_retries) == ("big", 0.3, 5)
    assert fast.api_key is not None and fast.api_key.get_secret_value() == "secret-1"
    router = settings.llm_role("router")
    assert router.model == "big" and router.max_tokens < 10 and router.temperature == 0.0


def test_fast_overrides_apply_and_the_router_inherits_from_fast() -> None:
    settings = make(
        llm_model="big", llm_api_key="k", llm_fast_model="small", llm_fast_provider="anthropic",
        llm_fast_timeout_s=5.0, router_timeout_s=2.0,
    )  # fmt: skip
    fast, router = settings.llm_role("fast"), settings.llm_role("router")
    assert (fast.model, fast.provider, fast.timeout_s) == ("small", "anthropic", 5.0)
    assert (router.model, router.provider, router.timeout_s) == ("small", "anthropic", 2.0)
    assert settings.fast_configured and not make(llm_model="x").fast_configured


def test_the_environment_beats_the_dotenv_file_which_beats_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("TTS_VOICE=from_file\nTTS_SPEED=1.2\nSTT_THREADS=\n", encoding="utf-8")
    monkeypatch.setenv("TTS_SPEED", "0.9")
    settings = AssistantSettings(_env_file=dotenv)
    assert settings.tts_voice == "from_file" and settings.tts_speed == 0.9
    assert settings.stt_threads == 4  # an empty value means "not set"


def test_secrets_never_appear_in_repr_or_dumps() -> None:
    settings = make(llm_model="m", llm_api_key="super-secret-value")
    assert "super-secret-value" not in repr(settings)
    assert "super-secret-value" not in str(settings.model_dump())
    assert "super-secret-value" not in repr(settings.llm_role("main"))


def test_bad_values_are_rejected_with_a_validation_error() -> None:
    for bad in ({"tts_speed": 0.0}, {"llm_provider": "nope"}, {"audio_volume_limit": 1.5}):
        with pytest.raises(ValidationError):
            make(**bad)


def test_keywords_are_split_and_lower_cased() -> None:
    assert make(router_hard_keywords=" Search, News ,, explain in detail").hard_keywords == (
        "search",
        "news",
        "explain in detail",
    )


def test_unknown_role_is_an_error_and_glasses_output_is_a_warning_not_a_problem() -> None:
    with pytest.raises(ValueError, match="valid: main, fast, router"):
        make().llm_role("other")
    settings = make(audio_output="glasses", llm_model="m", llm_api_key="k")
    assert settings.problems() == [] and "glasses" in settings.warnings()[0]


def test_openai_compatible_needs_a_base_url() -> None:
    settings = make(llm_provider="openai_compatible", llm_model="m", llm_api_key="k")
    assert any("LLM_BASE_URL" in p for p in settings.problems())
