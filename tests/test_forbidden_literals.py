"""Hardcoding guard: model names, voice ids, engine names and speeds belong in settings only."""

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "aiglasses"
# Where each kind of name may legitimately appear as a string literal.
SETTINGS = {"settings.py"}
REGISTRIES = {"settings.py", "registry.py", "engines.py"}

MODEL_NAMES = re.compile(r"\b(gpt-?\d|claude-|gemini-?\d|llama-?\d|mistral-|o\d-mini|sonar)", re.I)
VOICE_IDS = re.compile(r"\b[ab][fm]_[a-z]+\b")
ENGINE_NAMES = re.compile(r"moonshine|parakeet|kokoro|piper|faster.whisper", re.I)
PROVIDER_NAMES = re.compile(r"ddgs|tavily|brave", re.I)
LOCAL_MODEL_NAMES = re.compile(r"qwen|gemma|llama-?3|phi-?\d|unsloth|bartowski", re.I)
SEARCH_REGISTRY = {"settings.py", "search.py"}
MODEL_FILES = {"settings.py", "models.py"}


def string_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def offenders(pattern: re.Pattern[str], allowed: set[str]) -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name in allowed:
            continue
        for line, text in string_literals(path):
            if pattern.search(text):
                found.append(f"{path.relative_to(SRC)}:{line}: {text[:50]!r}")
    return found


def test_no_llm_model_names_outside_settings() -> None:
    assert offenders(MODEL_NAMES, SETTINGS) == []


def test_no_voice_ids_outside_settings() -> None:
    assert offenders(VOICE_IDS, SETTINGS) == []


def test_engine_names_appear_only_in_settings_and_the_engine_registry() -> None:
    assert offenders(ENGINE_NAMES, REGISTRIES) == []


def test_the_guard_itself_detects_a_hardcoded_literal(tmp_path: Path) -> None:
    sample = tmp_path / "x.py"
    sample.write_text('"""doc"""\nVOICE = "bf_isabella"\nMODEL = "gpt-5"\n')
    texts = [t for _, t in string_literals(sample)]
    assert texts == ["bf_isabella", "gpt-5"]
    assert VOICE_IDS.search(texts[0]) and MODEL_NAMES.search(texts[1])


def test_search_provider_names_appear_only_in_settings_and_the_search_registry() -> None:
    assert offenders(PROVIDER_NAMES, SEARCH_REGISTRY) == []


def test_local_model_names_are_data_not_code() -> None:
    assert offenders(LOCAL_MODEL_NAMES, MODEL_FILES) == []
