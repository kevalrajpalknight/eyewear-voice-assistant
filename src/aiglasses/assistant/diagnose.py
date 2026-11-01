"""`aiglasses assistant diagnose`: where does a phrase go, and where does it stop? Offline.

Uses the real intents, escalation rules, tool registry and settings with FAKE devices, search and
vision, so nothing touches the glasses or the network. It reports the decision path only.
"""

from dataclasses import dataclass, field

from aiglasses.assistant.escalation import EscalationRules
from aiglasses.assistant.intents import Intents, normalise
from aiglasses.assistant.memory import Memory
from aiglasses.assistant.router import DEFAULT_HANDLERS, LocalState
from aiglasses.assistant.search import FakeSearch, SearchHit
from aiglasses.assistant.tools.base import ToolContext
from aiglasses.assistant.tools.factory import build_registry
from aiglasses.assistant.vision import FakeImageSource, FakeVision
from aiglasses.domain.models import BatteryStatus, MediaCounts
from aiglasses.services.control import PhotoResult
from aiglasses.settings import AssistantSettings

SAMPLES = (
    "open the camera",
    "take a photo",
    "what is this",
    "identify this object",
    "search for the latest news about electric cars",
    "what's the weather in Mumbai",
    "how much battery is left",
    "tell me a joke",
    "forget that",
)


class _FakeDevice:
    async def read_battery(self) -> BatteryStatus:
        return BatteryStatus(percent=50)

    async def read_media_counts(self) -> MediaCounts:
        return MediaCounts(1, 1, 1)

    async def take_photo(self, *, confirm: bool) -> PhotoResult:
        return PhotoResult(MediaCounts(1, 1, 1), MediaCounts(2, 1, 1), "ok", "00")


@dataclass
class Diagnosis:
    phrase: str
    handler: str = ""
    intent: str = ""
    tool: str = ""
    args: dict[str, object] = field(default_factory=dict)
    enabled: bool | None = None
    registered: bool | None = None
    lane: str = ""
    checks: list[str] = field(default_factory=list)
    tainted: bool = False
    stopped_at: str = ""
    spoken: str = ""


async def diagnose(
    settings: AssistantSettings, phrases: tuple[str, ...] = SAMPLES
) -> list[Diagnosis]:
    intents, rules = Intents.load(settings.intents_path), EscalationRules.load()
    memory = Memory(settings.memory_max_turns, settings.memory_ttl_s, settings.memory_max_tokens)
    traced = settings.model_copy(
        update={"photo_min_interval_s": 0.0}
    )  # no rate limit between phrases
    registry = build_registry(
        traced, device=_FakeDevice(), memory=memory, intents=intents,
        search=FakeSearch([SearchHit("A result", "A snippet")]),
        image_source=FakeImageSource(b"fake image"), vision=FakeVision(),
    )  # fmt: skip
    state = LocalState(timezone=settings.assistant_timezone)
    results = []
    for phrase in phrases:
        text = normalise(phrase)
        found = Diagnosis(phrase)
        for handler in DEFAULT_HANDLERS:
            if handler(text, state) is not None:
                found.handler = handler.__name__
                found.lane, found.stopped_at = "rules", "answered by a local handler"
                break
        intent = None if found.handler else intents.find(phrase)
        if intent is not None:
            found.intent, found.tool = intent.name, intent.tool
            found.args = intent.args_for(text)
            found.enabled = intent.tool in settings.enabled_tools
            found.registered = registry.risk(intent.tool) is not None
            found.lane = "rules" if found.enabled else "local/cloud (tool is not enabled)"
            ctx = ToolContext(utterance=phrase)
            outcome = await registry.call(
                intent.tool, found.args, ctx, enabled=settings.enabled_tools,
                max_calls=settings.tool_max_calls_per_turn, timeout_s=settings.tool_timeout_s,
            )  # fmt: skip
            found.tainted = ctx.tainted
            found.spoken = outcome.spoken or outcome.summary
            found.stopped_at = (
                "ok: the tool ran" if outcome.ok else f"{outcome.category}: {outcome.summary}"
            )
        elif not found.handler:
            if rules.research(phrase):
                found.checks.append("research request -> cloud")
            found.lane = (
                "cloud" if any("cloud" in c for c in found.checks) else "local model decides"
            )
            found.stopped_at = "no rule matched; the model chooses"
        results.append(found)
    return results
