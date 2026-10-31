import hashlib
import http.server
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from aiglasses.assistant.models import (
    ChecksumMismatch,
    DownloadBudgetExceeded,
    FileSpec,
    GatedModel,
    ModelSpec,
    Registry,
    RuntimeSpec,
    check_budget,
    default_url,
    download,
    install_runtime,
    model_path,
    pull_model,
    sha256_of,
    verify_model,
)
from aiglasses.domain.errors import ConfigError, GlassesError

BLOB = bytes(range(256)) * 400  # 102 400 bytes
SHA = hashlib.sha256(BLOB).hexdigest()


class Handler(http.server.BaseHTTPRequestHandler):
    files: ClassVar[dict[str, bytes]] = {}
    seen_auth: ClassVar[list[str | None]] = []

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        Handler.seen_auth.append(self.headers.get("Authorization"))
        data = Handler.files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        start = 0
        if "Range" in self.headers:
            start = int(self.headers["Range"].split("=")[1].rstrip("-"))
            self.send_response(206)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        self.wfile.write(data[start:])


@pytest.fixture
def server() -> Iterator[str]:
    Handler.files, Handler.seen_auth = {}, []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def registry(gated: bool = False, size: int = len(BLOB)) -> Registry:
    spec = ModelSpec(
        "tiny", "org/repo", "rev1",
        (FileSpec("tiny.gguf", SHA, size), FileSpec("proj.gguf", SHA, size)),
        "MIT", "test", "chat", gated,
    )  # fmt: skip
    runtime = RuntimeSpec("b1", "http://x/z.zip", "z.zip", SHA, len(BLOB), "MIT")
    return Registry(runtime, {"tiny": spec})


def test_download_verifies_size_and_checksum_and_keeps_the_file(
    server: str, tmp_path: Path
) -> None:
    Handler.files["/a"] = BLOB
    target = tmp_path / "a.bin"
    seen: list[int] = []
    download(
        server + "/a", target, sha256=SHA, size=len(BLOB), progress=lambda d, t: seen.append(d)
    )
    assert (
        sha256_of(target) == SHA
        and seen[-1] == len(BLOB)
        and not target.with_suffix(".bin.part").exists()
    )


def test_a_wrong_checksum_deletes_the_download(server: str, tmp_path: Path) -> None:
    Handler.files["/a"] = BLOB
    with pytest.raises(ChecksumMismatch):
        download(server + "/a", tmp_path / "a.bin", sha256="0" * 64, size=len(BLOB))
    assert list(tmp_path.iterdir()) == []


def test_an_interrupted_download_resumes_from_the_part_file(server: str, tmp_path: Path) -> None:
    Handler.files["/a"] = BLOB
    (tmp_path / "a.bin.part").write_bytes(BLOB[:5000])
    download(server + "/a", tmp_path / "a.bin", sha256=SHA, size=len(BLOB))
    assert sha256_of(tmp_path / "a.bin") == SHA


def test_http_errors_are_reported_without_the_url_query(server: str, tmp_path: Path) -> None:
    with pytest.raises(GlassesError, match="HTTP 404"):
        download(server + "/missing?token=secret", tmp_path / "x", sha256=SHA, size=1)


def test_pull_downloads_every_file_and_skips_what_is_present(server: str, tmp_path: Path) -> None:
    Handler.files = {"/tiny.gguf": BLOB, "/proj.gguf": BLOB}
    reg = registry()
    urls = lambda spec, file: server + "/" + file.filename  # noqa: E731
    paths = pull_model(reg, "tiny", tmp_path, 1.0, url_for=urls)
    assert [p.name for p in paths] == ["tiny.gguf", "proj.gguf"]
    Handler.files = {}  # a second pull needs no network
    assert pull_model(reg, "tiny", tmp_path, 1.0, url_for=urls) == paths
    assert verify_model(reg, "tiny", tmp_path) == {"tiny.gguf": "ok", "proj.gguf": "ok"}


def test_verify_reports_missing_and_changed_files(tmp_path: Path) -> None:
    reg = registry()
    spec = reg.get("tiny")
    assert set(verify_model(reg, "tiny", tmp_path).values()) == {"missing"}
    path = model_path(tmp_path, spec)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"changed")
    assert verify_model(reg, "tiny", tmp_path)["tiny.gguf"] == "MISMATCH"


def test_the_download_budget_is_a_hard_stop(tmp_path: Path) -> None:
    huge = registry(size=5_000_000_000)
    with pytest.raises(DownloadBudgetExceeded, match="budget"):
        check_budget(tmp_path, huge, huge.get("tiny"), 8.0)
    check_budget(tmp_path, huge, huge.get("tiny"), 12.0)


def test_a_gated_model_is_refused_without_a_token_and_the_token_is_not_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(GatedModel) as caught:
        pull_model(registry(gated=True), "tiny", tmp_path, 1.0)
    assert "HF_TOKEN" in str(caught.value) and "hf_" not in str(caught.value)


def test_urls_point_at_the_pinned_revision() -> None:
    reg = registry()
    spec = reg.get("tiny")
    assert default_url(spec, spec.main) == "https://huggingface.co/org/repo/resolve/rev1/tiny.gguf"


def test_unknown_models_list_the_valid_names() -> None:
    with pytest.raises(ConfigError, match="valid: tiny"):
        registry().get("nope")


def test_the_shipped_registry_is_complete_and_pinned() -> None:
    reg = Registry.load()
    assert reg.runtime.release.startswith("b") and len(reg.runtime.sha256) == 64
    for spec in reg.models.values():
        assert len(spec.revision) == 40 and not spec.gated
        assert all(len(f.sha256) == 64 and f.size > 0 for f in spec.files)
    assert sum(s.size for s in reg.models.values()) < 8 * 1_000_000_000


def test_the_runtime_zip_is_verified_and_unpacked_safely(server: str, tmp_path: Path) -> None:
    archive = tmp_path / "src.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("llama-server.exe", b"x")
    data = archive.read_bytes()
    Handler.files["/z.zip"] = data
    good = Registry(
        RuntimeSpec(
            "b9", server + "/z.zip", "z.zip", hashlib.sha256(data).hexdigest(), len(data), "MIT"
        ),
        {},
    )  # fmt: skip
    bin_dir = tmp_path / "tools" / "llama.cpp"
    install_runtime(good, bin_dir)
    assert (bin_dir / "llama-server.exe").exists() and (bin_dir / "VERSION").read_text().startswith(
        "b9"
    )
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("../escape.txt", b"x")
    edata = evil.read_bytes()
    Handler.files["/e.zip"] = edata
    bad = Registry(
        RuntimeSpec(
            "b9", server + "/e.zip", "e.zip", hashlib.sha256(edata).hexdigest(), len(edata), "MIT"
        ),
        {},
    )  # fmt: skip
    with pytest.raises(GlassesError, match="unsafe"):
        install_runtime(bad, tmp_path / "tools2" / "llama.cpp")
    assert not (tmp_path / "tools2" / "escape.txt").exists()
