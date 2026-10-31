"""Benchmark one local model on this PC: speed, memory, prompt cache and decision quality.

    uv run python -m bench.local_llm_bench MODEL [--quality] [--vision] [--audio]

Run from the repository root with PYTHONPATH=bench/src, in the ROOT environment (it uses the real
prompt builder and the real llama-server manager, so the numbers describe the shipped system).
One model per process so memory and cleanup are clean; the server runs in a kill-on-close job.
Results go to bench/out/local_llm_<model>.json (gitignored). No network except 127.0.0.1.
"""

import asyncio
import base64
import io
import json
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psutil
import yaml
from aiglasses.assistant.app import load_persona
from aiglasses.assistant.conversation import system_prompt
from aiglasses.assistant.escalation import EscalationRules
from aiglasses.assistant.intents import Intents
from aiglasses.assistant.interfaces import Message
from aiglasses.assistant.lanes import LocalFirstClient
from aiglasses.assistant.local_llm import Step, step_schema
from aiglasses.assistant.local_server import LocalServer
from aiglasses.assistant.memory import Memory
from aiglasses.assistant.models import Registry
from aiglasses.assistant.search import FakeSearch
from aiglasses.assistant.tools.device import DeviceHolder
from aiglasses.assistant.tools.factory import build_registry
from aiglasses.assistant.vision import FakeImageSource, FakeVision
from aiglasses.settings import AssistantSettings

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
REPEATS = 3
ALL_TOOLS = "read_battery,read_media_counts,take_photo,forget_all_that,search_web,describe_scene"
HISTORY = [
    ("What is the capital of Italy?", "It is Rome."),
    ("Tell me a fun fact about owls.", "Owls can turn their heads almost all the way round."),
    ("How many minutes are in an hour?", "Sixty minutes."),
    ("Which is bigger, a whale or an elephant?", "A blue whale is the biggest animal ever."),
    ("What colour is the sky at noon?", "Usually blue."),
    ("Say hello to my friend.", "Hello, friend! Nice to meet you."),
    ("What is five times six?", "Thirty."),
    ("Name a fruit that is yellow.", "A banana is yellow."),
    ("How many days are in February?", "Twenty eight, or twenty nine in a leap year."),
    ("What do bees make?", "Bees make honey."),
]


def settings_for(model: str) -> AssistantSettings:
    return AssistantSettings(
        _env_file=None,
        llm_model="bench",
        llm_api_key="unused",
        router_mode="local_first",
        local_llm_model=model,
        tools_enabled=ALL_TOOLS,
        local_llm_port=8091,
    )  # type: ignore[call-arg]


def real_prompt(assistant: AssistantSettings, history: int = 10) -> tuple[list[Message], list[str]]:
    """System prompt + local rules + tool schemas + history, exactly as the lane builds them."""
    memory = Memory(10, 600, 100_000)
    registry = build_registry(
        assistant, device=DeviceHolder(), memory=memory, intents=Intents.load(),
        search=FakeSearch(), image_source=FakeImageSource(b""), vision=FakeVision(),
    )  # fmt: skip
    client = LocalFirstClient(
        assistant, local=None, cloud=None, registry=registry,  # type: ignore[arg-type]
        intents=Intents.load(), rules=EscalationRules.load(), memory=memory,
    )  # fmt: skip
    now = datetime(2026, 11, 1, 10, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    prefix = system_prompt(load_persona(assistant), None, now, assistant.reply_target_sentences)
    prefix = prefix.partition("\n\nCurrent date and time:")[0]
    system = client._local_system(prefix, assistant.enabled_tools)
    tools = [t for t in assistant.enabled_tools]
    turns: list[Message] = []
    for user, reply in HISTORY[:history]:
        turns += [Message("user", user), Message("assistant", reply)]
    return [Message("system", system), *turns], tools


def body(
    messages: list[Message],
    tools: list[str],
    *,
    cache: bool,
    schema: bool = True,
    max_tokens: int = 100,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "temperature": 0.0, "max_tokens": max_tokens, "stream": True, "cache_prompt": cache,
    }  # fmt: skip
    if schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "step", "strict": True, "schema": step_schema(tools)},
        }
    return payload


def timed_stream(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One streamed request: first-token time, total time, the text and the server's own timings."""
    started = time.perf_counter()
    first: float | None = None
    text, timings = "", {}
    with httpx.stream("POST", url, json=payload, timeout=300.0) as reply:
        reply.raise_for_status()
        for line in reply.iter_lines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            chunk = json.loads(line[5:])
            if "timings" in chunk:
                timings = chunk["timings"]
            choices = chunk.get("choices") or []
            piece = choices[0].get("delta", {}).get("content") if choices else None
            if piece:
                first = first if first is not None else time.perf_counter() - started
                text += piece
    return {
        "first_s": first,
        "total_s": time.perf_counter() - started,
        "text": text,
        "timings": timings,
    }


def median(values: list[float]) -> float:
    return round(statistics.median(values), 3)


class RamSampler(threading.Thread):
    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self._process = psutil.Process(pid)
        self._stop_flag = threading.Event()

    def run(self) -> None:
        while not self._stop_flag.is_set():
            try:
                self.peak = max(self.peak, self._process.memory_info().rss)
            except psutil.Error:
                return
            time.sleep(0.25)

    def stop(self) -> int:
        self._stop_flag.set()
        return round(self.peak / 2**20)


def speed_suite(url: str, assistant: AssistantSettings) -> dict[str, Any]:
    messages, tools = real_prompt(assistant)
    ask = lambda text: [*messages, Message("user", text)]  # noqa: E731
    prompt_chars = sum(len(m.content) for m in messages)
    result: dict[str, Any] = {"prompt_chars": prompt_chars}
    cold, cached, nocache, shared = [], [], [], []
    speeds, gen_speeds = [], []
    for run in range(REPEATS):
        question = f"What is {run + 3} plus {run + 4}?"
        r = timed_stream(url, body(ask(question + " (cold)"), tools, cache=False))
        cold.append(r["first_s"] or r["total_s"])
        t = r["timings"]
        if t.get("prompt_ms"):
            speeds.append(t["prompt_n"] / t["prompt_ms"] * 1000)
        if t.get("predicted_per_second"):
            gen_speeds.append(t["predicted_per_second"])
        timed_stream(url, body(ask(question), tools, cache=True))  # primes the cache
        r2 = timed_stream(url, body(ask(question), tools, cache=True))
        cached.append(r2["first_s"] or r2["total_s"])
        r3 = timed_stream(url, body(ask(question), tools, cache=False))
        nocache.append(r3["first_s"] or r3["total_s"])
        r4 = timed_stream(url, body(ask(f"What is {run + 9} plus {run + 2}?"), tools, cache=True))
        shared.append(r4["first_s"] or r4["total_s"])
    result.update(
        first_token_cold_s=median(cold), first_token_same_prompt_cached_s=median(cached),
        first_token_same_prompt_no_cache_s=median(nocache),
        first_token_new_question_shared_prefix_s=median(shared),
        prompt_tokens_per_s=round(statistics.median(speeds), 1) if speeds else None,
        generation_tokens_per_s=round(statistics.median(gen_speeds), 1) if gen_speeds else None,
    )  # fmt: skip
    totals = []
    for _ in range(REPEATS):
        free = [*messages, Message("user", "In two short sentences, why is the sky blue?")]
        r = timed_stream(url, body(free, tools, cache=True, schema=False, max_tokens=70))
        totals.append(r["total_s"])
    result["two_sentence_reply_total_s"] = median(totals)
    return result


def judge(item: dict[str, Any], step: Step | None) -> dict[str, Any]:
    expected = item["action"]
    if step is None:
        return {"json_valid": False, "action_ok": False, "tool_ok": None, "fact_ok": None}
    searched = step.action == "tool" and step.tool == "search_web"
    ok = {
        "answer": step.action == "answer",
        "tool": step.action == "tool",
        "escalate": step.action == "escalate",
        "escalate_or_search": step.action == "escalate" or searched,
        "answer_or_escalate": step.action in ("answer", "escalate"),
    }[expected]
    tool_ok = (step.tool == item.get("tool")) if expected == "tool" else None
    fact_ok = None
    if item.get("says"):
        fact_ok = step.action == "answer" and any(
            w.lower() in step.text.lower() for w in item["says"]
        )
    return {"json_valid": True, "action_ok": ok, "tool_ok": tool_ok, "fact_ok": fact_ok}


def quality_suite(url: str, assistant: AssistantSettings) -> dict[str, Any]:
    items = yaml.safe_load((Path(__file__).with_name("quality_prompts.yaml")).read_text("utf-8"))[
        "prompts"
    ]
    messages, tools = real_prompt(assistant)
    rows = []
    for item in items:
        convo = messages if item.get("history") else [messages[0]]
        payload = body([*convo, Message("user", item["text"])], tools, cache=True, max_tokens=100)
        r = timed_stream(url, payload)
        try:
            step: Step | None = Step.model_validate(json.loads(r["text"]))
        except ValueError:
            step = None
        rows.append(
            {
                "id": item["id"], "group": item["group"], "expected": item["action"],
                "got": None if step is None else f"{step.action}:{step.tool or ''}",
                "text": None if step is None else step.text[:120], "total_s": round(r["total_s"], 2),
                **judge(item, step),
            }
        )  # fmt: skip

    def rate(key: str, only: str | None = None) -> float | None:
        picked = [
            r[key] for r in rows if r[key] is not None and (only is None or r["group"] == only)
        ]
        return round(sum(picked) / len(picked), 3) if picked else None

    return {
        "n": len(rows), "json_valid": rate("json_valid"), "action_correct": rate("action_ok"),
        "tool_correct": rate("tool_ok"), "facts_correct": rate("fact_ok"),
        "by_group": {g: rate("action_ok", g) for g in sorted({r["group"] for r in rows})},
        "median_total_s": median([r["total_s"] for r in rows]), "rows": rows,
    }  # fmt: skip


def synthetic_image() -> bytes:
    from PIL import Image, ImageDraw

    picture = Image.new("RGB", (320, 240), "white")
    ImageDraw.Draw(picture).rectangle((90, 60, 230, 180), fill=(220, 20, 20))
    out = io.BytesIO()
    picture.save(out, format="JPEG")
    return out.getvalue()


def vision_suite(url: str) -> dict[str, Any]:
    uri = "data:image/jpeg;base64," + base64.b64encode(synthetic_image()).decode()
    content = [
        {"type": "text", "text": "What colour is the square? Answer in one short sentence."},
        {"type": "image_url", "image_url": {"url": uri}},
    ]
    times, answers = [], []
    for _ in range(REPEATS):
        started = time.perf_counter()
        reply = httpx.post(
            url,
            json={"messages": [{"role": "user", "content": content}], "max_tokens": 40},
            timeout=300.0,
        )
        times.append(time.perf_counter() - started)
        answers.append(reply.json()["choices"][0]["message"]["content"].strip())
    return {
        "median_total_s": median(times),
        "answer": answers[0],
        "says_red": "red" in answers[0].lower(),
    }


def main() -> None:
    model = sys.argv[1]
    flags = set(sys.argv[2:])
    assistant = settings_for(model)
    registry = Registry.load()
    OUT.mkdir(exist_ok=True)
    state = ROOT.parent / "logs" / f"bench-llama-{model}.json"
    server = LocalServer(assistant, registry, state)
    result: dict[str, Any] = {
        "model": model,
        "ctx": assistant.local_llm_ctx,
        "when": datetime.now().isoformat(),
    }
    started = time.perf_counter()
    try:
        server.start(auto_port=True, vision="--vision" in flags)
        result["load_s"] = round(time.perf_counter() - started, 2)
        sampler = RamSampler(server._process.pid)  # type: ignore[union-attr]
        sampler.start()
        url = server.base_url + "/v1/chat/completions"
        timed_stream(
            url, body([Message("user", "hi")], [], cache=True, schema=False, max_tokens=2)
        )  # warm
        if "--vision" in flags:
            result["vision"] = vision_suite(url)
        else:
            result["speed"] = speed_suite(url, assistant)
            if "--quality" in flags:
                result["quality"] = quality_suite(url, assistant)
        result["peak_ram_mb"] = sampler.stop()
    finally:
        server.stop()
    suffix = "_vision" if "--vision" in flags else ""
    (OUT / f"local_llm_{model}{suffix}.json").write_text(
        json.dumps(result, indent=1), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in result.items() if k != "quality"}, indent=1))
    if "quality" in result:
        q = result["quality"]
        print(
            {
                k: q[k]
                for k in (
                    "n",
                    "json_valid",
                    "action_correct",
                    "tool_correct",
                    "facts_correct",
                    "median_total_s",
                )
            }
        )


if __name__ == "__main__":
    asyncio.get_event_loop_policy()
    main()
