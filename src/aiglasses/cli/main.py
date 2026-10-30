import asyncio
import sys
import threading
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from aiglasses.capabilities import CAPABILITIES
from aiglasses.config import Settings, save_device_address
from aiglasses.domain.errors import GlassesError
from aiglasses.domain.models import Confidence
from aiglasses.glasses import Glasses
from aiglasses.hexfmt import ascii_of
from aiglasses.logging_setup import setup_logging
from aiglasses.research.gatt_dump import dump_gatt, write_dump
from aiglasses.research.listen import Notification, listen
from aiglasses.services.audio import AudioService
from aiglasses.transport.audio_sd import SoundDeviceAudio
from aiglasses.transport.ble_scan import is_candidate, scan
from aiglasses.transport.tracing import TracingTransport

app = typer.Typer(no_args_is_help=True, help="AIMB-G2 AI glasses toolkit.")
video_app = typer.Typer(help="Video control.")
media_app = typer.Typer(help="Media on the glasses.")
audio_app = typer.Typer(help="Bluetooth audio.")
wifi_app = typer.Typer(help="Glasses Wi-Fi link.")
capture_app = typer.Typer(help="Official-app capture analysis.")
for name, sub in (
    ("video", video_app),
    ("media", media_app),
    ("audio", audio_app),
    ("wifi", wifi_app),
    ("capture", capture_app),
):
    app.add_typer(sub, name=name)

console = Console()
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
    setup_logging(Settings().log_dir, verbose)


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


@app.command("listen")
def listen_cmd(
    duration: float = typer.Option(None, help="Seconds to listen (default from config)"),
) -> None:
    """Subscribe to all notify/indicate characteristics and print what arrives (no writes)."""

    def show(n: Notification) -> None:
        stamp = f"{n.ts % 86400:.3f}"
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


@app.command()
def battery() -> None:
    """Battery level."""
    _stub("battery", 3)


@app.command()
def photo() -> None:
    """Take a photo."""
    _stub("photo", 4)


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
def capture_analyze() -> None:
    _stub("capture analyze", 3)
