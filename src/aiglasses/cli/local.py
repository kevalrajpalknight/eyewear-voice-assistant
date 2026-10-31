"""`aiglasses models ...` and `aiglasses llm-server ...`: local models and the managed server."""

import signal
import time

import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TaskID, TextColumn
from rich.table import Table

from aiglasses.assistant import local_server
from aiglasses.assistant.models import (
    GB,
    RUNTIME_NAME,
    Registry,
    install_runtime,
    installed_bytes,
    model_path,
    pull_model,
    verify_model,
)
from aiglasses.config import Settings
from aiglasses.domain.errors import GlassesError
from aiglasses.settings import AssistantSettings

console = Console()
models_app = typer.Typer(help="Local model registry: list, pull, verify.")
llm_server_app = typer.Typer(help="Managed local llama.cpp server (127.0.0.1 only).")


def _registry(assistant: AssistantSettings) -> Registry:
    path = assistant.model_registry_path
    return Registry.load(path if path.exists() else None)


@models_app.command("list")
def models_list() -> None:
    """Models and the runtime in the registry, with what is installed."""
    assistant = AssistantSettings()
    registry = _registry(assistant)
    table = Table(title=f"Download budget {assistant.model_download_budget_gb:g} GB")
    for column in ("Name", "Size GB", "License", "Role", "Gated", "Installed"):
        table.add_column(column)
    bin_dir = assistant.local_llm_bin_dir
    rt = registry.runtime
    table.add_row(
        RUNTIME_NAME, f"{rt.size / GB:.2f}", rt.license, f"release {rt.release}", "no",
        "yes" if (bin_dir / local_server.EXECUTABLE).exists() else "no",
    )  # fmt: skip
    for spec in registry.models.values():
        present = all(model_path(assistant.models_dir, spec, f).exists() for f in spec.files)
        table.add_row(
            spec.name, f"{spec.size / GB:.2f}", spec.license, spec.role,
            "YES (needs your action)" if spec.gated else "no", "yes" if present else "no",
        )  # fmt: skip
    console.print(table)
    used = installed_bytes(assistant.models_dir, registry) / GB
    console.print(
        f"Installed models: {used:.2f} GB of {assistant.model_download_budget_gb:g} GB budget."
    )


def _progress() -> tuple[Progress, TaskID]:
    bar = Progress(TextColumn("{task.description}"), BarColumn(), DownloadColumn(), console=console)
    return bar, bar.add_task("download", total=None)


@models_app.command("pull")
def models_pull(
    name: str = typer.Argument(..., help="A model name, or llama.cpp for the runtime"),
) -> None:
    """Download and verify (sha256). Stops if the download budget would be exceeded."""
    assistant = AssistantSettings()
    registry = _registry(assistant)
    try:
        bar, task = _progress()
        with bar:

            def update(done: int, total: int) -> None:
                bar.update(task, completed=done, total=total, description=name)

            if name == RUNTIME_NAME:
                install_runtime(registry, assistant.local_llm_bin_dir, progress=update)
                console.print(f"Installed llama.cpp {registry.runtime.release}.")
            else:
                paths = pull_model(
                    registry, name, assistant.models_dir, assistant.model_download_budget_gb,
                    progress=update,
                )  # fmt: skip
                console.print(f"Ready: {', '.join(str(p) for p in paths)}")
    except GlassesError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc


@models_app.command("verify")
def models_verify(
    name: str = typer.Argument(None, help="One model; default all installed"),
) -> None:
    """Hash installed files and compare with the registry."""
    assistant = AssistantSettings()
    registry = _registry(assistant)
    bad = False
    for model in [name] if name else list(registry.models):
        report = verify_model(registry, model, assistant.models_dir)
        if name is None and set(report.values()) == {"missing"}:
            continue
        for filename, result in report.items():
            console.print(f"{model}/{filename}: {result}")
            bad = bad or result != "ok"
    raise typer.Exit(1 if bad else 0)


def _state_file() -> "object":
    return Settings().log_dir / "llama-server.json"


@llm_server_app.command("start")
def llm_server_start(
    auto_port: bool = typer.Option(False, help="Use the next free port if LOCAL_LLM_PORT is busy"),
    vision: bool = typer.Option(False, help="Also load the image projector (multimodal models)"),
) -> None:
    """Start llama-server and keep it until Ctrl+C. The child is killed with this process."""
    assistant = AssistantSettings()
    state_file = Settings().log_dir / "llama-server.json"
    server = local_server.LocalServer(assistant, _registry(assistant), state_file)
    try:
        console.print("Starting llama-server (loading the model) ...")
        server.start(auto_port=auto_port, vision=vision)
        console.print(
            f"Ready at {server.base_url} (model {assistant.local_llm_model}). Ctrl+C to stop."
        )
        signal.signal(signal.SIGINT, signal.default_int_handler)
        while server._process is not None and server._process.poll() is None:
            time.sleep(1)
        console.print("[yellow]llama-server exited.[/]")
    except GlassesError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        console.print("Stopping ...")
    finally:
        server.stop()


@llm_server_app.command("status")
def llm_server_status() -> None:
    """Health, model and memory of a running server."""
    info = local_server.status(Settings().log_dir / "llama-server.json")
    if not info["running"]:
        console.print("llama-server is not running.")
        raise typer.Exit(1)
    for key, value in info.items():
        console.print(f"{key}: {value}")


@llm_server_app.command("stop")
def llm_server_stop() -> None:
    """Stop a server that another terminal started."""
    stopped = local_server.stop_from_state(Settings().log_dir / "llama-server.json")
    console.print("Stopped." if stopped else "No server recorded.")
