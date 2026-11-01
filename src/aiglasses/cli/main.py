import asyncio
import contextlib
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Awaitable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from aiglasses.capabilities import CAPABILITIES
from aiglasses.cli.local import llm_server_app, models_app, thumbnail_app, wifi_direct_app
from aiglasses.config import Settings, save_device_address
from aiglasses.domain.errors import GlassesError
from aiglasses.domain.models import Confidence
from aiglasses.glasses import Glasses
from aiglasses.hexfmt import ascii_of
from aiglasses.logging_setup import setup_logging
from aiglasses.research.gatt_dump import dump_gatt, write_dump
from aiglasses.research.listen import Notification, listen
from aiglasses.services.audio import AudioService
from aiglasses.services.channel import FrameStat
from aiglasses.services.voice import VoiceWatch
from aiglasses.settings import AssistantSettings
from aiglasses.timefmt import both
from aiglasses.transport.audio_sd import SoundDeviceAudio
from aiglasses.transport.ble_scan import is_candidate, scan
from aiglasses.transport.tracing import TracingTransport

app = typer.Typer(no_args_is_help=True, help="AIMB-G2 AI glasses toolkit.")
video_app = typer.Typer(help="Video control.")
media_app = typer.Typer(help="Media on the glasses.")
audio_app = typer.Typer(help="Bluetooth audio.")
wifi_app = typer.Typer(help="Glasses Wi-Fi link.")
capture_app = typer.Typer(help="Official-app capture analysis.")
voice_app = typer.Typer(help="Offline experiments on the voice stream.")
ai_voice_app = typer.Typer(help="AI voice button (read-only).")
assistant_app = typer.Typer(help="Voice assistant (reads the BLE voice stream; sends nothing).")
apk_app = typer.Typer(help="Static analysis of the official app (read-only).")
for name, sub in (
    ("video", video_app),
    ("media", media_app),
    ("audio", audio_app),
    ("wifi", wifi_app),
    ("capture", capture_app),
    ("voice", voice_app),
    ("ai-voice", ai_voice_app),
    ("assistant", assistant_app),
    ("apk", apk_app),
    ("models", models_app),
    ("llm-server", llm_server_app),
    ("wifi-direct", wifi_direct_app),
    ("thumbnail", thumbnail_app),
):
    app.add_typer(sub, name=name)

console = Console()
APK_ARGUMENT = typer.Argument(..., help="Path to the APK")
TRIAGE_ARGUMENT = typer.Argument(..., help="Bug report .zip files, or a folder of them")
LOG_ARGUMENT = typer.Argument(..., help="btsnoop_hci.log or an Android bugreport .zip")
T = TypeVar("T")


def _run(coro: Awaitable[T]) -> T:
    """Run a coroutine; library errors and Ctrl+C become clean exits (cleanup runs in finally)."""

    async def runner() -> T:
        return await coro

    try:
        return asyncio.run(runner())
    except GlassesError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt as exc:
        console.print("[yellow]Interrupted; disconnected.[/]")
        raise typer.Exit(130) from exc


def _stub(feature: str, phase: int) -> None:
    console.print(f"[yellow]{feature}: not implemented until Phase {phase}[/]")
    raise typer.Exit(2)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    assistant = AssistantSettings()
    secrets = tuple(
        key.get_secret_value()
        for key in (
            assistant.llm_api_key, assistant.llm_fast_api_key, assistant.router_model_api_key
        )
        if key is not None
    )  # fmt: skip
    setup_logging(Settings().log_dir, verbose, assistant.log_level, secrets)


@app.command("scan")
def scan_cmd(
    timeout: float = typer.Option(None, help="Seconds to scan (default from config)"),
    select: bool = typer.Option(True, help="Prompt to save a target address"),
) -> None:
    """Scan for BLE devices and optionally save the glasses address to config.toml."""
    settings = Settings()
    results = _run(scan(timeout or settings.scan_timeout))

    table = Table(show_lines=True)
    for col in (
        "#",
        "Name",
        "Address",
        "RSSI",
        "Service UUIDs",
        "Manufacturer data",
        "Service data",
    ):
        table.add_column(col)
    candidates: list[int] = []
    for i, r in enumerate(results):
        cand = is_candidate(r, settings.name_prefix)
        if cand:
            candidates.append(i)
        table.add_row(
            str(i),
            r.name or "-",
            r.address,
            str(r.rssi),
            "\n".join(r.service_uuids),
            "\n".join(f"{k:#06x}: {v.hex()}" for k, v in r.manufacturer_data.items()),
            "\n".join(f"{k}: {v.hex()}" for k, v in r.service_data.items()),
            style="bold green" if cand else None,
        )
    console.print(table)
    console.print(f"{len(results)} devices, {len(candidates)} likely candidates (green).")
    if not results:
        console.print("[red]Nothing found.[/] Toggle glasses Bluetooth and retry.")
        raise typer.Exit(1)
    if not select:
        return
    idx = typer.prompt("Target index", default=candidates[0] if candidates else None, type=int)
    if not 0 <= idx < len(results):
        console.print("[red]Index out of range[/]")
        raise typer.Exit(1)
    save_device_address(results[idx].address)
    console.print(f"Saved {results[idx].address} to config.toml")


@app.command()
def capabilities() -> None:
    """Show which features are CONFIRMED on this unit."""
    table = Table()
    table.add_column("Capability")
    table.add_column("Status")
    colors = {
        Confidence.CONFIRMED: "green",
        Confidence.INFERRED: "yellow",
        Confidence.UNVERIFIED: "red",
    }
    for cap, status in CAPABILITIES.items():
        table.add_row(cap.value, f"[{colors[status]}]{status.value}[/]")
    console.print(table)


@app.command()
def info() -> None:
    """Device name, hardware and firmware (read-only)."""

    async def go() -> None:
        settings = Settings()
        async with Glasses.connect(settings) as g:
            d = await g.device.info()
        console.print(f"name: {escape(str(d.name))}")
        console.print(f"address: {d.address}")
        console.print(f"hardware: {escape(str(d.hardware))}")
        console.print(f"firmware: {escape(str(d.firmware))}")
        console.print(f"serial: {d.serial or '(empty)'}")

    _run(go())


@app.command("gatt-dump")
def gatt_dump(
    out: Path = typer.Option(None, help="Output file (default logs/gatt_dump.json)"),
) -> None:
    """Dump all services, characteristics and readable values to JSON (read-only)."""

    async def go() -> None:
        settings = Settings()
        async with Glasses.connect(settings) as g:
            data = await dump_gatt(g.transport)
        path = out or settings.log_dir / "gatt_dump.json"
        write_dump(path, data)
        chars = sum(len(s["characteristics"]) for s in data["services"])
        console.print(
            f"{len(data['services'])} services, {chars} characteristics, MTU {data['mtu']}"
        )
        console.print(f"Saved {path}")

    _run(go())


def _start_mark_reader(transport: TracingTransport) -> None:
    def read() -> None:
        for line in sys.stdin:
            label = line.strip()
            if label:
                transport.mark(label)
                console.print(f"[cyan]marked: {escape(label)}[/]")

    threading.Thread(target=read, daemon=True).start()


@voice_app.command("candidates")
def voice_candidates(
    trace: Path = typer.Option(None, help="trace.jsonl (default logs/trace.jsonl)"),
    burst: int = typer.Option(-1, help="Burst index; -1 is the latest"),
    out_dir: Path = typer.Option(None, help="Default logs/decode_candidates"),
) -> None:
    """Try several decodings of one 0x59 voice burst and write one WAV per attempt (offline)."""
    from aiglasses.protocol.bcframe import crc16_modbus
    from aiglasses.protocol.voice import voice_bursts
    from aiglasses.research.trace import load_frames
    from aiglasses.research.voice_decode import run_candidates

    settings = Settings()
    path = trace or settings.log_dir / "trace.jsonl"
    bursts = voice_bursts(load_frames(path))
    if not bursts:
        console.print("[red]No voice bursts in the trace.[/]")
        raise typer.Exit(1)
    packets = bursts[burst]
    console.print(f"{len(bursts)} bursts; using #{burst % len(bursts)} with {len(packets)} packets")
    sbc_hits = sum(1 for p in packets if p[0] in (0x9C, 0xAD))
    console.print(f"SBC/mSBC sync byte (0x9C/0xAD) at packet start: {sbc_hits}/{len(packets)}")
    bodies = [crc16_modbus(p).to_bytes(2, "little") + p for p in packets]
    target_dir = out_dir or settings.log_dir / "decode_candidates"
    results = run_candidates(bodies, f"burst{burst % len(bursts)}", target_dir)
    table = Table(title="Decode attempts")
    for col in ("Attempt", "Frames ok", "Errors", "Seconds", "RMS", "Peak", "File / note"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.name, str(r.decoded_frames), str(r.errors), f"{r.seconds:.2f}", f"{r.rms:.1f}",
            f"{r.peak:.0f}", escape(str(r.path) if r.path else r.note),
        )  # fmt: skip
    console.print(table)


@app.command("listen")
def listen_cmd(
    duration: float = typer.Option(None, help="Seconds to listen (default from config)"),
) -> None:
    """Subscribe to all notify/indicate characteristics and print what arrives (no writes)."""

    def show(n: Notification) -> None:
        stamp = both(datetime.fromtimestamp(n.ts, timezone.utc))
        console.print(
            f"{stamp} h={n.handle} {n.uuid[:8]} {n.data.hex()} '{escape(ascii_of(n.data))}'"
        )

    async def go() -> None:
        settings = Settings()
        seconds = duration or settings.listen_seconds
        async with Glasses.connect(settings) as g:
            console.print(f"Listening {seconds:.0f}s. Press buttons, tap, say the wake word...")
            if isinstance(g.transport, TracingTransport):
                console.print("Type a label (e.g. 'photo') and press Enter as you do each action.")
                _start_mark_reader(g.transport)
            result = await listen(g.transport, seconds, on_notification=show)
        console.print(f"{len(result.subscribed)} subscribed, {len(result.skipped)} skipped")
        for uuid, why in result.skipped.items():
            console.print(f"[yellow]skipped {uuid}: {escape(why)}[/]")
        table = Table(title="Raw summary")
        for col in ("UUID", "Count", "Distinct payloads (hex)"):
            table.add_column(col)
        by_uuid: dict[str, list[bytes]] = {}
        for n in result.notifications:
            by_uuid.setdefault(n.uuid, []).append(n.data)
        for uuid, payloads in by_uuid.items():
            table.add_row(uuid, str(len(payloads)), "\n".join(sorted({p.hex() for p in payloads})))
        console.print(table if by_uuid else "No notifications received.")

    _run(go())


def _print_frame_summary(stats: dict[str, FrameStat], watch: VoiceWatch) -> None:
    def moment(value: float | None) -> str:
        return "not seen" if value is None else both(datetime.fromtimestamp(value, timezone.utc))

    table = Table(title="Notification frames since connecting")
    for col in ("Type", "Count", "First", "Last"):
        table.add_column(col)
    for label, stat in sorted(stats.items()):
        first = datetime.fromtimestamp(stat.first, timezone.utc)
        last = datetime.fromtimestamp(stat.last, timezone.utc)
        table.add_row(label, str(stat.count), both(first), both(last))
    console.print(table if stats else "No notification frames arrived at all.")
    if "voice_frame" not in stats:
        console.print("[yellow]No 0x59 voice frames arrived.[/]")
    console.print(f"start marker (03 01): {moment(watch.start_marker_at)}")
    console.print(f"end marker (0a 01): {moment(watch.end_marker_at)}")


def parse_stt_option(value: str | None) -> dict[str, str]:
    """`--stt ENGINE` or `--stt ENGINE:MODEL` overrides STT_ENGINE and STT_MODEL for this run."""
    if not value:
        return {}
    engine, _, model = value.partition(":")
    return {"stt_engine": engine, **({"stt_model": model} if model else {})}


@assistant_app.command("echo")
def assistant_echo(
    output: str = typer.Option(None, help="null = no sound (latency test); default: AUDIO_OUTPUT"),
    stt: str = typer.Option(None, help="ENGINE or ENGINE:MODEL; default: STT_ENGINE / STT_MODEL"),
    tts_voice: str = typer.Option(None, help="Default: TTS_VOICE"),
    speed: float = typer.Option(None, help="Default: TTS_SPEED"),
    normalize: bool = typer.Option(False, help="Peak-normalise the input before STT"),
    debug: bool = typer.Option(
        False,
        help="Keep transcripts (console and log) and save each input WAV under captures/debug",
    ),
    wav: Path = typer.Option(
        None, help="Run ONE turn from a 16 kHz mono WAV instead of the glasses"
    ),
    glossary: bool = typer.Option(
        False,
        "--glossary",
        help="Correct names with GLOSSARY_PATH; prints raw and corrected text and logs "
        "every correction to logs/glossary_corrections.jsonl",
    ),
) -> None:
    """Rear click -> speech to text -> speak it back. No LLM, no network, nothing sent."""
    from aiglasses.assistant.session import run_echo

    if output not in (None, "pc", "null"):
        raise typer.BadParameter("--output must be pc or null")
    overrides: dict[str, object] = {**parse_stt_option(stt)}
    overrides.update({k: v for k, v in (("tts_voice", tts_voice), ("tts_speed", speed)) if v})
    if glossary:
        overrides["glossary_enabled"] = True
    assistant = AssistantSettings().model_copy(update=overrides)
    _run(run_echo(Settings(), assistant, output=output, normalize=normalize, debug=debug, wav=wav))


@assistant_app.command("run")
def assistant_run(
    output: str = typer.Option(None, help="null = no sound; default: AUDIO_OUTPUT"),
    stt: str = typer.Option(None, help="ENGINE or ENGINE:MODEL; default: STT_ENGINE / STT_MODEL"),
    llm_role: str = typer.Option(
        None, help="Force one LLM role (main or fast), skipping the router"
    ),
    no_router: bool = typer.Option(False, help="Always use the main model (local answers stay)"),
    wav: Path = typer.Option(
        None, help="Run ONE turn from a 16 kHz mono WAV instead of the glasses"
    ),
) -> None:
    """Rear click -> speech to text -> LLM -> speech. Needs .env (see `assistant check`)."""
    from aiglasses.assistant.runtime import run_assistant

    if output not in (None, "pc", "null"):
        raise typer.BadParameter("--output must be pc or null")
    if llm_role not in (None, "main", "fast"):
        raise typer.BadParameter("--llm-role must be main or fast")
    assistant = AssistantSettings().model_copy(update=parse_stt_option(stt))
    _run(
        run_assistant(
            Settings(), assistant, output=output, force_role=llm_role, no_router=no_router, wav=wav
        )
    )


@assistant_app.command("check")
def assistant_check(
    live: bool = typer.Option(
        False,
        "--live",
        help="Make one neutral call per configured LLM role (LIVE_CALL_BUDGET in all)",
    ),
    live_tools: bool = typer.Option(
        False, "--live-tools", help="Make ONE neutral web search (counts against the live budget)"
    ),
    reset_live_budget: bool = typer.Option(
        False, "--reset-live-budget", help="Set the live-call counter back to zero, then exit"
    ),
) -> None:
    """Validate settings, models, packages and audio. Secrets show only as set / NOT set."""
    from aiglasses.assistant import check as check_module

    log_dir = Settings().log_dir
    if reset_live_budget:
        check_module.reset_live_budget(log_dir / "live_calls.count")
        console.print("Live-call counter reset to zero.")
        raise typer.Exit(0)
    code = check_module.run_check(
        AssistantSettings(), live=live, log_dir=log_dir, console=console, live_tools=live_tools
    )
    raise typer.Exit(code)


@assistant_app.command("diagnose")
def assistant_diagnose(
    phrase: list[str] = typer.Argument(None, help="Phrases to trace; default: a sample set"),
) -> None:
    """Offline: trace each phrase (lane, intent, tool, enabled) and where it stops."""
    from aiglasses.assistant.diagnose import SAMPLES, diagnose

    rows = _run(diagnose(AssistantSettings(), tuple(phrase) if phrase else SAMPLES))
    table = Table(title="Phrase trace (fakes only, nothing is sent)", show_lines=True)
    for column in ("Phrase", "Lane", "Intent -> tool", "Enabled", "Stopped at", "Would say"):
        table.add_column(column, overflow="fold")
    for r in rows:
        route = f"{r.intent} -> {r.tool} {r.args or ''}" if r.intent else (r.handler or "-")
        enabled = "-" if r.enabled is None else ("yes" if r.enabled else "NO")
        notes = "; ".join(r.checks)
        table.add_row(
            r.phrase,
            r.lane,
            route,
            enabled,
            r.stopped_at + (f" ({notes})" if notes else ""),
            r.spoken,
        )
    console.print(table)


@app.command("wake-test")
def wake_test(
    seconds: float = typer.Option(60.0, help="How long to listen"),
    hfp: bool = typer.Option(False, help="Afterwards probe the headset microphone (you speak)"),
) -> None:
    """Read-only: list every voice-session start for a while, with no button and no phone.

    Say the glasses' own wake phrase ("Hey Glasses") without touching them and watch whether a
    session appears. Sends nothing (the write guard stays in dry-run mode).
    """
    settings = Settings()

    async def listen() -> int:
        count = 0
        async with Glasses.connect(settings) as glasses:
            console.print(
                f"Connected. Listening for {seconds:g} s. Do NOT touch the glasses; say the wake "
                "phrase. Nothing is sent."
            )

            async def consume() -> None:
                nonlocal count
                async for utterance in glasses.voice_requests():
                    count += 1
                    started = datetime.fromtimestamp(utterance.started_at, timezone.utc)
                    marker = "seen" if utterance.start_marker_at else "not seen"
                    console.print(
                        f"session {count}: started {both(started)}, {utterance.seconds:.1f} s of "
                        f"audio, ended by {utterance.end_reason.value}, start marker {marker}"
                    )

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(consume(), seconds)
        return count

    found = _run(listen())
    console.print(
        f"{found} voice session(s) started. If you did not touch the glasses, the wake phrase "
        "starts a BLE voice session on its own (INFERRED to be possible; this is the test)."
        if found
        else "No voice session started. The wake phrase did not start one (or was not heard)."
    )
    if hfp:
        subprocess.run([sys.executable, "-m", "aiglasses.research.hfp_probe"], check=False)


@app.command("bench-record")
def bench_record(
    out_dir: Path = typer.Option(Path("bench/data"), help="Local and gitignored"),
    glossary_file: Path = typer.Option(
        None, help="Private glossary (one term per line): adds 10 short sentences using your terms"
    ),
) -> None:
    """Guided recorder for the STT accuracy test: read a sentence, single-click the rear button.

    Sentences that already have a saved take are skipped, so a re-run continues.
    """
    from aiglasses.assistant.glossary import Glossary
    from aiglasses.assistant.recorder import SHORT_SENTENCES, glossary_sentences, pending
    from aiglasses.assistant.session import SENTENCES, run_bench_record

    extra = []
    if glossary_file is not None:
        terms = [t.canonical for t in Glossary.load(glossary_file).terms]
        extra = glossary_sentences(terms)
    todo = pending([*SENTENCES, *SHORT_SENTENCES, *extra], out_dir)
    if not todo:
        console.print("Every sentence already has a take.")
        return
    _run(run_bench_record(Settings(), out_dir, todo))


@ai_voice_app.command("record")
def ai_voice_record(
    wait: float = typer.Option(60.0, help="Seconds to wait for you to hold the rear click"),
    out_dir: Path = typer.Option(None, help="Default captures/"),
) -> None:
    """Wait for one AI voice burst, decode it and save a WAV. Sends nothing.

    SINGLE-CLICK the rear button (do not hold), wait about 2 s, then speak. A LONG press starts an
    audio-only recording on the glasses.
    """
    from aiglasses.services.voice import record_burst

    settings = Settings()
    target_dir = out_dir or Path("captures")

    async def go() -> None:
        async with Glasses.connect(settings) as g:
            await g.channel.start()
            console.print(
                f"Connected; notifications active: {g.channel.active}. Within {wait:.0f}s "
                "single-click the rear button (don't hold), wait about 2 s, then speak ..."
            )
            watch = VoiceWatch()
            try:
                burst = await record_burst(g.events(), wait_seconds=wait, watch=watch)
            finally:
                _print_frame_summary(g.channel.stats, watch)
        started = datetime.fromtimestamp(burst.started_at, timezone.utc)
        console.print(
            f"burst at {both(started)}: {len(burst.packets)} packets, "
            f"{burst.seconds:.2f}s of audio, "
            f"ended by {burst.ended_by}"
        )
        import soundfile as sf

        from aiglasses.research.voice_decode import APP_SAMPLE_RATE, OpusVoiceDecoder

        audio = OpusVoiceDecoder().decode(burst.packets)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"ai_voice_{started.strftime('%Y%m%dT%H%M%SZ')}.wav"
        sf.write(path, audio.samples, APP_SAMPLE_RATE, subtype="PCM_16")
        console.print(f"saved {path} ({len(audio.samples) / APP_SAMPLE_RATE:.2f}s, 16 kHz mono)")
        console.print("[yellow]Codec stays UNVERIFIED until you confirm it is intelligible.[/]")

    _run(go())


@app.command()
def battery() -> None:
    """Battery level."""
    _stub("battery", 3)


@app.command()
def photo() -> None:
    """Take ONE photo (changes the glasses' state). Reads counts first; asks you to confirm."""
    typer.confirm(
        "A photo will be taken and stored on the glasses. Are you watching the glasses, with the "
        "Cyan app closed and your phone's Bluetooth off?",
        abort=True,
    )

    async def go() -> None:
        async with Glasses.connect(Settings(), dry_run=False) as g:
            result = await g.take_photo(confirm=True)
        before = result.before
        console.print(
            f"before: photos {before.photos}, videos {before.videos}, audio {before.audio}"
        )
        if result.after is None:
            console.print("[yellow]no media-count event arrived after the photo[/]")
        else:
            after = result.after
            console.print(
                f"after:  photos {after.photos}, videos {after.videos}, audio {after.audio}"
            )
            console.print(f"photos added: {result.photos_added}")
        console.print(f"reply {result.reply_hex}: {escape(result.reply)}")

    _run(go())


@video_app.command("start")
def video_start() -> None:
    _stub("video start", 4)


@video_app.command("stop")
def video_stop() -> None:
    _stub("video stop", 4)


@media_app.command("list")
def media_list() -> None:
    _stub("media list", 4)


@media_app.command("pull")
def media_pull() -> None:
    _stub("media pull", 4)


def _audio(settings: Settings) -> AudioService:
    return AudioService(SoundDeviceAudio(), settings.audio_name_hint)


@audio_app.command("devices")
def audio_devices(
    all_endpoints: bool = typer.Option(False, "--all", help="Show every WASAPI endpoint"),
) -> None:
    """List the glasses' Windows audio endpoints (A2DP speaker, HFP microphone)."""
    settings = Settings()
    service = _audio(settings)
    endpoints = service.endpoints("" if all_endpoints else None)
    table = Table()
    for col in ("Index", "Kind", "Profile (guessed)", "Name", "Rate", "Channels"):
        table.add_column(col)
    for e in endpoints:
        table.add_row(
            str(e.index), e.kind, e.profile, escape(e.name), str(e.sample_rate), str(e.channels)
        )
    console.print(
        table if endpoints else "No matching endpoints. Pair the glasses in Windows Settings first."
    )


@audio_app.command("play")
def audio_play(
    file: Path = typer.Option(None, help="WAV/FLAC file to play (default: a test tone)"),
    freq: float = typer.Option(440.0, help="Tone frequency in Hz"),
    seconds: float = typer.Option(1.0, help="Tone length"),
    volume: float = typer.Option(0.15, min=0.0, max=0.5, help="Tone volume (0-0.5)"),
    endpoint: int = typer.Option(None, help="Endpoint index from `audio devices`"),
) -> None:
    """Play a short tone or file on the glasses speaker."""
    service = _audio(Settings())

    async def go() -> None:
        if file:
            target = await service.play_file(file, endpoint)
        else:
            target = await service.play_tone(freq, seconds, volume, endpoint)
        console.print(
            f"Played on {escape(target.name)} ({target.sample_rate} Hz, {target.channels} ch)"
        )

    _run(go())


@audio_app.command("record")
def audio_record(
    seconds: float = typer.Option(5.0, help="Recording length"),
    out: Path = typer.Option(None, help="Output WAV (default captures/mic_test.wav)"),
    endpoint: int = typer.Option(None, help="Endpoint index from `audio devices`"),
) -> None:
    """Record from the glasses microphone (HFP, low bandwidth)."""
    settings = Settings()
    service = _audio(settings)

    async def go() -> None:
        console.print(f"Recording {seconds:.0f}s from the glasses microphone. Speak now...")
        stats = await service.record(
            seconds, out or settings.captures_dir / "mic_test.wav", endpoint
        )
        console.print(f"Saved {stats.path} ({stats.sample_rate} Hz mono, {stats.seconds:.1f}s)")
        console.print(f"peak {stats.peak:.4f}, rms {stats.rms:.5f} (near 0 means silence)")

    _run(go())


@wifi_app.command("up")
def wifi_up() -> None:
    _stub("wifi up", 4)


@wifi_app.command("down")
def wifi_down() -> None:
    _stub("wifi down", 4)


@wifi_app.command("probe")
def wifi_probe() -> None:
    _stub("wifi probe", 4)


@capture_app.command("analyze")
def capture_analyze(
    log: Path = LOG_ARGUMENT,
    address: str = typer.Option(None, help="Glasses BLE address (default: config device_address)"),
    gatt_dump: Path = typer.Option(None, help="GATT dump for handle names"),
    gap: float = typer.Option(6.0, help="Quiet seconds that start a new action segment"),
    actions: str = typer.Option(None, help="Comma-separated action names, in order"),
    out_dir: Path = typer.Option(None, help="Where to write analysis.md/.csv (default captures)"),
) -> None:
    """Action-vs-bytes table for ATT and RFCOMM traffic to or from the glasses (read-only)."""
    from aiglasses.research.btsnoop import read_btsnoop
    from aiglasses.research.capture import (
        TO_GLASSES,
        analyze_records,
        find_known,
        load_handle_names,
        reactions,
        segment,
    )
    from aiglasses.research.report import write_reports

    settings = Settings()

    async def go() -> None:
        names = load_handle_names(gatt_dump or settings.log_dir / "gatt_dump.json")
        records = read_btsnoop(log)
        if not records:
            console.print(
                "[red]The Bluetooth log is valid but contains 0 packets.[/] "
                "Likely cause (not confirmed): the snoop log is buffered and only written out "
                "when Bluetooth is switched off. Switch Bluetooth off for a few seconds right "
                "after your last action, then take the bug report. See docs/CAPTURE.md."
            )
            raise typer.Exit(1)
        analysis = analyze_records(records, address or settings.device_address, names)
        segs = segment(analysis.rows, gap)
        found = max(segs) + 1 if segs else 0
        labels = [a.strip() for a in actions.split(",")] if actions else []
        for warning in analysis.warnings:
            console.print(f"[yellow]{escape(warning)}[/]")
        if labels and len(labels) != found:
            console.print(
                f"[yellow]{found} segments but {len(labels)} actions; labels not applied. "
                "Adjust --gap.[/]"
            )
            labels = []
        table = Table(title=f"{len(analysis.rows)} events to/from the glasses")
        for col in ("Seg", "Action", "Time", "Dir", "Ch", "Op", "Target", "Hex / decoded"):
            table.add_column(col, overflow="fold")
        first = analysis.rows[0].ts if analysis.rows else 0.0
        for row, seg in zip(analysis.rows, segs, strict=True):
            detail = row.decoded or row.hex
            if row.count > 1:
                detail += f" x{row.count} over {row.end_ts - row.ts:.1f}s"
            table.add_row(
                str(seg),
                labels[seg] if labels else "",
                f"+{row.ts - first:.2f}",
                "->" if row.direction == TO_GLASSES else "<-",
                row.channel,
                row.op,
                escape(row.target),
                escape(detail),
            )
        console.print(table)
        console.print(f"Connections: {analysis.connections}  L2CAP by PSM: {analysis.psm_counts}")
        for event, writes in reactions(analysis.rows):
            reply = ", ".join(f"{w.target}={w.hex}" for w in writes) or "(no writes)"
            console.print(f"after {escape(event.decoded)}: {escape(reply)}")
        for hit in find_known(analysis.rows):
            kind = "STRONG" if hit.strong else "weak"
            direction = "->" if hit.row.direction == TO_GLASSES else "<-"
            console.print(
                f"known value [{kind}] {escape(hit.name)} at byte {hit.offset} in {direction} "
                f"{escape(hit.row.target)} +{hit.row.ts - first:.2f}s: {hit.row.hex}"
            )
        md_path, csv_path = write_reports(analysis, segs, labels, out_dir or settings.captures_dir)
        console.print(f"Wrote {md_path} and {csv_path}")

    _run(go())


@capture_app.command("triage")
def capture_triage(
    paths: list[Path] = TRIAGE_ARGUMENT,
) -> None:
    """What a phone bug report says about the Cyan app and the glasses (no Bluetooth log needed)."""
    from aiglasses.research.bugreport import triage_zip

    zips = sorted(z for p in paths for z in (p.glob("*.zip") if p.is_dir() else [p]))
    if not zips:
        console.print("[red]No .zip files found.[/]")
        raise typer.Exit(1)
    for zip_path in zips:
        result = triage_zip(zip_path)
        console.print(f"\n[bold]{escape(result.zip_name)}[/]")
        for name, size in result.snoop_files:
            note = "EMPTY (header only)" if size <= 16 else f"{size} bytes"
            console.print(f"  Bluetooth log: {escape(name)} {note}")
        if not result.snoop_files:
            console.print("  Bluetooth log: none in this report")
        for item in result.gatt_connections:
            console.print(f"  BLE connection by the app: {escape(item)}")
        for line in result.audio_events:
            console.print(f"  audio: {escape(line)}")
        for time, what in result.p2p_events:
            console.print(f"  Wi-Fi Direct: {time} {escape(what)}")
        group = result.p2p_group
        if group.network:
            console.print(
                f"  Wi-Fi Direct group: network '{escape(group.network)}', phone is group owner: "
                f"{group.phone_is_group_owner}, owner IP {group.group_owner_ip}, "
                f"glasses P2P address {group.glasses_p2p_address or '?'}"
            )
        if result.remote_endpoints:
            console.print(f"  app network endpoints: {sorted(result.remote_endpoints)}")
        resumed = [e for e in result.app_events if e[1] == "ACTIVITY_RESUMED"]
        for time, _kind, cls in resumed:
            console.print(f"  app screen opened: {time} {escape(cls)}")


@apk_app.command("packages")
def apk_packages(
    apk: Path = APK_ARGUMENT,
    prefix: str = typer.Option("com.jieli", help="Dotted package prefix"),
    depth: int = typer.Option(4, help="Package segments to group by"),
) -> None:
    """Count classes per package under a prefix (names only)."""
    from aiglasses.research.dexstrings import apk_strings, class_packages

    total: Counter[str] = Counter()
    for _name, strings in apk_strings(apk):
        total.update(class_packages(strings, prefix, depth))
    for package, count in sorted(total.items()):
        console.print(f"{count:5d}  {package}")
    console.print(f"{sum(total.values())} classes under {prefix}")


@apk_app.command("search")
def apk_search(
    apk: Path = APK_ARGUMENT,
    pattern: str = typer.Argument(..., help="Regular expression (case-insensitive)"),
    limit: int = typer.Option(40, help="Maximum matches to print"),
    where: str = typer.Option(None, help="Only show strings whose class path contains this text"),
) -> None:
    """List string-table entries matching a pattern (class names, constants)."""
    import re

    from aiglasses.research.dexstrings import apk_strings

    rx = re.compile(pattern, re.IGNORECASE)
    shown = 0
    seen: set[str] = set()
    for dex, strings in apk_strings(apk):
        for s in strings:
            if s in seen or not rx.search(s) or (where and where not in s):
                continue
            seen.add(s)
            if shown < limit:
                console.print(f"{dex}  {escape(s[:200])}")
            shown += 1
    console.print(
        f"{shown} distinct matches" + (f" (first {limit} shown)" if shown > limit else "")
    )


@apk_app.command("methods")
def apk_methods(
    apk: Path = APK_ARGUMENT,
    class_pattern: str = typer.Argument(
        ..., help="Regex on the class descriptor, e.g. LargeDataHandler"
    ),
    max_classes: int = typer.Option(12, help="Maximum classes to print"),
) -> None:
    """List method names declared or called on matching classes (names only)."""
    import re

    from aiglasses.research.dexstrings import apk_method_names

    rx = re.compile(class_pattern, re.IGNORECASE)
    shown = 0
    for cls, names in sorted(apk_method_names(apk).items()):
        if not rx.search(cls):
            continue
        shown += 1
        if shown <= max_classes:
            console.print(f"[bold]{escape(cls)}[/] ({len(names)})")
            console.print("  " + escape(", ".join(sorted(names))))
    console.print(f"{shown} matching classes")


@app.command("probe")
def probe_cmd(
    name: str = typer.Argument(..., help="An approved probe: see `aiglasses probe --help`"),
    send: bool = typer.Option(False, "--send", help="Actually send. Without it nothing is sent."),
    known_photos: int = typer.Option(
        None, help="Photo count seen earlier, if none arrives on connect"
    ),
) -> None:
    """Send ONE approved request and show the reply. Dry run unless --send. Never repeated."""
    from aiglasses.protocol.registry import load_commands
    from aiglasses.research.probe import APPROVED_FRAMES, REPLY_TIMEOUT_SECONDS, run_probe

    if name not in APPROVED_FRAMES:
        console.print(
            f"[red]{escape(name)} is not approved. Approved: {sorted(APPROVED_FRAMES)}.[/]"
        )
        raise typer.Exit(2)
    command = next((c for c in load_commands() if c.name == name), None)
    if command is None or command.payload.hex() != APPROVED_FRAMES[name]:
        console.print("[red]The registry entry does not match the approved bytes; refusing.[/]")
        raise typer.Exit(2)
    console.print(
        f"{name}: write {command.payload.hex()} to {command.characteristic} (no response)"
    )
    if not send:
        console.print("Dry run: nothing sent. Add --send to send once.")
        return
    from aiglasses.research.probe import STATE_CHANGING

    question = "Is the Cyan app closed and your phone's Bluetooth off, and are you ready?"
    if name in STATE_CHANGING:
        question = (
            f"{name} CHANGES the glasses' state (a photo is taken and stored). "
            "Are you watching the glasses, with the app closed and phone Bluetooth off?"
        )
    typer.confirm(question, abort=True)
    settings = Settings()

    async def go() -> None:
        async with Glasses.connect(settings, dry_run=False) as g:
            result = await run_probe(
                g.transport,
                g.guard,
                command,
                settings.log_dir / "probe_audit.jsonl",
                known_photo_count=known_photos,
            )
        console.print(f"notifications active before the write: {result.notifications_active}")
        console.print(f"link up immediately before the write: {result.link_up_before_write}")
        console.print(f"sent: {result.sent} (once, timeout {REPLY_TIMEOUT_SECONDS:.0f}s)")
        console.print(f"baseline for counts: {result.baseline_source}")
        for r in result.replies:
            mark = "reply" if r.expected else "other"
            when = both(datetime.fromisoformat(r.time))
            console.print(f"  {when} [{mark}] {r.hex}  {escape(r.decoded)}")
        for note in result.notes:
            console.print(f"[yellow]note: {escape(note)}[/]")
        console.print(f"verdict: {escape(result.verdict)}")
        console.print("Next run: wait about 30 s; the glasses need time to advertise again.")

    _run(go())


@wifi_app.command("preflight")
def wifi_preflight() -> None:
    """Report the PC's current Wi-Fi band and channel (read-only; the network name is not shown)."""
    from aiglasses.research.wifi_preflight import advice, read_status

    status = read_status()
    console.print(f"state: {status.state}")
    console.print(f"radio type: {status.radio_type or 'unknown'}")
    console.print(f"band: {status.band or 'unknown'}  channel: {status.channel or 'unknown'}")
    console.print(advice(status))
