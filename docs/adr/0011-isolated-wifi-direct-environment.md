# 0011 Isolated environment for the Wi-Fi Direct helper (updates ADR 0006)

Status: accepted 2026-11-01 (the user approved an isolated environment; this replaces the wait
until 2026-11-07 for that environment only, never for the main BLE environment).

## Decision
The Wi-Fi Direct code runs in a separate process from its own gitignored virtual environment,
`tools/wifidirect-env/` (Python 3.12, created with `uv venv`). Because it shares nothing with the
main environment, ADR 0006's conflict between the winrt 3.x and 4.x generations disappears: the main
environment keeps winrt 4.x for bleak (unchanged: `winrt-runtime 4.0.3`), the helper uses the
generation that already satisfies the 14-day rule today.

## Pins (all released 2025-06-06 unless noted; PyPI wheels, no compiler)
`winrt-runtime==3.2.1`, `winrt-Windows.Devices.WiFiDirect==3.2.1`, `winrt-Windows.Foundation==3.2.1`,
`winrt-Windows.Foundation.Collections==3.2.1`, `winrt-Windows.Storage.Streams==3.2.1`,
`winrt-Windows.Devices.Enumeration==3.2.1`, `winrt-Windows.Networking==3.2.1`,
`winrt-Windows.Networking.Connectivity==3.2.1`, plus `typing-extensions==4.16.0` as their dependency.
Licence MIT (pywinrt). Import of `winrt.windows.devices.wifidirect` was checked in that environment;
no radio call was made.

## Install (repeatable)
```
uv venv tools/wifidirect-env --python 3.12
uv pip install --python tools/wifidirect-env/Scripts/python.exe winrt-runtime==3.2.1 winrt-Windows.Devices.WiFiDirect==3.2.1 winrt-Windows.Foundation==3.2.1 winrt-Windows.Foundation.Collections==3.2.1 winrt-Windows.Storage.Streams==3.2.1 winrt-Windows.Devices.Enumeration==3.2.1 winrt-Windows.Networking==3.2.1 winrt-Windows.Networking.Connectivity==3.2.1
```

## Consequences
- `pyproject.toml` and `uv.lock` of the main project are untouched by this.
- The helper must never import `aiglasses` and is started only by `SubprocessHelper` with a fixed
  command line from settings (`WIFI_DIRECT_HELPER_PYTHON`).
- The real radio backend is unverified; the first real connection is a supervised test with the exact
  bytes approved first (docs/WIFI_DIRECT_HELPER.md).
