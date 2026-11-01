import sys
import textwrap
from pathlib import Path
from typing import Any, ClassVar

import pytest

from aiglasses.assistant.vision import ImageUnavailable
from aiglasses.domain.models import MediaCounts
from aiglasses.research.glasses_http import HttpGuardError
from aiglasses.services.control import PhotoResult
from aiglasses.services.photo_fetch import (
    FakeHelper,
    HelperWifiDirect,
    PhotoFetcher,
    SubprocessHelper,
    WifiDirectImageSource,
    enable_not_approved,
)
from aiglasses.services.wifidirect import (
    FakeHostNetwork,
    FakeWifiDirect,
    GroupInfo,
    WifiDirectError,
)

HELPER_FILE = (
    Path(__file__).resolve().parents[1] / "src" / "aiglasses" / "helpers" / "wifidirect_helper.py"
)
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 50


class FakeHttp:
    log: ClassVar[list[str]] = []

    def __init__(self, ip: str, save_dir: Path, names: str = "a.jpg\nb.jpg\nnote.txt\n") -> None:
        self.ip, self.save_dir, self.names = ip, save_dir, names

    def get(self, path: str) -> bytes:
        FakeHttp.log.append(f"GET {path}")
        return self.names.encode()

    def download(self, path: str, dest: Path, *, expect_jpeg: bool = False) -> Path:
        FakeHttp.log.append(f"GET {path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(JPEG)
        return dest


class Events:
    async def wait_ip(self, timeout: float) -> str:
        return "192.168.49.1"


async def photo(added: int = 1) -> PhotoResult:
    return PhotoResult(MediaCounts(1, 0, 0), MediaCounts(1 + added, 0, 0), "ok", "00")


async def enable_ok() -> None:
    return None


def fetcher(
    tmp_path: Path, **overrides: Any
) -> tuple[PhotoFetcher, FakeWifiDirect, FakeHostNetwork]:
    wifi, host = overrides.pop("wifi", FakeWifiDirect()), overrides.pop("host", FakeHostNetwork())
    FakeHttp.log = []
    made = PhotoFetcher(
        take_photo=overrides.pop("take_photo", photo),
        enable_discovery=overrides.pop("enable", enable_ok),
        wifi=wifi, host=host, events=Events(), save_dir=tmp_path, peer_prefix="AIMB-G2",
        http=overrides.pop("http", FakeHttp),
    )  # fmt: skip
    return made, wifi, host


async def test_the_newest_image_is_downloaded_and_wifi_is_restored(tmp_path: Path) -> None:
    flow, wifi, host = fetcher(tmp_path)
    path = await flow.fetch_latest()
    assert path.name == "b.jpg" and path.read_bytes() == JPEG
    assert FakeHttp.log == ["GET /files/media.config", "GET /files/b.jpg"]
    assert wifi.connect_calls == [("AIMB-G2_TEST", 15)] and wifi.disconnects == 1
    assert host.restored == ["home"]


async def test_without_approval_nothing_touches_wifi_and_the_line_is_plain(tmp_path: Path) -> None:
    flow, wifi, host = fetcher(tmp_path, enable=enable_not_approved)
    with pytest.raises(ImageUnavailable) as caught:
        await flow.fetch_latest()
    assert "can't fetch the photo from the glasses yet" in caught.value.spoken
    assert wifi.connect_calls == [] and host.restored == []


async def test_a_photo_that_did_not_add_to_the_count_stops_before_wifi(tmp_path: Path) -> None:
    flow, wifi, _ = fetcher(tmp_path, take_photo=lambda: photo(0))
    with pytest.raises(ImageUnavailable, match="media count"):
        await flow.fetch_latest()
    assert wifi.connect_calls == []


async def test_a_peer_that_never_appears_rolls_back_and_speaks_a_short_line(tmp_path: Path) -> None:
    flow, wifi, host = fetcher(tmp_path, wifi=FakeWifiDirect(peers=[None]))
    with pytest.raises(ImageUnavailable) as caught:
        await flow.fetch_latest()
    assert "couldn't connect" in caught.value.spoken
    assert wifi.disconnects == 1 and host.restored == ["home"]


async def test_a_failed_download_still_leaves_the_group_and_restores_wifi(tmp_path: Path) -> None:
    class Failing(FakeHttp):
        def get(self, path: str) -> bytes:
            raise HttpGuardError("HTTP 500")

    flow, wifi, host = fetcher(tmp_path, http=Failing)
    with pytest.raises(ImageUnavailable) as caught:
        await flow.fetch_latest()
    assert "couldn't get the photo" in caught.value.spoken
    assert wifi.disconnects == 1 and host.restored == ["home"]


async def test_no_images_listed_is_an_error_not_a_crash(tmp_path: Path) -> None:
    flow, _, _ = fetcher(tmp_path, http=lambda ip, d: FakeHttp(ip, d, names="readme.txt\n"))
    with pytest.raises(ImageUnavailable, match="no images"):
        await flow.fetch_latest()


async def test_an_incomplete_rollback_is_reported_to_the_user(tmp_path: Path) -> None:
    flow, _, _ = fetcher(tmp_path, host=FakeHostNetwork(restore_error=True))
    with pytest.raises(ImageUnavailable) as caught:
        await flow.fetch_latest()
    assert "Wi-Fi may need reconnecting" in caught.value.spoken


async def test_the_image_source_returns_the_bytes(tmp_path: Path) -> None:
    flow, _, _ = fetcher(tmp_path)
    assert await WifiDirectImageSource(flow).capture() == JPEG


async def test_the_helper_adapter_maps_commands_and_caches_the_profile() -> None:
    helper = FakeHelper(profile="office", band="5 GHz")
    adapter = HelperWifiDirect(helper)
    await adapter.prepare()
    assert (adapter.current_profile(), adapter.band()) == ("office", "5 GHz")
    assert await adapter.find_peer("AIMB-G2", 16) == "AIMB-G2_TEST"
    group = await adapter.connect("AIMB-G2_TEST", group_owner_intent=15, timeout=15)
    assert group == GroupInfo("2.4 GHz", 6, None)
    await adapter.disconnect()
    await adapter.restore("office")
    assert await adapter.has_internet() is True
    assert helper.commands == [
        "preflight", "find_peer", "connect", "disconnect", "restore", "status",
    ]  # fmt: skip
    failing = HelperWifiDirect(FakeHelper(fail={"disconnect"}))
    with pytest.raises(WifiDirectError):
        await failing.disconnect()


HELPER_SCRIPT = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        request = json.loads(line)
        if request["cmd"] == "boom":
            print(json.dumps({"id": request["id"], "ok": False, "error": "no radio"}), flush=True)
        else:
            print(json.dumps({"id": request["id"], "ok": True, "echo": request["cmd"]}), flush=True)
    """
)


async def test_the_subprocess_helper_speaks_json_lines_and_reports_errors(tmp_path: Path) -> None:
    script = tmp_path / "helper.py"
    script.write_text(HELPER_SCRIPT, encoding="utf-8")
    helper = SubprocessHelper([sys.executable, "-I", str(script)], timeout_s=10)
    try:
        assert (await helper.request("status"))["echo"] == "status"
        with pytest.raises(WifiDirectError, match="no radio"):
            await helper.request("boom")
    finally:
        await helper.close()


async def test_the_real_helper_script_in_fake_mode_serves_the_whole_protocol() -> None:
    script = HELPER_FILE
    helper = SubprocessHelper([sys.executable, "-I", str(script), "--fake"], timeout_s=10)
    adapter = HelperWifiDirect(helper)
    try:
        await adapter.prepare()
        assert (adapter.current_profile(), adapter.band()) == ("fake-home", "2.4 GHz")
        peer = await adapter.find_peer("AIMB-G2", 16)
        assert peer == "AIMB-G2_FAKE"
        group = await adapter.connect(peer or "", group_owner_intent=15, timeout=15)
        assert group.glasses_ip == "192.168.49.2" and not await adapter.has_internet()
        await adapter.disconnect()
        await adapter.restore("fake-home")
        assert await adapter.has_internet()
        with pytest.raises(WifiDirectError, match="unknown command"):
            await helper.request("format_c")
    finally:
        await helper.close()
