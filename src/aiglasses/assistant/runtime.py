"""Build the live assistant from settings and run it against the glasses (or one WAV file)."""

import asyncio
import json
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeVar

from rich.console import Console
from rich.markup import escape

from aiglasses.assistant import local_server
from aiglasses.assistant.app import AssistantApp, load_persona, run_forever
from aiglasses.assistant.conversation import AssistantTurnRecord, ConversationPipeline
from aiglasses.assistant.echo import Debug, TurnLog
from aiglasses.assistant.escalation import EscalationRules
from aiglasses.assistant.glossary import Correction
from aiglasses.assistant.intents import Intents
from aiglasses.assistant.interfaces import LlmClient, SpeechToText, TextToSpeech
from aiglasses.assistant.lanes import LocalFirstClient
from aiglasses.assistant.llm_client import CloudVision, LangChainLlmClient
from aiglasses.assistant.local_llm import LocalLlm
from aiglasses.assistant.local_server import LocalServer
from aiglasses.assistant.memory import Memory
from aiglasses.assistant.models import Registry
from aiglasses.assistant.registry import build_stt, build_tts
from aiglasses.assistant.router import RoutedLlmClient
from aiglasses.assistant.search import build_search
from aiglasses.assistant.session import (
    load_glossary,
    make_sink,
    seconds_text,
    tts_options,
    utterance_from_wav,
    warm_up,
)
from aiglasses.assistant.speaker import NullSink, SubprocessSpeakerSink
from aiglasses.assistant.tools.base import ToolRegistry
from aiglasses.assistant.tools.device import DeviceHolder
from aiglasses.assistant.tools.factory import build_registry
from aiglasses.assistant.vision import ImageSource, build_vision
from aiglasses.assistant.wake import WakePhrases
from aiglasses.config import Settings
from aiglasses.domain.errors import ConfigError, GlassesError, NotSupportedError
from aiglasses.glasses import Glasses
from aiglasses.runtime_paths import HELPER_SCRIPT
from aiglasses.services.photo_fetch import (
    BleThumbnailImageSource,
    HelperWifiDirect,
    PhotoFetcher,
    SubprocessHelper,
    WifiDirectImageSource,
    enable_not_approved,
)
from aiglasses.settings import AssistantSettings

console = Console()
T = TypeVar("T")


@dataclass
class LocalFirstParts:
    """Everything the local-first client needs besides settings; built by `run_assistant`."""

    memory: Memory
    registry: ToolRegistry
    local: LocalLlm | None


def build_llm(
    assistant: AssistantSettings,
    *,
    force_role: str | None,
    no_router: bool,
    parts: LocalFirstParts | None = None,
) -> LlmClient:
    """The LLM the conversation talks to: the router around the roles, or one role forced."""
    problems = assistant.problems()
    if problems:
        raise ConfigError("; ".join(problems))
    if force_role is not None:
        return LangChainLlmClient(assistant.llm_role(force_role))
    main = LangChainLlmClient(assistant.llm_role("main"))
    if assistant.router_mode == "local_first" and not no_router:
        if parts is None:
            raise ConfigError("ROUTER_MODE=local_first needs the local parts (internal error)")
        return LocalFirstClient(
            assistant, local=parts.local, cloud=main, registry=parts.registry,
            intents=parts.registry.intents, rules=EscalationRules.load(), memory=parts.memory,
        )  # fmt: skip
    fast = LangChainLlmClient(assistant.llm_role("fast")) if assistant.fast_configured else None
    classifier = None
    if assistant.router_mode == "model" and not no_router:
        classifier = LangChainLlmClient(assistant.llm_role("router"))
    effective = assistant.model_copy(update={"router_mode": "off"}) if no_router else assistant
    if effective.router_mode == "local_first":
        effective = effective.model_copy(update={"router_mode": "off"})
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
    why = f" ({record.route_reason})" if record.route_reason else ""
    lane = f"lane {record.tier}" if record.tier is not None else "no lane"
    return (
        f"turn {record.turn}: {record.outcome}\n"
        f"  heard:  {record.words_in} words, speech to text {record.stt or '-'} "
        f"in {parts['stt_s']}\n"
        f"  brain:  {record.brain or '-'} ({lane}{why}), {record.chars_out} characters back\n"
        f"  tools:  {record.tools or 'none called'}\n"
        f"  voice:  {record.tts or '-'}, {record.clauses} clauses\n"
        f"  timing: end marker to sound {parts['end_to_sound_s']} = decode {parts['decode_s']} + "
        f"stt {parts['stt_s']} + router {parts['router_s']} + llm first token "
        f"{parts['llm_first_token_s']} + first audio {parts['first_audio_s']} + start "
        f"{parts['playback_start_s']}"
    )


def _show_heard(text: str) -> None:
    console.print(f"[cyan]you:[/] {escape(text)}")


def _show_said(text: str) -> None:
    console.print(f"[green]EVA:[/] {escape(text)}")


def make_pipeline(
    ble: Settings,
    assistant: AssistantSettings,
    *,
    stt: SpeechToText,
    tts: TextToSpeech,
    sink: SubprocessSpeakerSink | NullSink,
    llm: LlmClient,
    show_text: bool,
    memory: Memory | None = None,
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

    return ConversationPipeline(
        stt=stt, tts=tts, sink=sink, llm=llm, options=tts_options(assistant),
        settings=assistant, persona=load_persona(assistant), glossary=glossary,
        turn_log=TurnLog(ble.log_dir / "assistant_turns.jsonl"),
        debug=Debug(ble.captures_dir / "debug") if assistant.debug_save_audio else None,
        on_record=show, on_correction=log_correction if glossary is not None else None,
        memory=memory,
        on_heard=_show_heard if show_text else None,
        on_said=_show_said if show_text else None,
        wake=WakePhrases.load(assistant.wake_phrases_path),
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
    holder = DeviceHolder()
    # dry run refuses every write, even the CONFIRMED reads; open the guard only for device tools
    device_tools = any(
        t in assistant.enabled_tools
        for t in ("read_battery", "read_media_counts", "take_photo", "describe_scene")
    )
    memory = Memory(assistant.memory_max_turns, assistant.memory_ttl_s, assistant.memory_max_tokens)
    server: LocalServer | None = None
    local_mode = assistant.router_mode == "local_first" and not no_router and force_role is None
    parts: LocalFirstParts | None = None
    if local_mode:
        server = await _start_local_server(ble, assistant)
        base_url = server.base_url if server else f"http://127.0.0.1:{assistant.local_llm_port}"
        local = LocalLlm(
            base_url, first_token_timeout_s=assistant.local_first_token_timeout_s,
            prompt_cache=assistant.local_llm_prompt_cache,
        )  # fmt: skip
        parts = LocalFirstParts(memory, _registry(assistant, holder, memory, base_url), local)
    llm = build_llm(assistant, force_role=force_role, no_router=no_router, parts=parts)
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
        ble, assistant, stt=stt, tts=tts, sink=sink, llm=llm, show_text=assistant.show_transcripts,
        memory=memory if local_mode else None,
    )  # fmt: skip
    app = AssistantApp(pipeline, assistant)
    if isinstance(llm, LocalFirstClient):
        await llm.prime(pipeline._messages("hello")[0].content)
    try:
        if wav is not None:
            await app.handle(utterance_from_wav(wav))
            await app.runner.wait()
            return

        @asynccontextmanager
        async def open_glasses(on_start):  # type: ignore[no-untyped-def]
            async with Glasses.connect(ble, dry_run=not device_tools) as glasses:
                holder.bind(glasses)
                try:
                    yield glasses.voice_requests(on_start=on_start)
                finally:
                    holder.unbind()

        if assistant.startup_line:
            await pipeline.say_line(assistant.startup_line)
        console.print(
            f"{assistant.assistant_name}: STT {stt.name}, TTS {tts.name}, LLM {llm.name}. "
            "Click the rear button (don't hold), wait about 1 s, speak. Click again to interrupt. "
            "Press Q to quit."
        )
        end = await run_until_quit(
            run_forever(app, open_glasses, report=lambda m: console.print(f"[dim]{m}[/]"))
        )
        console.print("Stopped by you." if end is None else f"Session ended: {end.reason}.")
    finally:
        await app.close()
        if isinstance(sink, SubprocessSpeakerSink):
            await sink.close()
        if server is not None:
            await asyncio.to_thread(server.stop)


def _q_pressed_after(wait_s: float) -> bool:
    time.sleep(wait_s)
    return _q_pressed()


def _q_pressed() -> bool:
    """True when the Q key was pressed (Windows console); other keys are ignored."""
    try:
        import msvcrt
    except ImportError:
        return False
    return msvcrt.kbhit() and msvcrt.getwch().lower() == "q"


async def run_until_quit(work: Awaitable[T]) -> T | None:
    """Run `work` until it ends or the user presses Q (then it is cancelled and None returned)."""
    task = asyncio.ensure_future(work)

    async def watch() -> None:
        while not await asyncio.to_thread(_q_pressed_after, 0.2):
            pass

    watcher = asyncio.create_task(watch())
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        console.print("Stopping ...")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return None
    finally:
        watcher.cancel()
        task.cancel()
        await asyncio.gather(watcher, task, return_exceptions=True)


async def _start_local_server(ble: Settings, assistant: AssistantSettings) -> LocalServer | None:
    """Start and warm up llama-server inside a kill-on-close job. None means: use the cloud."""
    if assistant.local_llm_model is None or not assistant.local_llm_preload:
        return None
    server = LocalServer(
        assistant, Registry.load(_registry_path(assistant)), ble.log_dir / "llama-server.json"
    )
    console.print(f"Starting the local model ({assistant.local_llm_model}) ...")
    try:
        await asyncio.to_thread(
            server.start, auto_port=True, vision=assistant.vision_provider == "local"
        )
        await asyncio.to_thread(local_server.warm_up, server.port)
    except GlassesError as exc:
        console.print(f"[yellow]Local model unavailable ({exc}); using the cloud.[/]")
        await asyncio.to_thread(server.stop)
        return None
    return server


def _registry_path(assistant: AssistantSettings) -> Path | None:
    return assistant.model_registry_path if assistant.model_registry_path.exists() else None


def _registry(
    assistant: AssistantSettings, holder: DeviceHolder, memory: Memory, base_url: str
) -> ToolRegistry:
    enabled = assistant.enabled_tools
    search = build_search(assistant) if "search_web" in enabled else None
    vision = None
    source: ImageSource = BleThumbnailImageSource(
        holder, assistant.vision_capture, assistant.vision_capture_wait_s,
        Path("captures") / "thumbnails",
    )  # fmt: skip
    if assistant.wifi_direct_enabled:
        source = _wifi_direct_source(assistant, holder)
    if assistant.vision_provider == "local":
        vision = build_vision(assistant, base_url)
    elif assistant.vision_provider == "cloud" and assistant.vision_allow_cloud:
        vision = CloudVision(assistant.llm_role("main"), assistant.vision_max_image_side)
    return build_registry(
        assistant, device=holder, memory=memory, intents=Intents.load(assistant.intents_path),
        search=search, image_source=source, vision=vision,
    )  # fmt: skip


class _NoIpEvents:
    """The BLE IP event is not wired until the Wi-Fi Direct enable command is approved."""

    async def wait_ip(self, timeout: float) -> str:
        raise NotSupportedError("waiting for the glasses' IP is not wired yet")


def _wifi_direct_source(assistant: AssistantSettings, holder: DeviceHolder) -> ImageSource:
    helper = SubprocessHelper([str(assistant.wifi_direct_helper_python), "-I", str(HELPER_SCRIPT)])
    adapter = HelperWifiDirect(helper)
    fetcher = PhotoFetcher(
        take_photo=lambda: holder.take_photo(confirm=True),
        enable_discovery=enable_not_approved,  # a BLE write: needs the user's approval of the bytes
        wifi=adapter, host=adapter, events=_NoIpEvents(), save_dir=Path("captures") / "photos",
        peer_prefix="AIMB-G2",
    )  # fmt: skip
    return WifiDirectImageSource(fetcher)
