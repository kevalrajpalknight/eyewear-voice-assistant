"""Guided recorder for the STT accuracy benchmark: the displayed sentence is the reference text.

Everything stays local: WAVs, reference texts and the take log go under the chosen directory
(`bench/data` by default, which is gitignored). A take marked as misread is moved to `excluded/`.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf

from aiglasses.services.voice import VoiceUtterance
from aiglasses.timefmt import stamps


@dataclass(frozen=True)
class Sentence:
    id: str
    kind: str
    text: str


SENTENCES = [
    Sentence("s01_short", "about 2 seconds", "Turn off the kitchen lights."),
    Sentence(
        "s02_long",
        "about 10 seconds",
        "Yesterday I walked down to the market, bought some fresh vegetables and a loaf of "
        "bread, and then sat by the river for a while watching the boats go by.",
    ),
    Sentence(
        "s03_question", "question", "What is the weather going to be like tomorrow afternoon?"
    ),
    Sentence(
        "s04_command", "command", "Set a timer for ten minutes and remind me to take the bread out."
    ),
    Sentence(
        "s05_numbers",
        "numbers and times",
        "Book a table for four people at seven thirty on the twelfth of March.",
    ),
    Sentence(
        "s06_name",
        "a name",
        "Ask Priya Sharma if she is coming to Mumbai with Rahul on Friday.",
    ),
    Sentence("s07_casual", "casual", "Honestly, I have no idea what to make for dinner tonight."),
    Sentence(
        "s08_longer_a",
        "longer",
        "The new library opens at nine in the morning and closes at six, so if you want a "
        "quiet seat you should probably arrive early.",
    ),
    Sentence(
        "s09_longer_b",
        "longer",
        "I tried calling the shop twice yesterday, but nobody answered, so I think we should "
        "just drive over there and see if they are open.",
    ),
    Sentence(
        "s10_question2",
        "question",
        "Can you tell me how far it is from here to the nearest petrol station?",
    ),
    Sentence("s11_command2", "command", "Play some quiet music and turn the volume down a little."),
    Sentence("s12_short_reply", "short reply", "Yes, that sounds good to me, thank you."),
]


async def next_fresh(
    utterances: AsyncIterator[VoiceUtterance],
    shown_at: float,
    on_stray: Callable[[VoiceUtterance], None],
) -> VoiceUtterance:
    """The next utterance that began after the sentence was shown (earlier ones are strays)."""
    while True:
        utterance = await anext(utterances)
        if utterance.started_at >= shown_at:
            return utterance
        on_stray(utterance)


async def record_sentences(
    utterances: AsyncIterator[VoiceUtterance],
    out_dir: Path,
    *,
    sentences: list[Sentence],
    show: Callable[[int, int, Sentence, int], None],
    report: Callable[[dict[str, object]], None],
    ask_misread: Callable[[], Awaitable[bool]],
    on_stray: Callable[[VoiceUtterance], None] = lambda _u: None,
) -> list[dict[str, object]]:
    await asyncio.to_thread((out_dir / "excluded").mkdir, parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for index, sentence in enumerate(sentences, start=1):
        take = 0
        while True:
            take += 1
            shown_at = time.time()
            show(index, len(sentences), sentence, take)
            utterance = await next_fresh(utterances, shown_at, on_stray)
            row: dict[str, object] = {
                **stamps(datetime.fromtimestamp(utterance.started_at, timezone.utc)),
                "sentence": sentence.id, "take": take, "kind": sentence.kind,
                "words": len(sentence.text.split()), "audio_s": round(utterance.seconds, 2),
                "frames": utterance.frame_count, "end_reason": utterance.end_reason.value,
                "wall_s": round(utterance.ended_at - utterance.started_at, 2),
                "start_marker_seen": utterance.start_marker_at is not None,
            }  # fmt: skip
            report(row)
            misread = await ask_misread()
            row["excluded"] = misread
            if misread:
                sf.write(
                    out_dir / "excluded" / f"{sentence.id}_take{take}.wav",
                    utterance.pcm,
                    utterance.sample_rate,
                )
            else:
                sf.write(out_dir / f"{sentence.id}.wav", utterance.pcm, utterance.sample_rate)
                (out_dir / f"{sentence.id}.txt").write_text(sentence.text + "\n", encoding="utf-8")
            with (out_dir / "takes.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
            rows.append(row)
            if not misread:
                break
    return rows
