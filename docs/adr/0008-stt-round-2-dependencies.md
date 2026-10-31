# 0008 STT round 2 models and dependencies

Status: accepted for benchmarking; the default STT is not decided.

| Item | Version / revision | Licence | Source and check |
|---|---|---|---|
| sherpa-onnx, sherpa-onnx-core | 1.13.8 (2026-09-10) | Apache-2.0 | PyPI; 2.0.0/2.0.1 skipped as younger than 14 days |
| rapidfuzz | 3.14.6 (2026-08-30), Python >=3.11 | MIT | PyPI |
| jellyfish | 1.2.1 (2025-10-11) | MIT | PyPI |
| Parakeet TDT 0.6B v2 int8 | csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8 @ 1ab9323565dd | CC-BY-4.0 (NVIDIA) | HF revision pin, LFS SHA-256 checked in `bench/models_manifest.json` |
| faster-whisper small | Systran/faster-whisper-small @ 536b0662742c | MIT | HF revision pin, LFS SHA-256 of model.bin checked |
| Moonshine v2 small / medium streaming | moonshine-voice 0.1.5, download.moonshine.ai `quantized_26_08_21` | MIT | SHA-256 recorded on first download in `bench/models/moonshine_files.json` |

Dependencies live in the optional `assistant` extra and in `bench/`. `uv.lock` gained four
packages (jellyfish, rapidfuzz, sherpa-onnx, sherpa-onnx-core); no existing package changed.
Windows note: sherpa-onnx needs `sherpa-onnx-core` or it loads Windows' older `onnxruntime.dll`.
