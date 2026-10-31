"""A deliberately tiny HTTP client for the glasses' media server (read-only: GET on /files/ only).

Guards, all enforced in code: only the private IP the glasses reported; GET is the only method
that can be sent; paths must be under /files/ with a strict character set; redirects are never
followed; 5 s connect timeout; response size limit; photos must start with the JPEG marker.
"""

import http.client
import ipaddress
import re
from pathlib import Path

from aiglasses.domain.errors import GlassesError

ALLOWED_PREFIX = "/files/"
PATH_PATTERN = re.compile(r"^/files/[A-Za-z0-9._\-/]+$")
JPEG_MARKER = b"\xff\xd8"
RFC1918 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
CHUNK = 64 * 1024


class HttpGuardError(GlassesError):
    """A request or response broke one of the media-client rules."""


def validate_host(host: str, *, allow_loopback: bool = False) -> str:
    """Accept only a literal private IPv4 address (RFC 1918); loopback only for tests."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise HttpGuardError(f"{host!r} is not an IP address") from exc
    if address.version != 4:
        raise HttpGuardError("only IPv4 addresses are accepted")
    if address.is_loopback:
        if allow_loopback:
            return host
        raise HttpGuardError("loopback addresses are not accepted")
    if not any(address in network for network in RFC1918):
        raise HttpGuardError(f"{host} is not a private (RFC 1918) address")
    return host


def validate_path(path: str) -> str:
    if not PATH_PATTERN.fullmatch(path) or ".." in path or "//" in path:
        raise HttpGuardError(f"path {path!r} is not allowed (only {ALLOWED_PREFIX}<name>)")
    return path


class GlassesHttpClient:
    """GET-only client bound to one reported IP address."""

    def __init__(
        self,
        reported_ip: str,
        save_dir: Path,
        *,
        port: int = 80,
        max_bytes: int = DEFAULT_MAX_BYTES,
        allow_loopback: bool = False,
    ) -> None:
        self._host = validate_host(reported_ip, allow_loopback=allow_loopback)
        self._port = port
        self._save_dir = save_dir.resolve()
        self._max_bytes = max_bytes

    def get(self, path: str) -> bytes:
        validate_path(path)
        conn = http.client.HTTPConnection(self._host, self._port, timeout=CONNECT_TIMEOUT_SECONDS)
        try:
            conn.connect()
            if conn.sock is not None:
                conn.sock.settimeout(READ_TIMEOUT_SECONDS)
            conn.request("GET", path, headers={"Host": self._host, "Connection": "close"})
            response = conn.getresponse()
            if 300 <= response.status < 400:
                raise HttpGuardError(f"HTTP {response.status}: redirects are never followed")
            if response.status != 200:
                raise HttpGuardError(f"HTTP {response.status} for {path}")
            declared = response.getheader("Content-Length")
            if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
                raise HttpGuardError(
                    f"response of {declared} bytes exceeds the {self._max_bytes} limit"
                )
            body = bytearray()
            while chunk := response.read(CHUNK):
                body.extend(chunk)
                if len(body) > self._max_bytes:
                    raise HttpGuardError(f"response exceeds the {self._max_bytes} byte limit")
            return bytes(body)
        except OSError as exc:  # timeouts, refused connections, resets
            raise HttpGuardError(f"request to {self._host} failed: {exc}") from exc
        finally:
            conn.close()

    def download(self, path: str, dest: Path, *, expect_jpeg: bool = False) -> Path:
        """GET `path` and save it under save_dir. Nothing is written if a check fails."""
        target = dest.resolve()
        if not target.is_relative_to(self._save_dir):
            raise HttpGuardError(f"{dest} is outside the save directory {self._save_dir}")
        data = self.get(path)
        if expect_jpeg and not data.startswith(JPEG_MARKER):
            raise HttpGuardError("the file does not start with the JPEG marker FF D8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target
