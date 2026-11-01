"""Manage a llama.cpp `llama-server` as a child process that cannot outlive us.

- Binds 127.0.0.1 only.
- On Windows the child is placed in a job object with kill-on-close, so it dies when this process
  exits, crashes or is interrupted (Ctrl+C), even if no cleanup code runs.
- State (pid, port, model) is kept in a small JSON file so `status` and `stop` work from another
  terminal.
"""

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import psutil

from aiglasses.assistant.llm.jobobject import JobObject
from aiglasses.assistant.llm.models import Registry, model_path
from aiglasses.domain.errors import ConfigError, GlassesError
from aiglasses.settings import AssistantSettings

LOOPBACK = "127.0.0.1"
EXECUTABLE = "llama-server.exe"
HEALTH_POLL_S = 0.5
CHAT_ROUTE = "/v1/chat/completions"


class PortInUse(GlassesError):
    pass


# Gemma 4 sometimes 'thinks' for the whole token limit and then answers nothing; the server flag
# --reasoning off does not stop that, this request field does.
NO_THINKING = {"thinking_budget_tokens": 0}


class ServerStartError(GlassesError):
    pass


@dataclass
class ServerState:
    pid: int
    port: int
    model: str
    started: float


def physical_cores() -> int:
    return psutil.cpu_count(logical=False) or max((os.cpu_count() or 2) // 2, 1)


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((LOOPBACK, port))
        except OSError:
            return False
    return True


def free_port(start: int, tries: int = 50) -> int:
    for port in range(start, start + tries):
        if port_is_free(port):
            return port
    raise PortInUse(f"no free port found from {start} to {start + tries - 1}")


def server_args(
    settings: AssistantSettings, registry: Registry, port: int, *, vision: bool = False
) -> list[str]:
    """The llama-server command line. Everything tunable comes from settings."""
    name = settings.local_llm_model
    if name is None:
        raise ConfigError("LOCAL_LLM_MODEL is not set (see `aiglasses models list`)")
    spec = registry.get(name)
    exe = settings.local_llm_bin_dir / EXECUTABLE
    args = [
        str(exe), "-m", str(model_path(settings.models_dir, spec)),
        "--host", LOOPBACK, "--port", str(port),
        "-c", str(settings.local_llm_ctx), "-b", str(settings.local_llm_batch),
        "-t", str(settings.local_llm_threads or physical_cores()),
        "--parallel", "1", "--jinja", "--reasoning", "off",
    ]  # fmt: skip
    if not settings.local_llm_prompt_cache:
        args.append("--no-cache-prompt")
    if vision and len(spec.files) > 1:
        args += ["--mmproj", str(model_path(settings.models_dir, spec, spec.files[1]))]
    return args


class LocalServer:
    """Owns one llama-server process for the lifetime of the object (use `with` or `stop`)."""

    def __init__(self, settings: AssistantSettings, registry: Registry, state_file: Path) -> None:
        self.settings, self.registry, self.state_file = settings, registry, state_file
        self.port = settings.local_llm_port
        self._process: subprocess.Popen[bytes] | None = None
        self._job = JobObject()

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"

    def start(
        self, *, auto_port: bool = False, vision: bool = False, wait_s: float = 120.0
    ) -> None:
        exe = self.settings.local_llm_bin_dir / EXECUTABLE
        if not exe.exists():
            raise ServerStartError(f"{exe} is missing; run `aiglasses models pull llama.cpp`")
        if not port_is_free(self.port):
            if not auto_port:
                raise PortInUse(
                    f"port {self.port} is in use (set LOCAL_LLM_PORT, or start with --auto-port)"
                )
            self.port = free_port(self.port + 1)
        args = server_args(self.settings, self.registry, self.port, vision=vision)
        if not Path(args[2]).exists():
            raise ServerStartError(f"{args[2]} is missing; run `aiglasses models pull <name>`")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        log_path = self.state_file.parent / "llama-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as sink:  # the child keeps its own handle after this closes
            self._process = subprocess.Popen(
                args, stdout=sink, stderr=subprocess.STDOUT, creationflags=flags
            )
        self._job.add(self._process)
        state = ServerState(
            self._process.pid, self.port, str(self.settings.local_llm_model), time.time()
        )
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(asdict(state)), encoding="utf-8")
        self._wait_healthy(wait_s)

    def _wait_healthy(self, wait_s: float) -> None:
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                self.stop()
                raise ServerStartError("llama-server exited while loading the model")
            if health(self.port) == "ok":
                return
            time.sleep(HEALTH_POLL_S)
        self.stop()
        raise ServerStartError(f"llama-server was not ready after {wait_s:g} s")

    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self._process = None
        self._job.close()
        self.state_file.unlink(missing_ok=True)

    def __enter__(self) -> "LocalServer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def health(port: int, timeout: float = 1.0) -> str:
    """ok | loading | down."""
    try:
        reply = httpx.get(f"http://{LOOPBACK}:{port}/health", timeout=timeout)
    except httpx.HTTPError:
        return "down"
    return "ok" if reply.status_code == 200 else "loading"


def read_state(state_file: Path) -> ServerState | None:
    try:
        return ServerState(**json.loads(state_file.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def status(state_file: Path) -> dict[str, object]:
    state = read_state(state_file)
    if state is None:
        return {"running": False}
    try:
        proc = psutil.Process(state.pid)
        alive = proc.is_running() and proc.name().lower().startswith("llama-server")
        rss = round(proc.memory_info().rss / 2**20) if alive else None
    except psutil.Error:
        alive, rss = False, None
    return {
        "running": alive, "pid": state.pid, "port": state.port, "model": state.model,
        "health": health(state.port) if alive else "down", "ram_mb": rss,
        "uptime_s": round(time.time() - state.started),
    }  # fmt: skip


def stop_from_state(state_file: Path) -> bool:
    """Stop a server started by another terminal. Only touches a process named llama-server."""
    state = read_state(state_file)
    if state is None:
        return False
    try:
        proc = psutil.Process(state.pid)
        if proc.name().lower().startswith("llama-server"):
            proc.terminate()
            proc.wait(timeout=5)
    except psutil.Error:
        pass
    state_file.unlink(missing_ok=True)
    return True


def warm_up(port: int, model_hint: str = "") -> None:
    """One tiny request so weights are paged in and the first real turn is not slow."""
    httpx.post(
        f"http://{LOOPBACK}:{port}{CHAT_ROUTE}",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "cache_prompt": True,
        },
        timeout=60.0,
    )
