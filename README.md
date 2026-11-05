# EVA — Eyewear Voice Assistant

**A friendly, experimental voice assistant for AIMB-G2 smart glasses.**

EVA connects the glasses to your Windows PC, explores their Bluetooth features, and provides the building blocks for a voice assistant. The project is designed for curious glasses owners and developers alike: you can start with simple device checks, or dive into the Python library and assistant code.

> **A personal interoperability project — not an official product from the glasses' manufacturer.** Some features are verified on real hardware; others are experimental or still need testing. See [what works today](#what-works-today) before trying a feature.

## What EVA can do

| For glasses owners | For builders |
|---|---|
| Check whether the glasses are nearby and awake | Use an asynchronous Python library and command-line tools |
| Read device details, battery level, and media counts | Explore the Bluetooth protocol and captured device behavior |
| Listen to and decode a glasses voice recording | Experiment with speech recognition, text-to-speech, and language-model routing |
| Try an experimental voice-assistant workflow | Extend the assistant with tools and alternate model providers |

### The idea, in everyday terms

1. **You speak** using the glasses' rear button.
2. **Your PC turns speech into text** using a speech-recognition model.
3. **EVA works out what to do** — for example, answer a simple question or use an enabled tool.
4. **A voice reads the answer aloud** through the selected audio output.

The glasses and PC communicate over Bluetooth. Depending on your settings and the feature being used, EVA may also connect to a model provider or an online search service.

## What works today

The project keeps a record of what has been verified, what is inferred, and what still needs testing. Highlights:

- **Bluetooth discovery and device information:** confirmed on Windows 11.
- **Battery and media counts:** confirmed read-only device checks.
- **Voice decoding:** a recorded glasses voice stream has been decoded and listened to.
- **Taking a photo:** supported as an explicit, one-photo action that asks for confirmation.
- **Voice assistant:** the software and setup checks are available, but a complete live conversation with real glasses, a model provider, and audible output has not yet been verified end to end.

For the detailed status and known limitations, see [Capabilities](docs/CAPABILITIES.md). For the assistant's design and verification gates, see [Assistant design](docs/ASSISTANT_DESIGN.md).

## Try it on Windows

### You will need

- AIMB-G2 smart glasses
- Windows 11 with Bluetooth turned on
- [uv](https://docs.astral.sh/uv/) to install and run the project

Before scanning, wake the glasses and disconnect them from the phone app.

### 1. Install and find your glasses

Open PowerShell in this project folder:

```powershell
uv sync
uv run aiglasses --help
uv run aiglasses scan
```

### 2. Explore safely

These commands inspect the glasses or listen for events:

```powershell
uv run aiglasses info
uv run aiglasses capabilities
uv run aiglasses gatt-dump
uv run aiglasses listen --duration 60
uv run aiglasses ai-voice record
uv run aiglasses voice candidates
```

`scan` saves the discovered address to `config.toml`. The voice recording command is read-only and saves a WAV file under `captures/`; the voice candidate command works offline with logged voice data.

### 3. Try the experimental voice assistant

Install the optional assistant dependencies, then configure a language-model provider:

```powershell
uv sync --extra assistant --extra research --extra llm
Copy-Item .env.example .env
```

Edit `.env` with your provider, model, and API key, then check the setup before starting:

```powershell
uv run aiglasses assistant check
uv run aiglasses assistant run
```

The current quickstart uses the glasses' microphone and PC speakers. Local speech recognition is used; when a cloud language model is configured, the assistant sends it the recognized text, not the raw voice recording. Logs are designed not to retain transcripts by default. Review [Configuration](docs/CONFIG.md) for provider choices, privacy settings, optional local models, and other details. **The full live setup is experimental and has not been verified end to end**, so check [Capabilities](docs/CAPABILITIES.md) and [Assistant design](docs/ASSISTANT_DESIGN.md) first.

## Built with safety and evidence in mind

- **No surprise writes:** Bluetooth commands pass through a write guard. Only confirmed commands or exact payloads approved for the session can be sent; dangerous update and reset operations are refused.
- **Your choice to proceed:** photo capture asks for confirmation. Hardware tests that could change device state should only be done with the person using the glasses present.
- **Measured versus unknown:** capability notes distinguish confirmed behavior from inferences and unverified features.
- **Privacy-conscious defaults:** secrets are redacted in checks and logs; transcripts and audio are not saved by default.

Read the [architecture and write-safety notes](docs/ARCHITECTURE.md) before adding or testing Bluetooth commands.

## For developers

The library can be used directly from Python:

```python
async with Glasses.connect(Settings(), dry_run=False) as glasses:
    print(await glasses.read_battery())
    print(await glasses.read_device_info())
    print(await glasses.read_media_counts())

    async for event in glasses.events():
        print(event.kind, event.fields)
```

The code is organized in layers: the command-line interface and glasses facade sit above services, safety checks, protocol code, and transports. The assistant is separated into speech, listening, conversation, language-model, and tool packages. See [Architecture](docs/ARCHITECTURE.md) for the map.

To run the project checks:

```powershell
uv run nox
```

## Project guide

- [Capabilities and verification status](docs/CAPABILITIES.md)
- [Architecture and write safety](docs/ARCHITECTURE.md)
- [Assistant design and testing gates](docs/ASSISTANT_DESIGN.md)
- [Configuration, providers, and privacy options](docs/CONFIG.md)
- [Protocol notes](docs/PROTOCOL.md)
- [Capture and analysis guide](docs/CAPTURE.md)

---

**EVA is a work in progress.** If you are new to smart-glasses development, start with the Windows quickstart and the capability table. If you are a developer, the architecture and protocol notes are the best place to begin.
