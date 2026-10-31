"""`aiglasses assistant check`: validate the configuration (secrets: only "set" / "NOT set")."""

import asyncio
import importlib.util
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

from aiglasses.assistant.glossary import glossary_warnings
from aiglasses.assistant.interfaces import Message
from aiglasses.assistant.llm_client import (
    PROVIDER_PACKAGES,
    LangChainLlmClient,
    LlmAuthError,
    LlmError,
    LlmRequestError,
    LlmTimeoutError,
)
from aiglasses.assistant.registry import (
    ENGINE_MODULES,
    STT_ENGINES,
    engine_problems,
    model_dir_problems,
)
from aiglasses.settings import ROLES, AssistantSettings, LlmRole

LIVE_CALL_BUDGET = 3  # lifetime cap on real provider calls made through `check --live`
LIVE_PROMPT = "Reply with the word ok."
LIVE_MAX_TOKENS = 5
HINTS = {
    LlmAuthError: "the provider rejected the key: check LLM_API_KEY and LLM_PROVIDER",
    LlmRequestError: "the request was rejected: check LLM_MODEL (exact id) and LLM_BASE_URL",
    LlmTimeoutError: "no reply in time: check the network, or raise LLM_TIMEOUT_S",
}


def flag(value: object) -> str:
    return "set" if value else "NOT set"


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def audio_device_line() -> str:
    """Ask a child process: sounddevice must never be imported into the BLE process (ADR 0004)."""
    code = "import sounddevice as sd; print(sd.query_devices(kind='output')['name'])"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    return done.stdout.strip() if done.returncode == 0 else "NO output device found"


def role_row(role: LlmRole, configured: bool) -> list[str]:
    if not configured:
        return [role.role, "-", "-", "-", "-", "inherits main"]
    return [
        role.role, role.provider, role.model or "NOT set", flag(role.api_key),
        flag(role.base_url), f"{role.timeout_s:g} s, {role.max_retries} retries",
    ]  # fmt: skip


def live_budget_left(counter: Path) -> int:
    used = int(counter.read_text()) if counter.exists() else 0
    return max(LIVE_CALL_BUDGET - used, 0)


def spend_live_call(counter: Path) -> None:
    counter.parent.mkdir(parents=True, exist_ok=True)
    used = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(used + 1))


async def live_call(role: LlmRole) -> tuple[bool, str]:
    client = LangChainLlmClient(role)
    started = time.perf_counter()
    try:
        reply = "".join(
            [
                d
                async for d in client.stream(
                    [Message("user", LIVE_PROMPT)], max_tokens=LIVE_MAX_TOKENS
                )
            ]
        )
    except LlmError as exc:
        hint = next((h for kind, h in HINTS.items() if isinstance(exc, kind)), "see the message")
        return False, f"{type(exc).__name__}: {exc}\n    hint: {hint}"
    except Exception as exc:  # configuration errors etc.
        return False, f"{type(exc).__name__}: {exc}"
    return (
        True,
        f"reply of {len(reply.strip())} characters in {time.perf_counter() - started:.2f} s",
    )


def run_check(assistant: AssistantSettings, *, live: bool, log_dir: Path, console: Console) -> int:
    """Print the report; return 0 when nothing is wrong, 1 otherwise."""
    problems = list(assistant.problems())
    table = Table(title="LLM roles (secrets show only set / NOT set)")
    for column in ("Role", "Provider", "Model", "Key", "Base URL", "Limits"):
        table.add_column(column)
    router_configured = assistant.router_mode == "model"
    for name, configured in zip(
        ROLES, (True, assistant.fast_configured, router_configured), strict=True
    ):
        table.add_row(*role_row(assistant.llm_role(name), configured))
    console.print(table)
    console.print(
        f"Router mode: {assistant.router_mode} (max easy words {assistant.router_max_easy_words}, "
        f"fallback {assistant.router_fallback}"
        + (f", classifier timeout {assistant.router_timeout_s:g} s)" if router_configured else ")")
    )
    stt_entry = STT_ENGINES.get(assistant.stt_engine)
    stt_model = assistant.stt_model or (stt_entry[1] if stt_entry else "")
    console.print(
        f"STT: {assistant.stt_engine} {stt_model or '(single model)'}; hints "
        f"{'on' if assistant.stt_hints_enabled else 'off'}; glossary "
        f"{'on' if assistant.glossary_enabled else 'off'}"
    )
    console.print(
        f"TTS: {assistant.tts_engine} {assistant.tts_variant}, voice {assistant.tts_voice}, "
        f"speed {assistant.tts_speed:g}, first clause up to "
        f"{assistant.tts_first_clause_max_words} words"
    )
    console.print(
        f"Audio: output {assistant.audio_output}, volume limit {assistant.audio_volume_limit:g}; "
        f"join window {assistant.turn_join_window_s:g} s; logs carry transcripts: "
        f"{'yes' if assistant.log_transcripts else 'no'}"
    )
    problems += engine_problems(assistant)
    problems += model_dir_problems(assistant)
    providers = {assistant.llm_role("main").provider}
    if assistant.fast_configured:
        providers.add(assistant.llm_role("fast").provider)
    wanted = [ENGINE_MODULES.get(assistant.stt_engine), ENGINE_MODULES.get(assistant.tts_engine)]
    wanted += [PROVIDER_PACKAGES.get(p) for p in sorted(providers)]
    problems += [
        f"missing Python package {m}" for m in dict.fromkeys(wanted) if m and not has_module(m)
    ]
    if assistant.glossary_enabled and not assistant.glossary_path.exists():
        problems.append(f"GLOSSARY_PATH {assistant.glossary_path} does not exist")
    if assistant.glossary_path.exists():
        lines = assistant.glossary_path.read_text(encoding="utf-8").splitlines()
        for warning in glossary_warnings(lines):
            console.print(f"[yellow]warning: {warning}[/]")
    console.print(f"Audio output device: {audio_device_line()}")
    for warning in assistant.warnings():
        console.print(f"[yellow]warning: {warning}[/]")
    if live and problems:
        console.print("[yellow]Live check skipped: fix the problems below first.[/]")
    elif live:
        problems += asyncio.run(_live(assistant, log_dir, console))
    for problem in problems:
        console.print(f"[red]problem:[/] {problem}")
    console.print("[green]Configuration looks complete.[/]" if not problems else "")
    return 1 if problems else 0


async def _live(assistant: AssistantSettings, log_dir: Path, console: Console) -> list[str]:
    counter = log_dir / "live_calls.count"
    roles = [assistant.llm_role("main")]
    if assistant.fast_configured:
        roles.append(assistant.llm_role("fast"))
    if assistant.router_mode == "model":
        roles.append(assistant.llm_role("router"))
    failures = []
    for role in roles:
        if live_budget_left(counter) == 0:
            failures.append(f"live-call budget of {LIVE_CALL_BUDGET} is used up")
            break
        spend_live_call(counter)
        ok, message = await live_call(role)
        console.print(f"live {role.role}: {'ok' if ok else 'FAILED'}: {message}")
        if not ok:
            failures.append(f"live call for the {role.role} model failed")
    return failures
