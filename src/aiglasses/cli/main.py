import asyncio

import typer
from rich.console import Console
from rich.table import Table

from aiglasses.capabilities import CAPABILITIES
from aiglasses.config import Settings, save_device_address
from aiglasses.domain.errors import GlassesError
from aiglasses.domain.models import Confidence
from aiglasses.logging_setup import setup_logging
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
    try:
        results = asyncio.run(scan(timeout or settings.scan_timeout))
    except GlassesError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc

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
    """Device information."""
    _stub("info", 1)


@app.command("gatt-dump")
def gatt_dump() -> None:
    """Dump GATT services to JSON."""
    _stub("gatt-dump", 1)


@app.command()
def listen() -> None:
    """Listen for notifications."""
    _stub("listen", 1)


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
