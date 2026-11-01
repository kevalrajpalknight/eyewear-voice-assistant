"""Speech engines by name. Adding an engine is one factory function and one registry entry."""

from collections.abc import Callable
from pathlib import Path

from aiglasses.assistant.conversation.interfaces import SpeechToText, TextToSpeech
from aiglasses.assistant.speech.glossary import Glossary
from aiglasses.domain.errors import ConfigError
from aiglasses.settings import AssistantSettings

SttFactory = Callable[[AssistantSettings, str, bool], SpeechToText]
TtsFactory = Callable[[AssistantSettings], TextToSpeech]


def _moonshine(settings: AssistantSettings, model: str, normalize: bool) -> SpeechToText:
    from aiglasses.assistant.speech.engines import MoonshineStt

    return MoonshineStt(settings.stt_model_dir, model, normalize=normalize)


def _parakeet(settings: AssistantSettings, model: str, normalize: bool) -> SpeechToText:
    from aiglasses.assistant.speech.engines import ParakeetStt

    return ParakeetStt(settings.stt_model_dir, threads=settings.stt_threads, normalize=normalize)


def _faster_whisper(settings: AssistantSettings, model: str, normalize: bool) -> SpeechToText:
    from aiglasses.assistant.speech.engines import FasterWhisperStt

    hints: list[str] = []
    if settings.stt_hints_enabled:
        hints = [t.canonical for t in Glossary.load(settings.glossary_path).terms]
    return FasterWhisperStt(
        settings.stt_model_dir, model, compute_type=settings.stt_compute_type,
        threads=settings.stt_threads, hints=hints, normalize=normalize,
    )  # fmt: skip


def _kokoro(settings: AssistantSettings) -> TextToSpeech:
    from aiglasses.assistant.speech.engines import KokoroTts

    return KokoroTts(settings.tts_model_dir, settings.tts_variant)


def _piper(settings: AssistantSettings) -> TextToSpeech:
    from aiglasses.assistant.speech.engines import PiperTts

    path = settings.model_registry_path
    return PiperTts(settings.models_dir, settings.tts_voice, path if path.exists() else None)


# engine name -> (factory, the model used when STT_MODEL is empty, models the engine knows)
STT_ENGINES: dict[str, tuple[SttFactory, str, tuple[str, ...]]] = {
    "moonshine": (_moonshine, "base", ("tiny", "base", "small-v2", "medium-v2")),
    "parakeet": (_parakeet, "", ()),
    "faster-whisper": (_faster_whisper, "base.en", ("base.en", "small.en", "small")),
}
# Python package each engine needs, for `assistant check`
ENGINE_MODULES = {
    "moonshine": "moonshine_voice",
    "parakeet": "sherpa_onnx",
    "faster-whisper": "faster_whisper",
    "kokoro": "kokoro_onnx",
    "piper": "piper",
}
TTS_ENGINES: dict[str, TtsFactory] = {"kokoro": _kokoro, "piper": _piper}


def _unknown(kind: str, name: str, valid: list[str]) -> ConfigError:
    return ConfigError(f"unknown {kind} {name!r}; valid: {', '.join(sorted(valid))}")


def build_stt(settings: AssistantSettings, *, normalize: bool = False) -> SpeechToText:
    entry = STT_ENGINES.get(settings.stt_engine)
    if entry is None:
        raise _unknown("STT_ENGINE", settings.stt_engine, list(STT_ENGINES))
    factory, default_model, known = entry
    model = settings.stt_model or default_model
    if known and model not in known:
        raise _unknown(f"STT_MODEL for {settings.stt_engine}", model, list(known))
    return factory(settings, model, normalize)


def build_tts(settings: AssistantSettings) -> TextToSpeech:
    factory = TTS_ENGINES.get(settings.tts_engine)
    if factory is None:
        raise _unknown("TTS_ENGINE", settings.tts_engine, list(TTS_ENGINES))
    return factory(settings)


def model_dir_problems(settings: AssistantSettings) -> list[str]:
    """Missing model folders, for `assistant check` (no model is loaded)."""
    return [
        f"{name} {path} does not exist"
        for name, path in (
            ("STT_MODEL_DIR", settings.stt_model_dir),
            ("TTS_MODEL_DIR", settings.tts_model_dir),
        )
        if not Path(path).exists()
    ]


def engine_problems(settings: AssistantSettings) -> list[str]:
    """Unknown engine or model names, each message listing the valid ones."""
    found = []
    entry = STT_ENGINES.get(settings.stt_engine)
    if entry is None:
        found.append(str(_unknown("STT_ENGINE", settings.stt_engine, list(STT_ENGINES))))
    elif settings.stt_model and entry[2] and settings.stt_model not in entry[2]:
        found.append(str(_unknown("STT_MODEL", settings.stt_model, list(entry[2]))))
    if settings.tts_engine not in TTS_ENGINES:
        found.append(str(_unknown("TTS_ENGINE", settings.tts_engine, list(TTS_ENGINES))))
    return found
