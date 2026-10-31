import http.client
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from aiglasses.research import glasses_http
from aiglasses.research.glasses_http import GlassesHttpClient, HttpGuardError, validate_host

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100
SEEN: list[tuple[str, str, str | None]] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        return

    def _record(self) -> None:
        SEEN.append((self.command, self.path, self.headers.get("Host")))

    def do_GET(self) -> None:
        self._record()
        if self.path == "/files/media.config":
            body = b"a.jpg\nb.jpg\n"
        elif self.path == "/files/a.jpg":
            body = JPEG
        elif self.path == "/files/notjpeg.jpg":
            body = b"<html>not an image</html>"
        elif self.path == "/files/redirect":
            self.send_response(302)
            self.send_header("Location", "http://203.0.113.9/files/a.jpg")
            self.end_headers()
            return
        elif self.path == "/files/huge":
            self.send_response(200)
            self.send_header("Content-Length", str(10_000_000))
            self.end_headers()
            return
        elif self.path == "/files/endless":
            self.send_response(200)
            self.end_headers()
            for _ in range(200):
                self.wfile.write(b"z" * 1024)
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self) -> None:
        self._record()
        self.send_response(200)
        self.end_headers()

    do_POST = do_PUT = do_DELETE


@pytest.fixture
def server() -> Iterator[tuple[str, int]]:
    SEEN.clear()
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield "127.0.0.1", httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def client(server: tuple[str, int], tmp_path: Path, **kwargs: Any) -> GlassesHttpClient:
    host, port = server
    return GlassesHttpClient(host, tmp_path, port=port, allow_loopback=True, **kwargs)


def test_get_returns_the_body_and_sends_only_get(server: tuple[str, int], tmp_path: Path) -> None:
    assert client(server, tmp_path).get("/files/media.config") == b"a.jpg\nb.jpg\n"
    assert SEEN == [("GET", "/files/media.config", "127.0.0.1")]


@pytest.mark.parametrize(
    "path",
    [
        "/other/a.jpg",
        "/files/../etc/passwd",
        "/files//a.jpg",
        "/files/a.jpg?x=1",
        "/files/a b.jpg",
        "http://evil/files/a.jpg",
        "files/a.jpg",
        "/files/",
        "/files/a#b",
        "/files/a\\b",
    ],
)
def test_bad_paths_are_refused_before_any_request(
    server: tuple[str, int], tmp_path: Path, path: str
) -> None:
    with pytest.raises(HttpGuardError):
        client(server, tmp_path).get(path)
    assert SEEN == []


def test_redirects_are_never_followed(server: tuple[str, int], tmp_path: Path) -> None:
    with pytest.raises(HttpGuardError, match="redirects are never followed"):
        client(server, tmp_path).get("/files/redirect")
    assert len(SEEN) == 1  # no second request to the redirect target


def test_size_limits_by_header_and_by_stream(server: tuple[str, int], tmp_path: Path) -> None:
    small = client(server, tmp_path, max_bytes=1000)
    with pytest.raises(HttpGuardError, match="exceeds"):
        small.get("/files/huge")
    with pytest.raises(HttpGuardError, match="limit"):
        small.get("/files/endless")


def test_other_status_codes_are_errors(server: tuple[str, int], tmp_path: Path) -> None:
    with pytest.raises(HttpGuardError, match="404"):
        client(server, tmp_path).get("/files/missing")


def test_download_saves_only_valid_jpegs_inside_the_save_dir(
    server: tuple[str, int], tmp_path: Path
) -> None:
    c = client(server, tmp_path)
    saved = c.download("/files/a.jpg", tmp_path / "photos" / "a.jpg", expect_jpeg=True)
    assert saved.read_bytes() == JPEG
    with pytest.raises(HttpGuardError, match="JPEG"):
        c.download("/files/notjpeg.jpg", tmp_path / "bad.jpg", expect_jpeg=True)
    assert not (tmp_path / "bad.jpg").exists()
    with pytest.raises(HttpGuardError, match="outside the save directory"):
        c.download("/files/a.jpg", tmp_path.parent / "escape.jpg")


@pytest.mark.parametrize(
    "host", ["8.8.8.8", "169.254.1.1", "203.0.113.9", "example.com", "::1", "fe80::1", "0.0.0.0"]
)
def test_only_private_rfc1918_addresses_are_accepted(host: str) -> None:
    with pytest.raises(HttpGuardError):
        validate_host(host)


@pytest.mark.parametrize("host", ["192.168.49.1", "192.168.43.7", "10.1.2.3", "172.20.0.5"])
def test_private_addresses_are_accepted(host: str) -> None:
    assert validate_host(host) == host


def test_loopback_needs_the_test_flag() -> None:
    with pytest.raises(HttpGuardError, match="loopback"):
        validate_host("127.0.0.1")
    assert validate_host("127.0.0.1", allow_loopback=True) == "127.0.0.1"


def test_the_client_has_no_way_to_send_other_methods() -> None:
    public = {name for name in dir(GlassesHttpClient) if not name.startswith("_")}
    assert public == {"get", "download"}


def test_connect_timeout_is_five_seconds_and_only_get_is_issued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, float | None]] = []
    methods: list[str] = []
    real_init = http.client.HTTPConnection.__init__
    real_request = http.client.HTTPConnection.request

    def spy_init(
        self: Any, host: str, port: int | None = None, timeout: Any = None, **kw: Any
    ) -> None:
        calls.append((host, timeout))
        real_init(self, host, port, timeout, **kw)

    def spy_request(self: Any, method: str, url: str, *a: Any, **kw: Any) -> None:
        methods.append(method)
        raise OSError("stop here")

    monkeypatch.setattr(http.client.HTTPConnection, "__init__", spy_init)
    monkeypatch.setattr(http.client.HTTPConnection, "request", spy_request)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", lambda self: None)
    c = GlassesHttpClient("192.168.49.1", tmp_path)
    with pytest.raises(HttpGuardError):
        c.get("/files/a.jpg")
    assert calls == [("192.168.49.1", glasses_http.CONNECT_TIMEOUT_SECONDS)]
    assert glasses_http.CONNECT_TIMEOUT_SECONDS == 5.0
    assert methods == ["GET"]
    del real_request
