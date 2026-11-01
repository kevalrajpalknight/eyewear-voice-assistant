"""Model and runtime registry: what may be downloaded, from where, and how it is verified.

Sources are fixed in the registry file (Hugging Face at a pinned commit, GitHub release assets).
Every file has a sha256 that is checked before it is kept. Tokens (HF_TOKEN) are read only from the
environment, only for gated models, and are never sent to another host or printed.
"""

import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from aiglasses.domain.errors import ConfigError, GlassesError

RUNTIME_NAME = "llama.cpp"
HF_HOST = "huggingface.co"
HF_URL = "https://huggingface.co/{repo}/resolve/{revision}/{filename}"
CHUNK = 1 << 20
GB = 1_000_000_000
TOKEN_VARIABLE = "HF_TOKEN"


class DownloadBudgetExceeded(GlassesError):
    """The download would go beyond MODEL_DOWNLOAD_BUDGET_GB. Ask the user before raising it."""


class GatedModel(GlassesError):
    """The model needs a license click on Hugging Face; this program does not attempt it."""


class ChecksumMismatch(GlassesError):
    pass


@dataclass(frozen=True)
class FileSpec:
    filename: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    revision: str
    files: tuple[FileSpec, ...]  # the model file first, then companions (e.g. the vision projector)
    license: str
    role: str
    kind: str
    gated: bool

    @property
    def main(self) -> FileSpec:
        return self.files[0]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


@dataclass(frozen=True)
class RuntimeSpec:
    release: str
    url: str
    filename: str
    sha256: str
    size: int
    license: str


class Registry:
    def __init__(self, runtime: RuntimeSpec, models: dict[str, ModelSpec]) -> None:
        self.runtime, self.models = runtime, models

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        try:
            text = (
                path.read_text(encoding="utf-8")
                if path is not None
                else resources.files("aiglasses.assistant.llm")
                .joinpath("models.yaml")
                .read_text("utf-8")
            )
            raw = yaml.safe_load(text)
            rt = raw["runtime"][RUNTIME_NAME]
            runtime = RuntimeSpec(
                rt["release"],
                rt["url"],
                rt["filename"],
                rt["sha256"],
                int(rt["size"]),
                rt["license"],
            )
            models = {}
            for name, m in raw["models"].items():
                files = (
                    FileSpec(m["filename"], m["sha256"], int(m["size"])),
                    *(
                        FileSpec(c["filename"], c["sha256"], int(c["size"]))
                        for c in m.get("companions") or []
                    ),
                )
                models[name] = ModelSpec(
                    name, m["repo"], m["revision"], files, m["license"], m["role"], m["kind"],
                    bool(m["gated"]),
                )  # fmt: skip
            return cls(runtime, models)
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
            raise ConfigError(f"model registry is invalid: {type(exc).__name__}") from exc

    def get(self, name: str) -> ModelSpec:
        if name not in self.models:
            raise ConfigError(f"unknown model {name!r}; valid: {', '.join(sorted(self.models))}")
        return self.models[name]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def model_path(models_dir: Path, spec: ModelSpec, file: FileSpec | None = None) -> Path:
    return models_dir / spec.name / (file or spec.main).filename


class _SameHostAuth(urllib.request.HTTPRedirectHandler):
    """Drop the Authorization header when a redirect leaves the original host."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname != req.host.split(":")[0]:
            new.remove_header("Authorization")
        return new


Progress = Callable[[int, int], None]


def download(
    url: str,
    target: Path,
    *,
    sha256: str,
    size: int,
    token: str | None = None,
    progress: Progress | None = None,
) -> None:
    """Fetch `url` to `target` (resuming a .part file), verify size and sha256, then keep it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    done = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "aiglasses-models"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if done:
        headers["Range"] = f"bytes={done}-"
    opener = urllib.request.build_opener(_SameHostAuth)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=60) as response:
            resumed = done > 0 and response.status == 206
            with part.open("ab" if resumed else "wb") as out:
                done = done if resumed else 0
                while block := response.read(CHUNK):
                    out.write(block)
                    done += len(block)
                    if progress:
                        progress(done, size)
    except urllib.error.HTTPError as exc:
        raise GlassesError(
            f"download failed: HTTP {exc.code} for {urllib.parse.urlparse(url).hostname}"
        ) from exc
    if part.stat().st_size != size or sha256_of(part) != sha256:
        part.unlink()
        raise ChecksumMismatch(
            f"{target.name}: size or sha256 does not match the registry; deleted"
        )
    part.replace(target)


def installed_bytes(models_dir: Path, registry: Registry) -> int:
    total = 0
    for spec in registry.models.values():
        for file in spec.files:
            path = model_path(models_dir, spec, file)
            if path.exists():
                total += path.stat().st_size
    return total


def check_budget(models_dir: Path, registry: Registry, spec: ModelSpec, budget_gb: float) -> None:
    missing = sum(f.size for f in spec.files if not model_path(models_dir, spec, f).exists())
    if installed_bytes(models_dir, registry) + missing > budget_gb * GB:
        raise DownloadBudgetExceeded(
            f"pulling {spec.name} ({missing / GB:.2f} GB) would go beyond the "
            f"{budget_gb:g} GB budget (MODEL_DOWNLOAD_BUDGET_GB); ask before raising it"
        )


def pull_model(
    registry: Registry,
    name: str,
    models_dir: Path,
    budget_gb: float,
    *,
    progress: Progress | None = None,
    url_for: Callable[[ModelSpec, FileSpec], str] | None = None,
) -> list[Path]:
    spec = registry.get(name)
    token = os.environ.get(TOKEN_VARIABLE) or None
    if spec.gated and not token:
        raise GatedModel(
            f"{name} is gated on Hugging Face: accept its license there yourself and set "
            f"{TOKEN_VARIABLE} in your environment (never paste a token in chat). Skipped."
        )
    check_budget(models_dir, registry, spec, budget_gb)
    paths = []
    for file in spec.files:
        path = model_path(models_dir, spec, file)
        if not (path.exists() and path.stat().st_size == file.size):
            url = (url_for or default_url)(spec, file)
            download(
                url, path, sha256=file.sha256, size=file.size,
                token=token if spec.gated else None, progress=progress,
            )  # fmt: skip
        paths.append(path)
    return paths


def default_url(spec: ModelSpec, file: FileSpec) -> str:
    return HF_URL.format(repo=spec.repo, revision=spec.revision, filename=file.filename)


def verify_model(registry: Registry, name: str, models_dir: Path) -> dict[str, str]:
    """Per file: ok, missing or MISMATCH. Hashes the files on disk."""
    spec = registry.get(name)
    report = {}
    for file in spec.files:
        path = model_path(models_dir, spec, file)
        if not path.exists():
            report[file.filename] = "missing"
        else:
            report[file.filename] = "ok" if sha256_of(path) == file.sha256 else "MISMATCH"
    return report


def install_runtime(
    registry: Registry,
    bin_dir: Path,
    *,
    progress: Progress | None = None,
    url: str | None = None,
) -> Path:
    """Download the llama.cpp release zip (sha256 checked) and unpack it into `bin_dir`."""
    rt = registry.runtime
    archive = bin_dir.parent / f"{bin_dir.name}-download" / rt.filename
    if not archive.exists():
        download(url or rt.url, archive, sha256=rt.sha256, size=rt.size, progress=progress)
    bin_dir.mkdir(parents=True, exist_ok=True)
    root = bin_dir.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            if not (root / member).resolve().is_relative_to(root):
                raise GlassesError(f"unsafe path in {rt.filename}")
        bundle.extractall(bin_dir)
    (bin_dir / "VERSION").write_text(f"{rt.release}\n{rt.sha256}\n", encoding="utf-8")
    return bin_dir
