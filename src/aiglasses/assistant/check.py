"""`aiglasses assistant check`: validate the configuration (secrets: only "set" / "NOT set")."""

import asyncio
import importlib.util
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
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

LIVE_PROMPT = "Reply with the word ok."
LIVE_MAX_TOKENS = 5
LIVE_SEARCH_QUERY = "what is the capital of France"
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


def live_budget_left(counter: Path, budget: int) -> int:
    used = int(counter.read_text()) if counter.exists() else 0
    return max(budget - used, 0)


def reset_live_budget(counter: Path) -> None:
    """Start counting again from zero. Only the user asks for this (`--reset-live-budget`)."""
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text("0")


def spend_live_call(counter: Path) -> None:
    counter.parent.mkdir(parents=True, exist_ok=True)
    used = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(used + 1))


async def live_call(role: LlmRole) -> tuple[bool, str]:
    client = LangChainLlmClient(role)
    started = time.perf_counter()
    first: float | None = None
    reply = ""
    try:
        async for delta in client.stream(
            [Message("user", LIVE_PROMPT)], max_tokens=LIVE_MAX_TOKENS
        ):
            if first is None:
                first = time.perf_counter() - started
            reply += delta
    except LlmError as exc:
        hint = next((h for kind, h in HINTS.items() if isinstance(exc, kind)), "see the message")
        return False, f"{type(exc).__name__}: {exc}\n    hint: {hint}"
    except Exception as exc:  # configuration errors etc.
        return False, f"{type(exc).__name__}: {exc}"
    first_text = "no text" if first is None else f"{first:.2f} s"
    return True, (
        f"reply of {len(reply.strip())} characters; first token {first_text}, "
        f"total {time.perf_counter() - started:.2f} s"
    )


def local_report(assistant: AssistantSettings, console: Console) -> list[str]:
    """Print the local-model, tool, search and vision settings; return problems."""
    from aiglasses.assistant import local_server
    from aiglasses.assistant.models import Registry, model_path
    from aiglasses.assistant.search import SEARCH_PROVIDERS
    from aiglasses.assistant.tools.factory import KNOWN_TOOLS

    problems: list[str] = []
    console.print(
        f"Tools enabled: {', '.join(assistant.enabled_tools) or 'none'} "
        f"(at most {assistant.tool_max_calls_per_turn} calls per turn, "
        f"{assistant.tool_timeout_s:g} s each)"
    )
    unknown = [t for t in assistant.enabled_tools if t not in KNOWN_TOOLS]
    if unknown:
        problems.append(f"TOOLS_ENABLED names unknown tools: {', '.join(unknown)}")
    if assistant.search_provider not in SEARCH_PROVIDERS:
        valid = ", ".join(sorted(SEARCH_PROVIDERS))
        problems.append(f"SEARCH_PROVIDER {assistant.search_provider!r} is unknown; valid: {valid}")
    console.print(
        f"Search: {assistant.search_provider}, {assistant.search_max_results} results, region "
        f"{assistant.search_region}, cache {assistant.search_cache_ttl_s:g} s; vision: "
        f"{assistant.vision_provider}"
    )
    console.print(
        f"Memory: {assistant.memory_max_turns} turns or {assistant.memory_ttl_s:g} s, "
        f"{assistant.memory_max_tokens} tokens; replies: about {assistant.reply_target_sentences}, "
        f"{assistant.memory_max_tokens} tokens; replies: about {assistant.reply_target_sentences}, "
        f"steps in {assistant.agent_time_budget_s:g} s"
    )
    if assistant.router_mode != "local_first":
        return problems
    name = assistant.local_llm_model
    try:
        registry = Registry.load(
            assistant.model_registry_path if assistant.model_registry_path.exists() else None
        )
    except Exception as exc:
        return [*problems, f"model registry: {type(exc).__name__}"]
    threads = assistant.local_llm_threads or f"{local_server.physical_cores()} (physical cores)"
    console.print(
        f"Local model: {name or 'NOT set'}; port {assistant.local_llm_port}; context "
        f"{assistant.local_llm_ctx}; batch {assistant.local_llm_batch}; threads "
        f"{threads}; "
        f"prompt cache {'on' if assistant.local_llm_prompt_cache else 'off'}; first token limit "
        f"{assistant.local_first_token_timeout_s:g} s"
    )
    exe = assistant.local_llm_bin_dir / local_server.EXECUTABLE
    console.print(f"llama.cpp: {'installed' if exe.exists() else 'NOT installed'} ({exe})")
    if name is None:
        console.print(
            "[yellow]warning: LOCAL_LLM_MODEL is not set; every request goes to the cloud[/]"
        )
    elif name not in registry.models:
        problems.append(
            f"LOCAL_LLM_MODEL {name!r} is not in the registry; valid: "
            + ", ".join(sorted(registry.models))
        )
    else:
        spec = registry.models[name]
        missing = [
            f.filename for f in spec.files if not model_path(assistant.models_dir, spec, f).exists()
        ]
        if missing:
            console.print(
                f"[yellow]warning: model files missing ({', '.join(missing)}); "
                f"run `aiglasses models pull {name}`[/]"
            )
        if not exe.exists():
            console.print("[yellow]warning: run `aiglasses models pull llama.cpp`[/]")
    return problems


def run_check(
    assistant: AssistantSettings,
    *,
    live: bool,
    log_dir: Path,
    console: Console,
    live_tools: bool = False,
) -> int:
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
    console.print(
        "Glossary format: one term per line; spoken forms after a single '|', separated by "
        "commas (RapDuo SM | rap duo s m, rap do)."
    )
    if assistant.glossary_path.exists():
        lines = assistant.glossary_path.read_text(encoding="utf-8").splitlines()
        for warning in glossary_warnings(lines):
            console.print(f"[yellow]warning: {warning}[/]")
    problems += local_report(assistant, console)
    console.print(f"Audio output device: {audio_device_line()}")
    for warning in assistant.warnings():
        console.print(f"[yellow]warning: {warning}[/]")
    if live and problems:
        console.print("[yellow]Live check skipped: fix the problems below first.[/]")
    elif live or live_tools:
        problems += asyncio.run(_live(assistant, log_dir, console, roles=live, tools=live_tools))
    for problem in problems:
        console.print(f"[red]problem:[/] {problem}")
    console.print("[green]Configuration looks complete.[/]" if not problems else "")
    return 1 if problems else 0


async def live_search(assistant: AssistantSettings) -> tuple[bool, str]:
    """One neutral search through the configured provider."""
    from aiglasses.assistant.search import SearchError, build_search

    started = time.perf_counter()
    try:
        hits = await build_search(assistant).search(
            LIVE_SEARCH_QUERY, max_results=2, region=assistant.search_region,
            timeout_s=assistant.search_timeout_s,
        )  # fmt: skip
    except SearchError as exc:
        return False, f"search failed: {exc.category}"
    return True, f"{len(hits)} results in {time.perf_counter() - started:.2f} s"


async def _live(
    assistant: AssistantSettings, log_dir: Path, console: Console, *, roles: bool, tools: bool
) -> list[str]:
    counter = log_dir / "live_calls.count"
    jobs: list[tuple[str, Callable[[], Awaitable[tuple[bool, str]]]]] = []
    if roles:
        chosen = [assistant.llm_role("main")]
        if assistant.fast_configured:
            chosen.append(assistant.llm_role("fast"))
        if assistant.router_mode == "model":
            chosen.append(assistant.llm_role("router"))
        jobs += [(r.role, lambda r=r: live_call(r)) for r in chosen]  # type: ignore[misc]
    if tools:
        jobs.append(("search", lambda: live_search(assistant)))
    failures = []
    for label, job in jobs:
        if live_budget_left(counter, assistant.live_call_budget) == 0:
            failures.append(f"live-call budget of {assistant.live_call_budget} is used up")
            break
        spend_live_call(counter)
        ok, message = await job()
        console.print(f"live {label}: {'ok' if ok else 'FAILED'}: {message}")
        if not ok:
            failures.append(f"live call for {label} failed")
    return failures
