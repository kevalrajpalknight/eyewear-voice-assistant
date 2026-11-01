"""Wire the assistant together and keep it running: join cut-off requests, barge in, reconnect."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

from aiglasses.assistant.conversation.pipeline import ConversationPipeline
from aiglasses.assistant.conversation.turns import TurnRunner
from aiglasses.assistant.listening.joiner import TurnJoiner
from aiglasses.domain.errors import ConfigError, GlassesError
from aiglasses.services.voice import VoiceUtterance
from aiglasses.settings import AssistantSettings

PACKAGED_PERSONA = Path(__file__).with_name("persona.md")
RECONNECT_DELAYS_S = (2.0, 5.0, 10.0, 20.0, 30.0)  # the glasses need up to ~30 s to advertise
Opener = Callable[[Callable[[], None]], AbstractAsyncContextManager[AsyncIterator[VoiceUtterance]]]


def load_persona(settings: AssistantSettings) -> str:
    path = settings.persona_path or PACKAGED_PERSONA
    if not path.exists():
        raise ConfigError(f"PERSONA_PATH {path} does not exist")
    return path.read_text(encoding="utf-8")


@dataclass
class SessionEnd:
    reason: str  # "disconnected" | "turn_limit"


class AssistantApp:
    def __init__(self, pipeline: ConversationPipeline, settings: AssistantSettings) -> None:
        self.pipeline = pipeline
        self.runner = TurnRunner(pipeline)
        self.joiner = TurnJoiner(settings.turn_join_window_s, settings.turn_end_silence_ms)
        pipeline.history.on_forget(self.joiner.reset)
        pipeline.on_text = self.joiner.note_text
        self.max_turns = settings.max_turns_per_session
        self.turns = 0

    async def handle(self, utterance: VoiceUtterance) -> None:
        joined = self.joiner.feed(utterance)
        if joined.replaces_previous:
            previous = self.runner.turn
            await self.runner.cancel()  # settles the old turn so its history entry is final
            self.pipeline.history.drop_turn(previous)
        await self.runner.submit(joined.utterance)
        self.turns += 1

    async def consume(self, utterances: AsyncIterator[VoiceUtterance]) -> SessionEnd:
        async for utterance in utterances:
            await self.handle(utterance)
            if self.turns >= self.max_turns:
                await self.runner.wait()
                return SessionEnd("turn_limit")
        return SessionEnd("disconnected")

    async def close(self) -> None:
        await self.runner.close()


async def run_forever(
    app: AssistantApp,
    opener: Opener,
    *,
    report: Callable[[str], None],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    delays: tuple[float, ...] = RECONNECT_DELAYS_S,
) -> SessionEnd:
    """Connect, serve, and reconnect with backoff until the turn limit or Ctrl+C."""
    failures = 0
    while True:
        try:
            async with opener(app.runner.interrupt) as utterances:
                report("connected")
                served = app.turns
                end = await app.consume(utterances)
                if app.turns > served:
                    failures = 0  # only a session that did work resets the backoff
            if end.reason == "turn_limit":
                return end
            report("the glasses disconnected")
        except asyncio.CancelledError:
            raise
        except (GlassesError, OSError, asyncio.TimeoutError) as exc:
            report(f"connection problem: {type(exc).__name__}")
        delay = delays[min(failures, len(delays) - 1)]
        failures += 1
        report(f"reconnecting in {delay:.0f} s")
        await sleep(delay)
