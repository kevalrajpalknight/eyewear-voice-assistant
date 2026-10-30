# aiglasses

Python core library and CLI for AIMB-G2 AI glasses. Personal interoperability project, tested on Windows 11.

## Quickstart (Windows / PowerShell)

```powershell
uv sync
uv run aiglasses --help
uv run aiglasses scan            # find the glasses, saves address to config.toml
uv run aiglasses capabilities    # what is CONFIRMED on this unit
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

Phase 0 (scaffold) complete. See [docs/CAPABILITIES.md](docs/CAPABILITIES.md) and [docs/PROTOCOL.md](docs/PROTOCOL.md).

`enumerate.py`, `listen.py` and `common.py` at the repo root are the original prototypes; Phase 1 replaces them.
