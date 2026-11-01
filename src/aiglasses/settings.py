"""Every tunable value of the voice assistant lives here and nowhere else.

Precedence, highest first: real environment, `.env`, `config.toml`, the defaults below. Secrets
come only from the environment or `.env` and are held as `SecretStr` so they never print.
"""

from pathlib import Path
from typing import Literal, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr
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
    reasoning_effort: str | None = None


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        toml_file="config.toml",
        env_ignore_empty=True,  # `KEY=` in .env means "not set"
        populate_by_name=True,
    )

    # LLM, main
    llm_provider: Provider = "openai"
    llm_model: str | None = None  # no default on purpose: `assistant check` demands it
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_temperature: float = Field(0.6, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(1200, gt=0)
    llm_timeout_s: float = Field(30.0, gt=0)
    llm_max_retries: int = Field(2, ge=0)
    llm_reasoning_effort: str | None = None  # passed to the provider only when set

    # LLM, fast (optional; every unset field inherits from the main LLM_* value)
    llm_fast_provider: Provider | None = None
    llm_fast_model: str | None = None
    llm_fast_api_key: SecretStr | None = None
    llm_fast_base_url: str | None = None
    llm_fast_temperature: float | None = Field(None, ge=0.0, le=2.0)
    llm_fast_max_tokens: int | None = Field(None, gt=0)
    llm_fast_timeout_s: float | None = Field(None, gt=0)
    llm_fast_max_retries: int | None = Field(None, ge=0)
    llm_fast_reasoning_effort: str | None = None

    # Router
    router_mode: Literal["off", "rules", "model", "local_first"] = "rules"
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
    tts_sentence_pause_s: float = Field(0.3, ge=0.0, le=1.5)  # silence between sentences
    tts_clause_pause_s: float = Field(0.12, ge=0.0, le=1.0)  # silence at commas
    tts_model_dir: Path = Path("bench/models")
    stt_model_dir: Path = Path("bench/models")

    # Audio and session
    audio_output: Literal["pc", "glasses"] = "pc"
    audio_volume_limit: float = Field(1.0, gt=0.0, le=1.0)  # 1.0 = no attenuation
    turn_join_window_s: float = Field(4.0, ge=0.0)

    # Assistant behaviour
    assistant_name: str = "EVA"
    assistant_name_long: str = "Eyewear Voice Assistant"
    startup_line: str = ""  # spoken once when the assistant is ready; empty = silent
    wake_mode: Literal["button", "glasses_phrase", "hfp_experimental"] = "button"
    wake_required: bool = False  # true: ignore speech that does not start with a wake phrase
    wake_phrases_path: Path | None = None  # None = the list shipped in the package
    wake_ack: str = "Yes?"  # said when only the wake phrase was heard
    followup_window_s: float = Field(0.0, ge=0.0)  # reserved: needs a safe way to restart sessions
    persona_path: Path | None = None  # None = the persona shipped in the package
    assistant_timezone: str = "Asia/Kolkata"
    max_turns_per_session: int = Field(50, gt=0)
    max_reply_seconds: float = Field(60.0, gt=0)
    fallback_llm_error: str = "Sorry, I could not reach my brain just now. Please try again."
    fallback_no_speech: str = "Sorry, I did not catch that. Could you say it again?"
    fallback_tts_error: str = "Sorry, I lost my voice for a moment."

    # Memory (RAM only; one format for every lane)
    memory_max_turns: int = Field(
        10, ge=0, validation_alias=AliasChoices("MEMORY_MAX_TURNS", "HISTORY_TURNS")
    )
    memory_ttl_s: float = Field(600.0, gt=0)
    memory_max_tokens: int = Field(1500, gt=0)  # cap for the local model's context

    # Reply length
    reply_target_sentences: int = Field(3, ge=1)
    reply_soft_max_sentences: int = Field(
        8, ge=1, validation_alias=AliasChoices("REPLY_SOFT_MAX_SENTENCES", "REPLY_MAX_SENTENCES")
    )
    first_chunk_min_words: int = Field(
        8, ge=2
    )  # first spoken chunk: sentence end or a comma after this
    wrap_up_line: str = "Do you want me to go on?"

    # Local model and server (llama.cpp)
    local_llm_bin_dir: Path = Path("tools/llama.cpp")
    local_llm_model: str | None = None  # a name from the model registry
    local_llm_threads: int | None = Field(None, gt=0)  # None = physical cores
    local_llm_ctx: int = Field(4096, gt=0)
    local_llm_port: int = Field(8081, gt=0, lt=65536)
    local_llm_batch: int = Field(512, gt=0)
    local_llm_max_tokens: int = Field(120, gt=0)  # per local step; one step is a short JSON
    local_llm_preload: bool = True
    local_llm_prompt_cache: bool = True
    local_first_token_timeout_s: float = Field(6.0, gt=0)
    models_dir: Path = Path("models")
    model_registry_path: Path = Path("src/aiglasses/assistant/models.yaml")
    model_download_budget_gb: float = Field(8.0, gt=0)
    escalation_filler: str = "One moment, let me think about that."

    # Agent loop and tools
    agent_max_steps: int = Field(3, ge=1, le=10)
    agent_time_budget_s: float = Field(8.0, gt=0)
    tools_enabled: str = (
        "search_web,get_time,get_weather,take_photo,read_battery,read_media_counts,"
        "forget_all_that,describe_scene"
    )
    tool_max_calls_per_turn: int = Field(3, ge=1)
    tool_timeout_s: float = Field(10.0, gt=0)
    allow_recording_tools: bool = False
    camera_open_asks_first: bool = False  # "open the camera": ask before taking the photo
    photo_min_interval_s: float = Field(10.0, ge=0)
    photo_max_per_session: int = Field(10, ge=0)
    intents_path: Path | None = None  # None = the intents file shipped in the package
    turn_end_silence_ms: int = Field(300, ge=0)

    # Search
    search_provider: str = "ddgs"
    search_max_results: int = Field(5, ge=1, le=10)
    search_region: str = "wt-wt"
    search_cache_ttl_s: float = Field(300.0, ge=0)
    search_timeout_s: float = Field(5.0, gt=0)

    # Weather (free public forecast service, no key)
    weather_location: str = ""  # default city when none is spoken; empty = ask
    weather_geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    weather_forecast_url: str = "https://api.open-meteo.com/v1/forecast"

    # Vision
    vision_provider: Literal["off", "local", "cloud"] = "off"
    vision_allow_cloud: bool = False  # cloud vision only if this is true as well
    wifi_direct_enabled: bool = (
        False  # fetch photos over Wi-Fi Direct (needs the helper, unverified)
    )
    wifi_direct_helper_python: Path = Path("tools/wifidirect-env/Scripts/python.exe")
    vision_max_image_side: int = Field(768, gt=0)
    # how a picture is made for describe_scene: "ai_photo" asks the glasses for it (hands-free),
    # "wait" waits for you to double-click the rear button
    vision_capture: Literal["ai_photo", "wait"] = "wait"
    vision_capture_wait_s: float = Field(30.0, gt=0)

    # Live calls (cloud or search) made through `assistant check`
    live_call_budget: int = Field(3, ge=0)

    # Logging and privacy
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_transcripts: bool = False  # off: logs carry only lengths and timings
    show_transcripts: bool = True  # print what was heard and what is said (console only)
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
    def history_turns(self) -> int:
        """Old name of MEMORY_MAX_TURNS."""
        return self.memory_max_turns

    @property
    def enabled_tools(self) -> tuple[str, ...]:
        return tuple(t.strip() for t in self.tools_enabled.split(",") if t.strip())

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
            reasoning_effort=self.llm_reasoning_effort,
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
                "reasoning_effort": self.llm_fast_reasoning_effort or main.reasoning_effort,
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
                "reasoning_effort": None,
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
