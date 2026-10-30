# ADR 0002: Dependencies

**Status:** accepted

| Dependency | Why |
|---|---|
| bleak | Cross-platform BLE (WinRT on Windows) |
| typer, rich | CLI and readable tables |
| pydantic-settings[toml] | Typed config from config.toml and env |
| pyyaml | Command registry file |
| sounddevice, soundfile, numpy | Bluetooth audio playback and recording (Phase 2) |

Dev: pytest, pytest-asyncio, pytest-cov, mypy, ruff, pre-commit, nox. HTTP for Wi-Fi probing will use the standard
library unless Phase 4 shows a need for more.
