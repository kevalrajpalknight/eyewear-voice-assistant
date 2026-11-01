import socket
import sys
import textwrap
from pathlib import Path

import psutil
import pytest

from aiglasses.assistant.llm import local_server
from aiglasses.assistant.llm.local_server import LocalServer, PortInUse, ServerStartError
from aiglasses.assistant.llm.models import Registry
from aiglasses.settings import AssistantSettings

FAKE_SERVER = textwrap.dedent(
    """
    import http.server, sys
    port = int(sys.argv[1])
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"status":"ok"}')
    http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
    """
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[LocalServer, Path]:
    """A LocalServer whose 'llama-server' is a tiny Python HTTP server."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / local_server.EXECUTABLE).write_bytes(b"")
    reg = Registry.load()
    model = next(iter(reg.models))
    settings = AssistantSettings(
        _env_file=None,
        local_llm_model=model,
        local_llm_bin_dir=bin_dir,
        models_dir=tmp_path,
        local_llm_port=free_port(),
    )
    script = tmp_path / "fake.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    (tmp_path / model).mkdir()
    (tmp_path / model / reg.models[model].main.filename).write_bytes(b"x")

    def fake_args(
        s: AssistantSettings, r: Registry, port: int, *, vision: bool = False
    ) -> list[str]:
        return [
            sys.executable,
            "-I",
            str(script),
            str(port),
            str(tmp_path / model / reg.models[model].main.filename),
        ]

    monkeypatch.setattr(local_server, "server_args", fake_args)
    return LocalServer(settings, reg, tmp_path / "state.json"), tmp_path / "state.json"


def test_start_reports_healthy_then_stop_leaves_no_process_and_no_state(
    rig: tuple[LocalServer, Path],
) -> None:
    server, state = rig
    server.start(wait_s=30)
    pid = server._process.pid  # type: ignore[union-attr]
    assert local_server.health(server.port) == "ok" and state.exists()
    info = local_server.status(state)
    assert info["running"] is False or info["health"]  # the stand-in is not named llama-server
    server.stop()
    assert not psutil.pid_exists(pid) or not psutil.Process(pid).is_running()
    assert local_server.health(server.port) == "down" and not state.exists()


def test_the_job_object_kills_the_child_when_the_owner_closes_it(
    rig: tuple[LocalServer, Path],
) -> None:
    server, _ = rig
    server.start(wait_s=30)
    pid = server._process.pid  # type: ignore[union-attr]
    server._job.close()  # what the OS does when the parent dies or crashes
    for _ in range(50):
        if not psutil.pid_exists(pid):
            break
        import time

        time.sleep(0.1)
    assert not psutil.pid_exists(pid) or sys.platform != "win32"
    server.stop()


def test_a_busy_port_is_an_error_unless_auto_port_is_asked_for(
    rig: tuple[LocalServer, Path],
) -> None:
    server, _ = rig
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", server.port))
        blocker.listen(1)
        with pytest.raises(PortInUse, match="LOCAL_LLM_PORT"):
            server.start(wait_s=5)
        server.start(auto_port=True, wait_s=30)
        assert server.port != blocker.getsockname()[1] and local_server.health(server.port) == "ok"
    server.stop()


def test_a_missing_runtime_or_model_is_a_clear_error(
    rig: tuple[LocalServer, Path], tmp_path: Path
) -> None:
    server, _ = rig
    (server.settings.local_llm_bin_dir / local_server.EXECUTABLE).unlink()
    with pytest.raises(ServerStartError, match=r"models pull llama\.cpp"):
        server.start()


def test_a_server_that_exits_while_loading_is_reported(
    rig: tuple[LocalServer, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    server, state = rig
    quitter = state.parent / "quit.py"
    quitter.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    monkeypatch.setattr(
        local_server, "server_args", lambda *a, **k: [sys.executable, "-I", str(quitter)]
    )
    with pytest.raises(ServerStartError, match="exited"):
        server.start(wait_s=10)
    assert not state.exists()


def test_status_and_stop_ignore_a_process_that_is_not_llama_server(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(
        f'{{"pid": {psutil.Process().pid}, "port": 1, "model": "m", "started": 0}}',
        encoding="utf-8",
    )
    assert local_server.status(state)["running"] is False
    assert local_server.stop_from_state(state) is True  # the file is cleared...
    assert psutil.Process().is_running()  # ...and this very process was NOT terminated
    assert local_server.status(state) == {"running": False}
    assert local_server.stop_from_state(state) is False


def test_the_server_start_wait_times_out_cleanly(
    rig: tuple[LocalServer, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _ = rig
    monkeypatch.setattr(local_server, "health", lambda port, timeout=1.0: "loading")
    with pytest.raises(ServerStartError, match="not ready"):
        server.start(wait_s=0.6)
    assert server._process is None
