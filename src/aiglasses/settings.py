"""Every tunable value of the voice assistant lives here and nowhere else.

Precedence, highest first: real environment, `.env`, `config.toml`, the defaults below. Secrets
come only from the environment or `.env` and are held as `SecretStr` so they never print.
"""

from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

PROVIDERS = ("openai", "anthropic", "google", "openrouter", "openai_compatible")
T = TypeVar("T")
Provider = Literal["openai", "anthropic", "google", "openrouter", "openai_compatible"]
ROLES = ("main", "fast", "router")


class LlmRole(BaseModel):
    """One resolved LLM configuration (main, fast or router) after inheritance."""

    model_config = ConfigDict(frozen=True)

    role: str
    provider: Provider
    model: str | None
    api_key: SecretStr | None
    base_url: str | None
    temperature: float
    max_tokens: int
    timeout_s: float
    max_retries: int


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        toml_file="config.toml",
        env_ignore_empty=True,  # `KEY=` in .env means "not set"
    )

    # LLM, main
    llm_provider: Provider = "openai"
    llm_model: str | None = None  # no default on purpose: `assistant check` demands it
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = Field(0.6, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(300, gt=0)
    llm_timeout_s: float = Field(30.0, gt=0)
    llm_max_retries: int = Field(2, ge=0)

    # LLM, fast (optional; every unset field inherits from the main LLM_* value)
    llm_fast_provider: Provider | None = None
    llm_fast_model: str | None = None
    llm_fast_api_key: SecretStr | None = None
    llm_fast_base_url: str | None = None
    llm_fast_temperature: float | None = Field(None, ge=0.0, le=2.0)
    llm_fast_max_tokens: int | None = Field(None, gt=0)
    llm_fast_timeout_s: float | None = Field(None, gt=0)
    llm_fast_max_retries: int | None = Field(None, ge=0)

    # Router
    router_mode: Literal["off", "rules", "model"] = "rules"
    router_max_easy_words: int = Field(8, gt=0)
    router_hard_keywords: str = (
        "search,latest,news,weather,price,look,see,compare,explain in detail,translate,"
        "summarise,summarize,write,research,why does,how does"
    )
    router_model_provider: Provider | None = None
    router_model_model: str | None = None
    router_model_api_key: SecretStr | None = None
    router_model_base_url: str | None = None
    router_timeout_s: float = Field(3.0, gt=0)
    router_fallback: Literal["strong", "fast"] = "strong"

    # Speech to text
    stt_engine: str = "moonshine"
    stt_model: str | None = None  # None = the engine's own default
    stt_compute_type: str = "int8"
    stt_language: str = "en"
    stt_threads: int = Field(4, gt=0)
    stt_hints_enabled: bool = False
    glossary_enabled: bool = False
    glossary_path: Path = Path("bench/data/glossary.txt")
    glossary_threshold: float = Field(88.0, ge=0.0, le=100.0)

    # Text to speech
    tts_engine: str = "kokoro"
    tts_variant: Literal["int8", "fp32"] = "int8"
    tts_voice: str = "bf_isabella"
    tts_speed: float = Field(1.05, gt=0.25, le=3.0)
    tts_first_clause_max_words: int = Field(6, ge=2)
    tts_model_dir: Path = Path("bench/models")
    stt_model_dir: Path = Path("bench/models")

    # Audio and session
    audio_output: Literal["pc", "glasses"] = "pc"
    audio_volume_limit: float = Field(1.0, gt=0.0, le=1.0)  # 1.0 = no attenuation
    turn_join_window_s: float = Field(4.0, ge=0.0)

    # Assistant behaviour
    persona_path: Path | None = None  # None = the persona shipped in the package
    assistant_timezone: str = "Asia/Kolkata"
    history_turns: int = Field(6, ge=0)
    max_turns_per_session: int = Field(50, gt=0)
    max_reply_seconds: float = Field(25.0, gt=0)
    fallback_llm_error: str = "Sorry, I could not reach my brain just now. Please try again."
    fallback_no_speech: str = "Sorry, I did not catch that. Could you say it again?"
    fallback_tts_error: str = "Sorry, I lost my voice for a moment."

    # Logging and privacy
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_transcripts: bool = False  # off: logs carry only lengths and timings
    debug_save_audio: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
        )

    @property
    def hard_keywords(self) -> tuple[str, ...]:
        return tuple(k.strip().lower() for k in self.router_hard_keywords.split(",") if k.strip())

    def llm_role(self, role: str) -> LlmRole:
        """Resolve `main`, `fast` or `router`. Unset fast and router fields inherit upwards."""
        if role not in ROLES:
            raise ValueError(f"unknown LLM role {role!r}; valid: {', '.join(ROLES)}")
        main = LlmRole(
            role="main", provider=self.llm_provider, model=self.llm_model,
            api_key=self.llm_api_key, base_url=self.llm_base_url,
            temperature=self.llm_temperature, max_tokens=self.llm_max_tokens,
            timeout_s=self.llm_timeout_s, max_retries=self.llm_max_retries,
        )  # fmt: skip
        if role == "main":
            return main
        fast = main.model_copy(
            update={
                "role": "fast",
                "provider": self.llm_fast_provider or main.provider,
                "model": self.llm_fast_model or main.model,
                "api_key": self.llm_fast_api_key or main.api_key,
                "base_url": self.llm_fast_base_url or main.base_url,
                "temperature": _first(self.llm_fast_temperature, main.temperature),
                "max_tokens": _first(self.llm_fast_max_tokens, main.max_tokens),
                "timeout_s": _first(self.llm_fast_timeout_s, main.timeout_s),
                "max_retries": _first(self.llm_fast_max_retries, main.max_retries),
            }
        )
        if role == "fast":
            return fast
        return fast.model_copy(
            update={
                "role": "router",
                "provider": self.router_model_provider or fast.provider,
                "model": self.router_model_model or fast.model,
                "api_key": self.router_model_api_key or fast.api_key,
                "base_url": self.router_model_base_url or fast.base_url,
                "timeout_s": self.router_timeout_s,
                "max_tokens": _ROUTER_REPLY_TOKENS,
                "temperature": 0.0,
            }
        )

    @property
    def fast_configured(self) -> bool:
        """A separate fast model exists only when LLM_FAST_MODEL is set."""
        return self.llm_fast_model is not None

    def problems(self) -> list[str]:
        """Human-readable configuration problems, never containing a secret."""
        found = []
        if self.llm_model is None:
            found.append("LLM_MODEL is not set (there is no default; add it to .env)")
        if self.llm_api_key is None:
            found.append("LLM_API_KEY is not set (add it to .env or the environment)")
        if self.llm_provider == "openai_compatible" and self.llm_base_url is None:
            found.append("LLM_PROVIDER=openai_compatible needs LLM_BASE_URL")
        if self.router_mode == "model" and self.llm_role("router").model is None:
            found.append("ROUTER_MODE=model needs a router, fast or main model")
        return found

    def warnings(self) -> list[str]:
        if self.audio_output == "glasses":
            return ["AUDIO_OUTPUT=glasses is not supported until pairing; the PC speakers are used"]
        return []


def _first(preferred: T | None, fallback: T) -> T:
    return fallback if preferred is None else preferred


_ROUTER_REPLY_TOKENS = 4
