import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np

from aiglasses.assistant.recorder import SENTENCES, Sentence, record_sentences
from aiglasses.services.voice import EndReason, VoiceUtterance

TWO = [Sentence("a", "short", "Hello there."), Sentence("b", "long", "Good morning to you.")]


def utt(started: float, reason: EndReason = EndReason.END_MARKER) -> VoiceUtterance:
    return VoiceUtterance(
        np.ones(16000, dtype=np.int16), 16000, started, started + 1.0, 50, reason, started, None
    )


async def feed(*items: VoiceUtterance) -> AsyncIterator[VoiceUtterance]:
    for item in items:
        yield item
        await asyncio.sleep(0)
    await asyncio.sleep(10)


def test_the_sentence_list_covers_the_requested_kinds() -> None:
    kinds = " ".join(s.kind for s in SENTENCES)
    assert len(SENTENCES) == 12
    for wanted in ("about 2", "about 10", "question", "command", "numbers", "name", "casual"):
        assert wanted in kinds
    assert sum(1 for s in SENTENCES if s.kind == "longer") == 2
    assert len({s.id for s in SENTENCES}) == 12


async def test_saves_wav_and_reference_and_logs_every_take(tmp_path: Path) -> None:
    now = 4_000_000_000.0  # future: every utterance counts as fresh
    answers = iter([False, False])

    async def ask() -> bool:
        return next(answers)

    shown: list[tuple[str, int]] = []
    rows = await asyncio.wait_for(
        record_sentences(
            feed(utt(now), utt(now + 5, EndReason.TIMEOUT)),
            tmp_path,
            sentences=TWO,
            show=lambda i, n, s, t: shown.append((s.id, t)),
            report=lambda r: None,
            ask_misread=ask,
        ),
        5,
    )
    assert (tmp_path / "a.txt").read_text().strip() == "Hello there."
    assert (tmp_path / "b.wav").exists()
    log = [json.loads(x) for x in (tmp_path / "takes.jsonl").read_text().splitlines()]
    assert [r["end_reason"] for r in log] == ["end_marker", "timeout"]
    assert all(r["excluded"] is False and "ts" in r and "local" in r for r in log)
    assert shown == [("a", 1), ("b", 1)] and len(rows) == 2


async def test_a_misread_take_is_excluded_and_the_sentence_is_asked_again(tmp_path: Path) -> None:
    now = 4_000_000_000.0
    answers = iter([True, False])

    async def ask() -> bool:
        return next(answers)

    shown: list[tuple[str, int]] = []
    rows = await asyncio.wait_for(
        record_sentences(
            feed(utt(now), utt(now + 5)),
            tmp_path,
            sentences=TWO[:1],
            show=lambda i, n, s, t: shown.append((s.id, t)),
            report=lambda r: None,
            ask_misread=ask,
        ),
        5,
    )
    assert shown == [("a", 1), ("a", 2)]
    assert [r["excluded"] for r in rows] == [True, False]
    assert (tmp_path / "excluded" / "a_take1.wav").exists()
    assert (tmp_path / "a.wav").exists()


async def test_a_click_made_before_the_sentence_was_shown_is_ignored(tmp_path: Path) -> None:
    strays: list[VoiceUtterance] = []

    async def ask() -> bool:
        return False

    rows = await asyncio.wait_for(
        record_sentences(
            feed(utt(1.0), utt(4_000_000_000.0)),  # the first started in 1970
            tmp_path,
            sentences=TWO[:1],
            show=lambda i, n, s, t: None,
            report=lambda r: None,
            ask_misread=ask,
            on_stray=strays.append,
        ),
        5,
    )
    assert len(strays) == 1 and len(rows) == 1
