# Architecture

Layered, asyncio throughout, no I/O in core logic.

```
cli ──► glasses (facade) ──► services ──► safety.WriteGuard ──► transport (BLE / Wi-Fi / Audio)
                                  │
                                  └──► protocol (pure: codec, command registry)
domain (dataclasses, enums, errors) is shared by every layer.
```

| Package | Role |
|---|---|
| `domain/` | Dataclasses, `Capability`, `Confidence`, errors. |
| `protocol/` | Pure functions. `FrameCodec` protocol (swappable; `RawCodec` until a capture proves a framing). `commands.yaml` registry; CONFIRMED entries must cite a capture. |
| `transport/` | `BleTransport`, `WifiLink`, `AudioIO` protocols, fakes for tests, `TracingTransport` (trace.jsonl), bleak scan. Real BLE/Wi-Fi/audio implementations land in Phases 1, 4 and 2. |
| `safety.py` | `WriteGuard`: exact-payload allowlist, OTA name denylist, dry-run (default on), audit log. |
| `services/` | Device, Camera, Audio, Events. Unverified features raise `NotSupportedError`. |
| `glasses.py` | `async with Glasses.connect(config, transport=...) as g:` facade plus `g.capabilities()`. |
| `config.py` | pydantic-settings; `config.toml`, `AIGLASSES_*` env vars. |

## Write safety

1. Allowlist is built only from commands that are `CONFIRMED`, direction `write`, `safe_to_send: true`.
2. Session approval (`guard.approve`) covers one exact payload.
3. The denylist matches command names (ota, upgrade, update, dfu, flash, firmware, bootloader, reboot) and wins over approval.
4. Known limit: the denylist is name-based. Once the real opcodes are captured (Phase 3) an opcode/byte-level denylist will be added.
5. `dry_run=True` by default; nothing is sent until the caller opts in.

## Layout notes

Files are small and focused. Logs (`logs/`) and captures (`captures/`) are gitignored.
