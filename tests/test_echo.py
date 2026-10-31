import asyncio
import json
import time
from pathlib import Path

import numpy as np

from aiglasses.assistant.chunker import split_clauses
from aiglasses.assistant.echo import Debug, EchoPipeline, TurnLog, TurnRecord, TurnRunner
from aiglasses.assistant.fakes import FakeSink, FakeStt, FakeTts
from aiglasses.assistant.interfaces import TtsOptions
from aiglasses.services.voice import EndReason, VoiceUtterance

TEXT = "Turn off the kitchen lights, and then please set a timer for ten minutes."


def utterance(seconds: float = 2.0) -> VoiceUtterance:
    now = time.time()
    return VoiceUtterance(
        pcm=np.zeros(int(16000 * seconds), dtype=np.int16),
        sample_rate=16000,
        started_at=now - seconds,
        ended_at=now,
        frame_count=int(seconds * 50),
        end_reason=EndReason.END_MARKER,
        start_marker_at=now - seconds - 0.1,
        end_marker_at=now,
    )


def pipeline(
    tmp_path: Path,
    stt: FakeStt | None = None,
    tts: FakeTts | None = None,
    sink: FakeSink | None = None,
    debug: Debug | None = None,
    records: list[TurnRecord] | None = None,
) -> EchoPipeline:
    return EchoPipeline(
        stt or FakeStt(TEXT),
        tts or FakeTts(),
        sink or FakeSink(),
        TtsOptions(voice="v", speed=1.0),
        turn_log=TurnLog(tmp_path / "turns.jsonl"),
        first_clause_max_words=6,
        debug=debug,
        on_record=records.append if records is not None else None,
    )


async def test_speaks_back_the_clauses_in_order_and_logs_latency_stages(tmp_path: Path) -> None:
    tts, sink = FakeTts(), FakeSink()
    record = await pipeline(tmp_path, tts=tts, sink=sink).run_turn(utterance(), 1)
    assert record.outcome == "spoken" and tts.spoken == split_clauses(TEXT, 6)
    assert len(sink.played) == len(tts.spoken)
    assert record.t_stt and record.t_first_chunk and record.t_playing and record.t_done
    assert record.t_end <= record.t_received <= record.t_stt <= record.t_first_chunk
    assert record.t_first_chunk <= record.t_playing <= record.t_done
    row = json.loads((tmp_path / "turns.jsonl").read_text())
    for key in ("decode_s", "stt_s", "first_chunk_s", "playback_start_s", "end_to_sound_s"):
        assert row[key] is not None
    assert row["ts"].endswith("+00:00") and row["local"]  # explicit zones


async def test_first_clause_is_sent_to_tts_first_and_playback_starts_before_the_rest(
    tmp_path: Path,
) -> None:
    tts = FakeTts(delay=0.05)
    record = await pipeline(tmp_path, tts=tts).run_turn(utterance(), 1)
    assert tts.spoken[0] == split_clauses(TEXT, 6)[0] and 3 <= record.first_clause_words <= 6
    assert record.t_playing is not None and record.t_done is not None
    assert record.t_done - record.t_playing > 0.05  # later clauses were still being made


async def test_log_has_no_transcript_or_audio_by_default(tmp_path: Path) -> None:
    await pipeline(tmp_path).run_turn(utterance(), 1)
    assert "kitchen" not in (tmp_path / "turns.jsonl").read_text()
    names = await asyncio.to_thread(lambda: sorted(p.name for p in tmp_path.iterdir()))
    assert names == ["turns.jsonl"]


async def test_debug_flag_keeps_the_transcript_and_the_input_wav(tmp_path: Path) -> None:
    await pipeline(tmp_path, debug=Debug(tmp_path / "dbg")).run_turn(utterance(), 1)
    assert json.loads((tmp_path / "turns.jsonl").read_text())["text"] == TEXT
    assert (tmp_path / "dbg" / "turn001.wav").exists()


async def test_empty_transcript_says_nothing(tmp_path: Path) -> None:
    tts, sink = FakeTts(), FakeSink()
    record = await pipeline(tmp_path, FakeStt("  "), tts, sink).run_turn(utterance(), 1)
    assert record.outcome == "empty" and tts.spoken == [] and sink.played == []


class BrokenStt:
    name = "broken"

    async def transcribe(self, pcm: object, sample_rate: int = 16000, *, language: str = "en"):  # type: ignore[no-untyped-def]
        raise RuntimeError("stt exploded")


async def test_stt_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    record = await pipeline(tmp_path, BrokenStt()).run_turn(utterance(), 1)  # type: ignore[arg-type]
    assert record.outcome == "error" and "stt exploded" in record.error


async def test_tts_failure_midway_is_recorded_and_playback_ends(tmp_path: Path) -> None:
    sink = FakeSink()
    record = await pipeline(tmp_path, tts=FakeTts(fail_on="timer"), sink=sink).run_turn(
        utterance(), 1
    )
    assert record.outcome == "error" and "fake TTS failure" in record.error
    assert len(sink.played) >= 1  # the first clause was already playing


async def test_a_new_click_cancels_playback_and_starts_a_fresh_turn(tmp_path: Path) -> None:
    records: list[TurnRecord] = []
    sink = FakeSink(play_delay=0.2)
    runner = TurnRunner(
        pipeline(tmp_path, FakeStt([TEXT, "Second one."]), sink=sink, records=records)
    )
    await runner.submit(utterance())
    await asyncio.wait_for(sink.playing.wait(), 3)
    await runner.submit(utterance())  # the user clicked again
    await runner.wait()
    await runner.close()
    assert [r.outcome for r in records] == ["cancelled", "spoken"]
    assert sink.stops >= 1


async def test_interrupt_from_the_click_callback_stops_talking_immediately(tmp_path: Path) -> None:
    records: list[TurnRecord] = []
    sink = FakeSink(play_delay=0.2)
    runner = TurnRunner(pipeline(tmp_path, sink=sink, records=records))
    await runner.submit(utterance())
    await asyncio.wait_for(sink.playing.wait(), 3)
    runner.interrupt()
    await runner.close()
    assert [r.outcome for r in records] == ["cancelled"] and sink.stops >= 1


async def test_no_tasks_are_left_behind(tmp_path: Path) -> None:
    before = len(asyncio.all_tasks())
    sink = FakeSink(play_delay=0.1)
    runner = TurnRunner(pipeline(tmp_path, sink=sink))
    for _ in range(3):
        await runner.submit(utterance())
        await asyncio.sleep(0.05)
    await runner.close()
    await asyncio.sleep(0.05)
    assert len(asyncio.all_tasks()) <= before


async def test_a_corrector_changes_what_is_spoken_but_the_log_holds_no_text(tmp_path: Path) -> None:
    from aiglasses.assistant.glossary import Correction

    tts = FakeTts()
    seen: list[Correction] = []
    pipe = EchoPipeline(
        FakeStt("ask zorvexquill now please"), tts, FakeSink(), TtsOptions(voice='v', speed=1.0),
        turn_log=TurnLog(tmp_path / "turns.jsonl"),
        first_clause_max_words=6,
        corrector=lambda t: (
            t.replace("zorvexquill", "Zorvex Quill"),
            [Correction("zorvexquill", "Zorvex Quill", 100.0, "spacing/case")],
        ),
        on_correction=seen.append,
    )  # fmt: skip
    record = await pipe.run_turn(utterance(), 1)
    assert " ".join(tts.spoken) == "ask Zorvex Quill now please"
    assert record.raw_text == "ask zorvexquill now please" and record.corrections == 1
    assert len(seen) == 1
    log = (tmp_path / "turns.jsonl").read_text()
    assert "zorvex" not in log.lower() and json.loads(log)["corrections"] == 1
