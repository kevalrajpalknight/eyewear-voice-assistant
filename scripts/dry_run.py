"""Offline dry run: a WAV file through the whole pipeline with the real local STT and TTS, a fake
LLM and a silent sink. No network, no glasses, no sound.

    uv run python scripts/dry_run.py bench/data/s03_question.wav
"""

import asyncio
import sys
from pathlib import Path

from aiglasses.assistant.app import AssistantApp
from aiglasses.assistant.fakes import FakeLlm
from aiglasses.assistant.registry import build_stt, build_tts
from aiglasses.assistant.router import RoutedLlmClient
from aiglasses.assistant.runtime import make_pipeline
from aiglasses.assistant.session import tts_options, utterance_from_wav, warm_up
from aiglasses.assistant.speaker import NullSink
from aiglasses.config import Settings
from aiglasses.settings import AssistantSettings


async def main(wav: Path) -> None:
    assistant = AssistantSettings()
    stt = await asyncio.to_thread(build_stt, assistant)
    tts = await asyncio.to_thread(build_tts, assistant)
    await warm_up(stt, tts, tts_options(assistant))
    reply = ("**Sure.** I can help with that, ", "and it is 4:30 now. ", "Anything else?")
    llm = RoutedLlmClient(assistant, FakeLlm([reply]))
    sink = NullSink()
    pipeline = make_pipeline(
        Settings(), assistant, stt=stt, tts=tts, sink=sink, llm=llm, show_text=True
    )
    app = AssistantApp(pipeline, assistant)
    await app.handle(utterance_from_wav(wav))
    await app.runner.wait()
    await app.close()
    print(f"silent sink received {sink.seconds_received:.1f} s of speech")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
