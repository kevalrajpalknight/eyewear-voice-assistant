# aiglasses

Python core library and CLI for AIMB-G2 AI glasses. Personal interoperability project, tested on Windows 11.

## Quickstart (Windows / PowerShell)

```powershell
uv sync
uv run aiglasses --help
uv run aiglasses scan            # find the glasses, saves address to config.toml
uv run aiglasses info            # device name / hardware / firmware (read-only)
uv run aiglasses gatt-dump       # logs/gatt_dump.json
uv run aiglasses listen --duration 60
uv run aiglasses capabilities    # what is CONFIRMED on this unit
uv run aiglasses voice candidates   # offline: decode the logged voice bursts to logs/decode_candidates/
uv run aiglasses capture analyze captures\bugreport.zip   # Phase 3, see docs/CAPTURE.md
```

Turn Bluetooth on in Windows and make sure the glasses are awake and not connected to a phone.

## Development

```powershell
uv run nox                       # lint, typecheck, tests
uv run pre-commit install
```

## Safety

Nothing is ever written to the glasses unless the exact payload came from an observed capture of the
official app (`protocol/commands.yaml`) or you approve it in-session. Firmware/OTA-looking commands are always
refused. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Status

Phases 0 (scaffold), 1 (read-only BLE core) and 2 (Bluetooth audio) complete; Phase 3 tooling is ready and waits for your phone capture. See [docs/CAPABILITIES.md](docs/CAPABILITIES.md) and [docs/PROTOCOL.md](docs/PROTOCOL.md).
