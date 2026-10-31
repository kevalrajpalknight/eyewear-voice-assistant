import socket

import pytest


def test_connections_to_outside_hosts_are_blocked() -> None:
    with socket.socket() as sock, pytest.raises(RuntimeError, match="blocked"):
        sock.connect(("93.184.216.34", 80))


def test_loopback_connections_still_work() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.connect(server.getsockname())
