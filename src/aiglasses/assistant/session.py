"""Glue for the CLI: load engines, connect the glasses, run the echo loop or the guided recorder."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path

import numpy as np
import soundfile as sf
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from aiglasses.assistant.echo import Debug, EchoPipeline, TurnLog, TurnRecord, TurnRunner
from aiglasses.assistant.glossary import Correction, Glossary
from aiglasses.assistant.interfaces import SpeechToText, TextToSpeech, TtsOptions
from aiglasses.assistant.recorder import SENTENCES, Sentence, record_sentences
from aiglasses.assistant.speaker import NullSink, SubprocessSpeakerSink
from aiglasses.config import Settings
from aiglasses.domain.errors import GlassesError
from aiglasses.glasses import Glasses
from aiglasses.services.voice import EndReason, VoiceUtterance

console = Console()


def load_engines(
    models_dir: Path, stt_name: str, tts_variant: str, normalize: bool
) -> tuple[SpeechToText, TextToSpeech]:
    from aiglasses.assistant.engines import FasterWhisperStt, KokoroTts, MoonshineStt

    stt: SpeechToText
    if stt_name == "fw-base.en":
        stt = FasterWhisperStt(models_dir, normalize=normalize)
    else:
        stt = MoonshineStt(models_dir, stt_name.removeprefix("moonshine-"), normalize=normalize)
    return stt, KokoroTts(models_dir, tts_variant)


async def warm_up(stt: SpeechToText, tts: TextToSpeech, options: TtsOptions) -> None:
    """Run each engine once so the first real turn is not slowed by lazy initialisation."""
    await stt.transcribe(np.zeros(8000, dtype=np.int16))
    async for _ in tts.synthesize_stream("Ready.", options):
        break


def describe(record: TurnRecord) -> str:
    def seconds(value: float | None) -> str:
        return "-" if value is None else f"{value:.2f}s"

    row = record.to_json()
    return (
        f"turn {record.turn}: {record.outcome}; {record.words} words in {record.clauses} clauses "
        f"(first {record.first_clause_words}); end marker to sound {seconds(row['end_to_sound_s'])}"  # type: ignore[arg-type]
        f" = decode {seconds(row['decode_s'])} + stt {seconds(row['stt_s'])}"  # type: ignore[arg-type]
        f" + first audio {seconds(row['first_chunk_s'])} + start {seconds(row['playback_start_s'])}"  # type: ignore[arg-type]
    )


async def run_echo(
    settings: Settings,
    *,
    models_dir: Path,
    stt_name: str,
    tts_variant: str,
    voice: str,
    speed: float,
    output: str,
    normalize: bool,
    debug: bool,
    wav: Path | None,
    glossary_file: Path | None = None,
) -> None:
    options = TtsOptions(voice=voice, speed=speed)
    console.print("Loading speech engines ...")
    stt, tts = await asyncio.to_thread(load_engines, models_dir, stt_name, tts_variant, normalize)
    await warm_up(stt, tts, options)
    sink: SubprocessSpeakerSink | NullSink
    sink = NullSink() if output == "null" else SubprocessSpeakerSink()
    if isinstance(sink, SubprocessSpeakerSink):
        await sink.start()
    glossary = Glossary.load(glossary_file) if glossary_file is not None else None
    if glossary is not None and not glossary:
        raise GlassesError(f"{glossary_file} holds no terms")
    corrections_path = settings.log_dir / "glossary_corrections.jsonl"

    def log_correction(correction: Correction) -> None:
        corrections_path.parent.mkdir(parents=True, exist_ok=True)
        with corrections_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(correction)) + "\n")

    def show(record: TurnRecord) -> None:
        console.print(escape(describe(record)))
        if record.raw_text is not None:
            console.print(f"  raw:       {escape(record.raw_text)}")
        if record.corrected_text is not None:
            console.print(f"  corrected: {escape(record.corrected_text)}")
        elif record.text:
            console.print(f"  heard:     {escape(record.text)}")

    debug_keep = Debug(settings.captures_dir / "debug") if debug else None
    pipeline = EchoPipeline(
        stt, tts, sink, options,
        turn_log=TurnLog(settings.log_dir / "assistant_turns.jsonl"),
        debug=debug_keep,
        on_record=lambda r: console.print(
            escape(describe(r)) + (f"\n  heard: {escape(r.text)}" if r.text else "")
        ),
    )  # fmt: skip
    runner = TurnRunner(pipeline)
    try:
        if wav is not None:
            data, rate = sf.read(wav, dtype="int16")
            if rate != 16000:
                raise GlassesError(f"{wav} is {rate} Hz; 16000 Hz mono is required")
            pcm = data if data.ndim == 1 else data[:, 0]
            now = time.time()
            seconds = len(pcm) / 16000
            utterance = VoiceUtterance(
                pcm, 16000, now - seconds, now, len(pcm) // 320, EndReason.END_MARKER, None, now
            )
            await runner.submit(utterance)
            await runner.wait()
            return
        async with Glasses.connect(settings) as glasses:
            console.print(
                f"Connected. STT {stt.name}, TTS {tts.name}, voice {voice} at {speed}. "
                "Single-click the rear button (don't hold), wait about 2 s, speak, and listen. "
                "Click again while it talks to interrupt. Ctrl+C to stop."
            )
            async for utterance in glasses.voice_requests(on_start=runner.interrupt):
                await runner.submit(utterance)
            console.print("[yellow]The glasses disconnected.[/]")
    finally:
        await runner.close()
        if isinstance(sink, SubprocessSpeakerSink):
            await sink.close()


async def run_bench_record(settings: Settings, out_dir: Path, sentences: list[Sentence]) -> None:
    def show(index: int, total: int, sentence: Sentence, take: int) -> None:
        retry = " (retake)" if take > 1 else ""
        console.print(f"\n[bold]Sentence {index} of {total}[/] [{sentence.kind}]{retry}")
        console.print(f'\n    [bold cyan]"{escape(sentence.text)}"[/]\n')
        console.print(
            "Single-click the rear button (do not hold), wait about 1 s, then read it aloud. "
            "The glasses stop listening about 4.8 s after the click."
        )

    def report(row: dict[str, object]) -> None:
        console.print(
            f"  got {row['audio_s']} s of audio, {row['frames']} frames, ended by "
            f"{row['end_reason']}"
            + ("; speech was still going when it ended (cut off)" if row["cut_off"] else "")
        )

    async def ask_misread() -> bool:
        return await asyncio.to_thread(
            typer.confirm, "Did you misread it? (it will be excluded and you read it again)",
            False,
        )  # fmt: skip

    def on_stray(utterance: VoiceUtterance) -> None:
        console.print(f"  [dim]ignored an earlier click ({utterance.seconds:.1f} s)[/]")

    async with Glasses.connect(settings) as glasses:
        utterances: AsyncIterator[VoiceUtterance] = glasses.voice_requests()
        rows = await record_sentences(
            utterances, out_dir, sentences=sentences, show=show, report=report,
            ask_misread=ask_misread, on_stray=on_stray,
        )  # fmt: skip
    table = Table(title="Takes")
    for column in ("Sentence", "Take", "Words", "Audio s", "Ended by", "Cut off", "Excluded"):
        table.add_column(column)
    for r in rows:
        table.add_row(
            str(r["sentence"]), str(r["take"]), str(r["words"]), str(r["audio_s"]),
            str(r["end_reason"]), str(r["cut_off"]), str(r["excluded"]),
        )  # fmt: skip
    console.print(table)
    console.print(f"Saved under {out_dir} (local, gitignored). Longest take: "
                  f"{max(float(str(r['audio_s'])) for r in rows):.2f} s")  # fmt: skip


__all__ = ["SENTENCES", "run_bench_record", "run_echo"]
