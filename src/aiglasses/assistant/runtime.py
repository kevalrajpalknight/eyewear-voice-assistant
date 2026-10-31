"""Build the live assistant from settings and run it against the glasses (or one WAV file)."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from aiglasses.assistant.app import AssistantApp, load_persona, run_forever
from aiglasses.assistant.conversation import AssistantTurnRecord, ConversationPipeline
from aiglasses.assistant.echo import Debug, TurnLog
from aiglasses.assistant.glossary import Correction
from aiglasses.assistant.interfaces import LlmClient, SpeechToText, TextToSpeech
from aiglasses.assistant.llm_client import LangChainLlmClient
from aiglasses.assistant.registry import build_stt, build_tts
from aiglasses.assistant.router import RoutedLlmClient
from aiglasses.assistant.session import (
    load_glossary,
    make_sink,
    seconds_text,
    tts_options,
    utterance_from_wav,
    warm_up,
)
from aiglasses.assistant.speaker import NullSink, SubprocessSpeakerSink
from aiglasses.config import Settings
from aiglasses.domain.errors import ConfigError
from aiglasses.glasses import Glasses
from aiglasses.settings import AssistantSettings

console = Console()


def build_llm(
    assistant: AssistantSettings, *, force_role: str | None, no_router: bool
) -> LlmClient:
    """The LLM the conversation talks to: the router around the roles, or one role forced."""
    problems = assistant.problems()
    if problems:
        raise ConfigError("; ".join(problems))
    if force_role is not None:
        return LangChainLlmClient(assistant.llm_role(force_role))
    main = LangChainLlmClient(assistant.llm_role("main"))
    fast = LangChainLlmClient(assistant.llm_role("fast")) if assistant.fast_configured else None
    classifier = None
    if assistant.router_mode == "model" and not no_router:
        classifier = LangChainLlmClient(assistant.llm_role("router"))
    effective = assistant.model_copy(update={"router_mode": "off"}) if no_router else assistant
    return RoutedLlmClient(effective, main, fast=fast, classifier=classifier)


def describe(record: AssistantTurnRecord) -> str:
    row = record.to_json()
    parts = {
        key: seconds_text(row[key])  # type: ignore[arg-type]
        for key in (
            "end_to_sound_s", "decode_s", "stt_s", "router_s", "llm_first_token_s",
            "first_audio_s", "playback_start_s",
        )
    }  # fmt: skip
    route = f"{record.route_role or '-'} ({record.route_reason or '-'})"
    return (
        f"turn {record.turn}: {record.outcome}; {record.words_in} words in, {record.chars_out} "
        f"characters out, {record.clauses} clauses; route {route}\n"
        f"  end marker to sound {parts['end_to_sound_s']} = decode {parts['decode_s']} + stt "
        f"{parts['stt_s']} + router {parts['router_s']} + llm first token "
        f"{parts['llm_first_token_s']} + first audio {parts['first_audio_s']} + start "
        f"{parts['playback_start_s']}"
    )


def make_pipeline(
    ble: Settings,
    assistant: AssistantSettings,
    *,
    stt: SpeechToText,
    tts: TextToSpeech,
    sink: SubprocessSpeakerSink | NullSink,
    llm: LlmClient,
    show_text: bool,
) -> ConversationPipeline:
    glossary = load_glossary(assistant)
    corrections_path = ble.log_dir / "glossary_corrections.jsonl"

    def log_correction(correction: Correction) -> None:
        corrections_path.parent.mkdir(parents=True, exist_ok=True)
        with corrections_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(correction)) + "\n")

    def show(record: AssistantTurnRecord) -> None:
        console.print(escape(describe(record)))
        if record.error:
            console.print(f"  [yellow]{escape(record.error)}[/]")
        if show_text and record.heard is not None:
            console.print(f"  heard: {escape(record.heard)}")
        if show_text and record.reply is not None:
            console.print(f"  said:  {escape(record.reply)}")

    return ConversationPipeline(
        stt=stt, tts=tts, sink=sink, llm=llm, options=tts_options(assistant),
        settings=assistant, persona=load_persona(assistant), glossary=glossary,
        turn_log=TurnLog(ble.log_dir / "assistant_turns.jsonl"),
        debug=Debug(ble.captures_dir / "debug") if assistant.debug_save_audio else None,
        on_record=show, on_correction=log_correction if glossary is not None else None,
    )  # fmt: skip


async def run_assistant(
    ble: Settings,
    assistant: AssistantSettings,
    *,
    output: str | None,
    force_role: str | None,
    no_router: bool,
    wav: Path | None,
) -> None:
    llm = build_llm(assistant, force_role=force_role, no_router=no_router)
    for warning in assistant.warnings():
        console.print(f"[yellow]{warning}[/]")
    console.print("Loading speech engines ...")
    stt = await asyncio.to_thread(build_stt, assistant)
    tts = await asyncio.to_thread(build_tts, assistant)
    await warm_up(stt, tts, tts_options(assistant))
    sink = make_sink(assistant, output)
    if isinstance(sink, SubprocessSpeakerSink):
        await sink.start()
    pipeline = make_pipeline(
        ble, assistant, stt=stt, tts=tts, sink=sink, llm=llm, show_text=assistant.log_transcripts
    )
    app = AssistantApp(pipeline, assistant)
    try:
        if wav is not None:
            await app.handle(utterance_from_wav(wav))
            await app.runner.wait()
            return

        @asynccontextmanager
        async def open_glasses(on_start):  # type: ignore[no-untyped-def]
            async with Glasses.connect(ble) as glasses:
                yield glasses.voice_requests(on_start=on_start)

        console.print(
            f"STT {stt.name}, TTS {tts.name}, LLM {llm.name}. Single-click the rear button "
            "(don't hold), wait about 1 s, speak. Click again while it talks to interrupt. "
            "Ctrl+C to stop."
        )
        end = await run_forever(app, open_glasses, report=lambda m: console.print(f"[dim]{m}[/]"))
        console.print(f"Session ended: {end.reason}.")
    finally:
        await app.close()
        if isinstance(sink, SubprocessSpeakerSink):
            await sink.close()
