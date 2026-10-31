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
uv run aiglasses photo           # ONE photo; asks you to confirm you are watching (you type the answer)
uv run aiglasses ai-voice record # read-only: hold the rear click, saves a WAV under captures/
uv run aiglasses voice candidates   # offline: decode the logged voice bursts to logs/decode_candidates/
uv run aiglasses capture analyze captures\bugreport.zip   # Phase 3, see docs/CAPTURE.md
```

Turn Bluetooth on in Windows and make sure the glasses are awake and not connected to a phone.

## Voice assistant quickstart

Single-click the rear button, speak, hear the answer on the PC speakers. Speech is recognised
locally; only the text of your request goes to the LLM provider you choose.

```powershell
uv sync --extra assistant --extra research --extra llm
python -m bench.models                  # in bench/: download the speech models (checksummed)
copy .env.example .env                  # then edit .env: LLM_PROVIDER, LLM_MODEL, LLM_API_KEY
uv run aiglasses assistant check        # validates everything; secrets show only as set / NOT set
uv run aiglasses assistant run          # glasses mic -> STT -> LLM -> TTS -> PC speakers
```

Close the phone app first and keep the phone's Bluetooth off. The glasses end a voice session
about 4.8 s after the click, so speak straight away and keep requests short; a second click soon
after a cut-off request is joined to it. Changing provider, model, router, speech engine, voice or
speed means editing `.env` only: see `docs/CONFIG.md`. `assistant run --wav file.wav --output null`
runs one turn from a recording without sound; `assistant echo` repeats what it heard (no LLM).

### Local model setup (optional, `ROUTER_MODE=local_first`)

```powershell
uv sync --extra assistant --extra research --extra llm --extra search
uv run aiglasses models pull llama.cpp            # llama-server, sha256 checked, into tools/
uv run aiglasses models list                      # sizes, licences, 8 GB download budget
uv run aiglasses models pull qwen3-1.7b-q4_k_m    # into models/ (gitignored), sha256 checked
# in .env: ROUTER_MODE=local_first   LOCAL_LLM_MODEL=qwen3-1.7b-q4_k_m   TOOLS_ENABLED=...
uv run aiglasses assistant check                  # shows the resolved local settings
uv run aiglasses assistant run                    # starts the local server itself (127.0.0.1)
```

`aiglasses llm-server start|status|stop` runs the server on its own. The server cannot outlive the
program (Windows job object). If it is down, requests go to the cloud model. Free some RAM first:
a 1 to 4 GB model needs headroom. Details: `docs/ASSISTANT_DESIGN.md`, `docs/CONFIG.md`.

## Library

```python
async with Glasses.connect(Settings(), dry_run=False) as g:
    print(await g.read_battery(), await g.read_device_info(), await g.read_media_counts())
    async for event in g.events():            # typed 0x73 events and the 0x59 voice stream
        print(event.kind, event.fields)
    # await g.take_photo(confirm=True)        # state-changing: only when the person present agrees
```

Every request goes through WriteGuard; only the three confirmed reads and the confirmed photo are sendable.

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
