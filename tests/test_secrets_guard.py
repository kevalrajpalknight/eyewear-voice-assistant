import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load_guard() -> ModuleType:
    path = ROOT / "scripts" / "check_secrets.py"
    spec = importlib.util.spec_from_file_location("check_secrets", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_secrets"] = module
    spec.loader.exec_module(module)
    return module


def fake_key() -> str:
    return "sk-" + "A1b2C3d4" * 4  # assembled at run time so this file stays clean


def test_env_files_are_blocked_but_the_example_is_allowed(tmp_path: Path) -> None:
    guard = load_guard()
    (tmp_path / ".env").write_text("A=1\n")
    (tmp_path / ".env.example").write_text("LLM_API_KEY=\n")
    assert guard.problems(tmp_path / ".env")
    assert guard.problems(tmp_path / ".env.example") == []


def test_key_like_strings_are_reported_without_the_value(tmp_path: Path) -> None:
    guard = load_guard()
    leaked = tmp_path / "notes.md"
    leaked.write_text(f"token: {fake_key()}\nLLM_API_KEY=" + "x" * 30 + "\nfine line\n")
    found = guard.problems(leaked)
    assert len(found) == 2 and all(fake_key() not in m and "xxxx" not in m for m in found)
    assert guard.main([str(leaked)]) == 1


def test_clean_files_pass(tmp_path: Path) -> None:
    guard = load_guard()
    clean = tmp_path / "ok.py"
    clean.write_text("LLM_API_KEY = settings.llm_api_key\nsk = 3\n")
    assert guard.problems(clean) == [] and guard.main([str(clean)]) == 0
