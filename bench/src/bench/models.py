"""Download benchmark models from their official sources and record or verify checksums.

Hugging Face files are pinned to a commit and checked against the LFS sha256 the hub publishes.
GitHub release assets publish no digest, so their sha256 is recorded on first download (trust on
first use) and verified on every later run.
"""

import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models"
MANIFEST = ROOT / "models_manifest.json"

HF = "https://huggingface.co"
HF_FILES = {
    "faster-whisper-base.en": (
        "Systran/faster-whisper-base.en",
        "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
        ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
        {"model.bin": "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef"},
    ),
    "faster-whisper-small.en": (
        "Systran/faster-whisper-small.en",
        "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
        ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
        {"model.bin": "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a"},
    ),
    "faster-whisper-small": (
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
        ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
        {"model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"},
    ),
    "parakeet-tdt-0.6b-v2-int8": (
        "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
        "1ab9323565ddb038682214b292f588070a538ce2",
        ["decoder.int8.onnx", "encoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"],
        {
            "decoder.int8.onnx": "b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e",
            "encoder.int8.onnx": "a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab",
            "joiner.int8.onnx": "7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2",
        },
    ),
}
GITHUB = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_FILES = ["kokoro-v1.0.onnx", "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    part = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as out:
        while block := response.read(1 << 20):
            out.write(block)
    part.replace(target)


def _manifest() -> dict[str, dict[str, str]]:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def _record(
    manifest: dict[str, dict[str, str]],
    key: str,
    path: Path,
    source: str,
    rev: str,
    expected: str | None,
) -> None:
    actual = sha256_of(path)
    if expected is not None and actual != expected:
        raise SystemExit(f"checksum mismatch for {path.name}: {actual} != {expected}")
    known = manifest.get(key, {}).get("sha256")
    if known is not None and known != actual:
        raise SystemExit(f"{path.name} changed since it was first recorded")
    manifest[key] = {
        "source": source, "revision": rev, "sha256": actual,
        "checked": "published LFS sha256" if expected else "trust on first use",
    }  # fmt: skip


def download_all() -> None:
    manifest = _manifest()
    for name, (repo, rev, files, checksums) in HF_FILES.items():
        for file in files:
            target = MODELS / name / file
            url = f"{HF}/{repo}/resolve/{rev}/{file}"
            _fetch(url, target)
            _record(manifest, f"{name}/{file}", target, url, rev, checksums.get(file))
    for file in KOKORO_FILES:
        target = MODELS / "kokoro" / file
        url = f"{GITHUB}/{file}"
        _fetch(url, target)
        _record(manifest, f"kokoro/{file}", target, url, "model-files-v1.0", None)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"{len(manifest)} files verified or recorded in {MANIFEST.name}")


if __name__ == "__main__":
    download_all()
