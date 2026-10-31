import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aiglasses.cli import main as cli
from aiglasses.research.bugreport import triage_zip

REPORT = """\
DUMP OF SERVICE bluetooth_manager:
  10-31 10:46:08 - 42683ms : D0:4B:1B:C3:23:24 (6)
  10-31 10:50:00 - 100ms : 11:22:33:44:55:66 (7)
DUMP OF SERVICE audio:
  10-31 10:04:17:429 BT connected: addr=D0:4B:1B:C3:23:24 profile=2 state=2 codec=AUDIO_FORMAT_SBC
  10-31 10:04:18:000 something about 11:22:33:44:55:66 BT connected
DUMP OF SERVICE wifip2p:
  rec[84]: time=10-31 10:46:41.344 processed=A org=A dest=B what=WifiP2pManager.DISCOVER_PEERS sender=com.aitowe.aitoglasses(10096)
  rec[85]: time=10-31 10:46:42.000 processed=A org=A dest=B what=WifiP2pManager.DISCOVER_PEERS sender=com.other.app(10200)
  rec[93]: time=10-31 10:46:47.259 processed=A org=A dest=B what=WifiP2pMonitor.P2P_PROV_DISC_PBC_RSP_EVENT sender=null(-1)
  mWifiP2pInfo groupFormed: true isGroupOwner: true groupOwnerAddress: /192.168.49.1
  mGroup network: DIRECT-ab-Phone
  isGO: true
  passphrase: SECRETPASS123
  Client: Device: AIMB-G2_D04B1BC32324
  deviceAddress: 60:c2:2a:65:83:63
DUMP OF SERVICE usagestats:
  time="2026-10-31 10:46:41" type=ACTIVITY_RESUMED package=com.aitowe.aitoglasses class=com.aitowe.aitoglasses.MainActivity instanceId=1
  time="2026-10-31 10:47:00" type=ACTIVITY_RESUMED package=com.other.app class=com.other.app.Home instanceId=2
------ DETAILED SOCKET STATE (ss -eionptu) ------
tcp   ESTAB   0   0   [::ffff:192.168.29.217]:44772   [::ffff:47.92.167.140]:443   users:(("owe.aitoglasses",pid=1,fd=5)) uid:10096
tcp   ESTAB   0   0   [::ffff:192.168.29.217]:44800   [::ffff:8.8.8.8]:443   users:(("chrome",pid=2,fd=9)) uid:10200
"""


def make_zip(tmp_path: Path, snoop_size: int | None = None) -> Path:
    path = tmp_path / "bugreport-test.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bugreport-test-2026-10-31.txt", REPORT)
        if snoop_size is not None:
            zf.writestr("FS/data/misc/bluetooth/logs/BT_HCI_x.cfa", bytes(snoop_size))
    return path


def test_triage_extracts_only_cyan_and_glasses_facts(tmp_path: Path) -> None:
    result = triage_zip(make_zip(tmp_path, snoop_size=16))
    assert result.snoop_files == [("FS/data/misc/bluetooth/logs/BT_HCI_x.cfa", 16)]
    assert result.gatt_connections == ["10-31 10:46:08 - 42683ms : D0:4B:1B:C3:23:24 (6)"]
    assert len(result.audio_events) == 1 and "11:22" not in result.audio_events[0]
    assert [what for _t, what in result.p2p_events] == [
        "DISCOVER_PEERS",
        "P2P_PROV_DISC_PBC_RSP_EVENT",
    ]
    group = result.p2p_group
    assert group.network == "DIRECT-ab-Phone" and group.phone_is_group_owner is True
    assert group.group_owner_ip == "192.168.49.1"
    assert group.glasses_p2p_address == "60:c2:2a:65:83:63"
    assert result.app_events == [("2026-10-31 10:46:41", "ACTIVITY_RESUMED", "MainActivity")]
    assert result.remote_endpoints == {"[47.92.167.140]:443"}


def test_triage_never_collects_secrets_or_phone_addresses(tmp_path: Path) -> None:
    text = repr(triage_zip(make_zip(tmp_path)))
    assert "SECRETPASS" not in text
    assert "192.168.29.217" not in text


def test_triage_command_flags_empty_log(tmp_path: Path) -> None:
    make_zip(tmp_path, snoop_size=16)
    result = CliRunner().invoke(cli.app, ["capture", "triage", str(tmp_path)])
    assert result.exit_code == 0
    assert "EMPTY" in result.output and "Wi-Fi Direct" in result.output
    assert "group owner" in result.output


def test_triage_command_without_zips(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.app, ["capture", "triage", str(tmp_path)])
    assert result.exit_code == 1


@pytest.mark.parametrize("size", [0, 16])
def test_zero_packet_log_sizes(tmp_path: Path, size: int) -> None:
    assert triage_zip(make_zip(tmp_path, snoop_size=size)).snoop_files[0][1] == size
