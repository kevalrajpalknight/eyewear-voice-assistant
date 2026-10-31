# ADR 0006: Wi-Fi Direct dependency (winrt-Windows.Devices.WiFiDirect) and its pins

**Status:** proposed; nothing is installed or pinned in `pyproject.toml` yet. Needs the user's choice below.

Step 2 of the Phase 4 plan needs the PC to take part in Wi-Fi Direct. The Python route is `winrt-Windows.Devices.WiFiDirect`, which also needs `winrt-runtime` and the
`winrt-Windows.Foundation`, `Foundation.Collections`, `Storage.Streams` and `Devices.Enumeration` packages. Rule from the user: pin these to versions at least two weeks old.

Facts checked on PyPI (today is 2026-10-31):

| Generation | Release date | Python needed |
|---|---|---|
| 3.2.1 (all six packages) | 2025-06-06 | 3.9 or newer |
| 4!10.0.28000.2705 (the five Windows packages) and `winrt-runtime` 4.0.x | 2026-10-24 | 3.11 or newer (3.12 is fine) |

The two generations cannot be installed together (`winrt-runtime` 3 versus 4). The project's BLE stack (bleak 3.0.2) already runs on the 4.x generation on Python 3.11 and newer, and on 3.2.1 on
Python 3.10. So any pin of the Wi-Fi Direct package decides the version of the whole `winrt` family, including the packages the working BLE code uses. The 4.x set is only 7 days old, so it fails the
two-week rule today.

## Options

- **A. Pin the whole family to 3.2.1 now.** Old enough and works on Python 3.9 and newer, but it replaces the 4.x packages under the working BLE stack with 3.2.1 (which bleak already supports on
  3.10). The BLE tests would have to be repeated on the hardware.
- **B. Wait until 2026-11-07 and pin the 4.x set** (`winrt-Windows.Devices.WiFiDirect==4!10.0.28000.2705`, `winrt-runtime==4.0.3`, and the four supporting `4!10.0.28000.2705` packages). It needs Python
  3.11 or newer (the dev environment is 3.12, so no change; the nox 3.10 session would keep using 3.2.1 through bleak's own markers). No change to the BLE stack.

**Recommendation:** B. Step 2 is not the next step anyway, one week costs nothing, and the BLE stack stays as tested. Use A only if Step 2 must happen sooner.

Until the user chooses, `pyproject.toml` and `uv.lock` are untouched (a change would make `uv run` silently re-lock and reinstall the winrt packages).
