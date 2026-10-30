import asyncio
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
from aiglasses.transport.ble_scan import is_candidate, scan

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


@audio_app.command("devices")
def audio_devices() -> None:
    _stub("audio devices", 2)


@audio_app.command("play")
def audio_play() -> None:
    _stub("audio play", 2)


@audio_app.command("record")
def audio_record() -> None:
    _stub("audio record", 2)


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
