"""TTS bake-off: the same six assistant-style replies through several voices, saved as WAVs.

    uv run --no-sync python -m bench.tts_bakeoff            (from bench/, or bench/src on PYTHONPATH)

Candidates: Kokoro int8 voices (and a blend, and fp32), Piper English voices. For each, every reply
is spoken sentence by sentence like the assistant does, with the same pauses, and the script
records time to first audio, real-time factor and peak RAM. Nothing is played; you listen to the
WAVs in bench/out/tts_bakeoff/. Models come from the registry (models/ and bench/models/).
"""

import json
import re
import threading
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
OUT = ROOT / "out" / "tts_bakeoff"
SENTENCE_PAUSE_S = 0.3
REPLIES = {
    "greeting": "Good morning! I'm EVA. How can I help you today?",
    "fact": "The capital of France is Paris, which sits on the river Seine.",
    "explain": (
        "Rain forms when water vapour rises and cools into tiny droplets. "
        "Those droplets join together until they are too heavy to stay in the air. "
        "Then they fall to the ground as rain."
    ),
    "numbers": "Your meeting is at half past four on the twelfth of March, in room two hundred and fourteen.",
    "question": "Would you like me to read the first result, or search for something else?",
    "apology": "I'm sorry, I didn't quite catch that. Could you say it again, please?",
}
KOKORO = [
    ("kokoro-int8-isabella-1.00", "int8", "bf_isabella", 1.00),
    ("kokoro-int8-isabella-1.05", "int8", "bf_isabella", 1.05),
    ("kokoro-int8-emma-1.00", "int8", "bf_emma", 1.00),
    ("kokoro-int8-heart-1.00", "int8", "af_heart", 1.00),
    ("kokoro-int8-isabella60-emma40-1.00", "int8", "bf_isabella:0.6+bf_emma:0.4", 1.00),
    ("kokoro-fp32-isabella-1.00", "fp32", "bf_isabella", 1.00),
]
PIPER = [
    ("piper-alba-medium", "piper-en-gb-alba-medium/en/en_GB/alba/medium/en_GB-alba-medium.onnx"),
    (
        "piper-jenny-dioco-medium",
        "piper-en-gb-jenny-dioco-medium/en/en_GB/jenny_dioco/medium/en_GB-jenny_dioco-medium.onnx",
    ),
]
KOKORO_FILES = {"int8": "kokoro-v1.0.int8.onnx", "fp32": "kokoro-v1.0.onnx"}


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


class Peak(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self._stop_flag = threading.Event()
        self._process = psutil.Process()

    def run(self) -> None:
        while not self._stop_flag.is_set():
            self.peak = max(self.peak, self._process.memory_info().rss)
            time.sleep(0.1)

    def stop(self) -> int:
        self._stop_flag.set()
        return round(self.peak / 2**20)


def blend(engine: object, spec: str) -> object:
    if "+" not in spec:
        return spec
    parts = [p.partition(":") for p in spec.split("+")]
    weights = [float(w) if w else 1.0 for _, _, w in parts]
    total = sum(weights)
    return sum(
        w / total * engine.get_voice_style(name)  # type: ignore[attr-defined]
        for w, (name, _, _) in zip(weights, parts, strict=True)
    )


def render(speak, rate_of, out_dir: Path) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """speak(sentence) -> (samples float32, rate). Returns per-reply timing."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name, text in REPLIES.items():
        started = time.perf_counter()
        pieces: list[np.ndarray] = []
        first: float | None = None
        rate = 24000
        for sentence in sentences(text):
            samples, rate = speak(sentence)
            first = first if first is not None else time.perf_counter() - started
            if pieces:
                pieces.append(np.zeros(int(rate * SENTENCE_PAUSE_S), dtype=np.float32))
            pieces.append(samples)
        wall = time.perf_counter() - started
        audio = np.concatenate(pieces)
        sf.write(out_dir / f"{name}.wav", audio, rate)
        seconds = len(audio) / rate
        rows[name] = {
            "first_audio_s": round(first or wall, 2),
            "audio_s": round(seconds, 2),
            "rtf": round(wall / seconds, 2),
        }
    return rows


def run_kokoro(candidates: list[tuple[str, str, str, float]]) -> dict[str, object]:
    from kokoro_onnx import Kokoro

    results: dict[str, object] = {}
    for label, variant, voice, speed in candidates:
        peak = Peak()
        peak.start()
        started = time.perf_counter()
        engine = Kokoro(
            str(REPO / "bench" / "models" / "kokoro" / KOKORO_FILES[variant]),
            str(REPO / "bench" / "models" / "kokoro" / "voices-v1.0.bin"),
        )
        load = time.perf_counter() - started
        style = blend(engine, voice)
        lang = "en-gb" if voice.startswith("b") else "en-us"

        def speak(sentence: str, engine=engine, style=style, speed=speed, lang=lang):  # type: ignore[no-untyped-def]
            samples, rate = engine.create(sentence, voice=style, speed=speed, lang=lang)
            return samples, rate

        speak("Warm up.")
        rows = render(speak, None, OUT / label)
        results[label] = {"load_s": round(load, 2), "peak_ram_mb": peak.stop(), "replies": rows}
        print(label, "done", flush=True)
    return results


def run_piper(candidates: list[tuple[str, str]]) -> dict[str, object]:
    from piper import PiperVoice

    results: dict[str, object] = {}
    for label, relative in candidates:
        path = REPO / "models" / relative
        if not path.exists():
            results[label] = {"error": f"{path} missing; run `aiglasses models pull`"}
            continue
        peak = Peak()
        peak.start()
        started = time.perf_counter()
        voice = PiperVoice.load(path)
        load = time.perf_counter() - started

        def speak(sentence: str, voice=voice):  # type: ignore[no-untyped-def]
            chunks = list(voice.synthesize(sentence))
            audio = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
            return audio, chunks[0].sample_rate

        speak("Warm up.")
        rows = render(speak, None, OUT / label)
        results[label] = {"load_s": round(load, 2), "peak_ram_mb": peak.stop(), "replies": rows}
        print(label, "done", flush=True)
    return results


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {**run_kokoro(KOKORO), **run_piper(PIPER)}
    (OUT / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("| Candidate | Load s | Peak RAM MB | First audio s (median) | RTF (median) |")
    print("|---|---|---|---|---|")
    for label, data in results.items():
        if "error" in data:  # type: ignore[operator]
            print(f"| {label} | {data['error']} |")  # type: ignore[index]
            continue
        rows = data["replies"].values()  # type: ignore[attr-defined]
        firsts = sorted(r["first_audio_s"] for r in rows)
        rtfs = sorted(r["rtf"] for r in rows)
        mid = len(firsts) // 2
        print(
            f"| {label} | {data['load_s']} | {data['peak_ram_mb']} | {firsts[mid]} | {rtfs[mid]} |"
        )  # type: ignore[index]


if __name__ == "__main__":
    main()
