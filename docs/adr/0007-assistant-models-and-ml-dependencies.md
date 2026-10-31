# ADR 0007: Assistant models and ML dependencies (bench/ project)

**Status:** accepted for benchmarking only. Nothing here is a dependency of the `aiglasses` package; the runtime choice (STT default) waits for Gate A.

## Isolation
All heavy ML packages live in `bench/` (its own `pyproject.toml`, `uv.lock` and virtual environment, Python 3.13 in the lock). The BLE stack and the root environment do not import or install
any of them. No `winrt` package is installed there or anywhere (`uv pip list` in `bench/` shows none), so ADR 0006 and the 2026-11-07 decision are untouched.

## Pins (rule: at least 14 days old)
`bench/pyproject.toml` sets `[tool.uv] exclude-newer = "2026-10-17T00:00:00Z"`, so every direct and transitive package resolves to a release at least 14 days older than 2026-10-31.
Direct pins: `faster-whisper==1.2.1`, `ctranslate2==4.8.3`, `moonshine-voice==0.1.5`, `kokoro-onnx==0.6.1`, `onnxruntime==1.31.0`, `tokenizers==0.23.3`,
`huggingface-hub==2.2.0`, `jiwer==4.0.0`, `psutil==7.2.2`. (`sherpa-onnx` was not benchmarked; its newest release is under 14 days old, 1.13.8 is the last older one.)

## Models: source, revision, licence, checksum
Recorded by `bench/src/bench/models.py` in `bench/models_manifest.json` (and `bench/models/moonshine_files.json` for Moonshine). "Published" = the hub publishes the sha256 and we verified it.
"First use" = the source publishes no digest, so the sha256 was recorded on first download and is verified on every later run.

| Model | Official source | Revision | Licence | Checksum |
|---|---|---|---|---|
| faster-whisper base.en (CTranslate2) | huggingface.co/Systran/faster-whisper-base.en | commit 3d3d5dee26484f91867d81cb899cfcf72b96be6c | MIT (model card) | model.bin published; small files first use |
| faster-whisper small.en | huggingface.co/Systran/faster-whisper-small.en | commit d1d751a5f8271d482d14ca55d9e2deeebbae577f | MIT (model card) | model.bin published; small files first use |
| Moonshine tiny-en, base-en (quantized .ort) | download.moonshine.ai via `moonshine-voice` 0.1.5 | the package's own catalogue (no commit id published) | MIT per the PyPI metadata for the package; the package warns that non-English models are non-commercial, English is not flagged | first use |
| Kokoro v1.0 ONNX (fp32 and int8) and voices-v1.0.bin | github.com/thewh1teagle/kokoro-onnx release `model-files-v1.0` (community ONNX export of hexgrad/Kokoro-82M) | release tag model-files-v1.0 | Kokoro-82M is Apache-2.0 per its authors; the export's own licence line is UNVERIFIED | first use |

Native pieces on Windows: none to install by hand. `kokoro-onnx` pulls `espeakng-loader` 0.2.4, which bundles espeak-ng, and `phonemizer` 3.4.0. Both are GPL-licensed; fine for this personal,
undistributed project, but they would matter if the assistant were ever shipped.

## Addendum 2026-10-31: root `assistant` extra
The echo loop runs from the root project, so `pyproject.toml` now has an optional `assistant` extra (`moonshine-voice==0.1.5`, `faster-whisper==1.2.1`, `ctranslate2==4.8.3`,
`kokoro-onnx==0.6.1`, `onnxruntime==1.31.0`; the last two only on Python 3.11 or newer). `[tool.uv] constraint-dependencies` holds the new transitive packages (`huggingface-hub`, `hf-xet`,
`httpx2`, `httpcore2`, `certifi`) to releases from 2026-10-17 or earlier; a script check of `uv.lock` found none of the 33 added packages newer than that. `uv lock` changed no existing package
(BLE stack and winrt untouched). The models are still downloaded only by `bench/`; the echo loop reads `bench/models` and verifies sha256 against `bench/models_manifest.json` before loading.

## Consequences
- If Gate A picks a model, the runtime dependency gets its own ADR and optional extra, again pinned at least 14 days old.
- Downloads happen only through `python -m bench.models` (and Moonshine's own downloader), never at import time.
