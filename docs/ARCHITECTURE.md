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

## Assistant package layout (`src/aiglasses/assistant/`)
| Package | What lives there |
|---|---|
| `agent/` | The brain: `AgentClient` (LangChain `create_agent`), model routing middleware, tool adapters, tool hints (`hints.yaml`), the rule fast path, turn state. The only place besides `llm/llm_client.py` that may import LangChain or LangGraph (enforced by `tests/test_architecture.py`). |
| `conversation/` | The turn pipeline, the turn runner and logs, memory, intents (`intents.yaml`), fillers, escalation rules, the persona and the app loop with reconnects. |
| `speech/` | Speech to text and text to speech engines, the engine factory, text clean-up, chunking for natural speech, the speaker worker process. |
| `listening/` | Microphone worker, voice detector, wake phrases, echo guard, sounds and thanks filter, turn-end rules, request joining. |
| `llm/` | The cloud chat models (`llm_client.py`), the managed local `llama-server`, the model registry and its downloads. |
| `tools/` | The tools the assistant can use (device, memory, search, vision, time and weather) and the registry that enforces risk classes. |
| `run/` | Starting the assistant, the configuration check, offline diagnosis, benchmark sessions. |
| `testing/` | Fakes for tests and the offline diagnosis. |

Data files (YAML, the persona) sit next to the code that reads them.
