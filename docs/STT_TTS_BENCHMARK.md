# STT and TTS benchmark (Phase A, 2026-10-31)

Code: `bench/` (separate project, see ADR 0007). Raw results: `bench/out/*.json` (not committed). The WAVs to judge by ear are in `bench/out/tts/` (not committed; they are synthetic).
Each figure is the median of 3 runs after a warm-up. Peak RAM is the process peak working set, one process per engine.

## This PC (measured)
Intel Core i5-1135G7 (4 cores, 8 threads), 11.7 GB RAM (not 8 GB), Intel Iris Xe graphics only. **No NVIDIA GPU**, so everything below runs on the CPU, and CUDA builds are irrelevant.

## STT: speed and memory (measured)
Audio: `capture_a`, one real 5.76 s glasses utterance. Windows 11, CPU only, int8 where the engine supports it.

| Engine | Model download | Wall time | Real-time factor | Peak RAM | Load (cached / first) |
|---|---|---|---|---|---|
| Moonshine tiny (en) | 44 MB | 0.21 s | 0.037 | 156 MB | 0.6 s / 3.2 s |
| Moonshine base (en) | 141 MB | 0.29 s | 0.050 | 337 MB | 0.9 s / 4.8 s |
| faster-whisper base.en, int8, greedy | 145 MB | 0.68 s | 0.119 | 321 MB | 0.7 s / 10.8 s |
| faster-whisper small.en, int8, greedy (reference) | 483 MB | 2.08 s | 0.362 | 697 MB | 3.9 s / 11.4 s |

After peak normalisation to -3 dBFS the times are the same within noise (0.24, 0.25, 0.70, 2.19 s). The raw capture peaked at -14 dBFS.

Install effort on Windows: all four install with plain wheels (`uv sync`), no compiler, no extra native install. Moonshine ships its own DLLs; faster-whisper needs the `ctranslate2` wheel.

### Accuracy: NOT MEASURED yet (recorder and table are ready)
No word error rate is reported yet. `capture_b` was excluded from all benchmarks on the user's instruction (its content is unknown) and deleted from `bench/data`; `capture_a` has no reference
text, so it is skipped by the accuracy run. The accuracy data comes from `aiglasses bench-record`: it shows 12 sentences one at a time, the user reads each aloud and single-clicks the rear button,
and the displayed sentence is saved as the reference (a take marked as misread goes to `excluded/` and the sentence is asked again). Kinds: about 2 s, about 10 s, two questions, two commands,
numbers and times, a name, casual, two longer, a short reply. Every take's duration, frame count and end reason go to `bench/data/takes.jsonl`. Then `uv run python -m bench.table` in `bench/`
runs the four engines on raw and peak-normalised input and prints WER (jiwer, lower-cased, punctuation removed, small numbers and ordinals converted to words on both sides), speed and RAM together.
The table pipeline was validated on one synthetic Kokoro clip (not a real result).

## Transcribing while the glasses are still streaming (measured, Moonshine only)
The audio was fed in 20 ms steps paced in real time, as the glasses send it, and compared with transcribing everything after the end marker.

| Engine | Time from last audio to final text, streaming | Same, after the end marker only |
|---|---|---|
| Moonshine tiny | 0.15 s | 0.20 s |
| Moonshine base | 0.17 s | 0.23 s |

Streaming saves about 50 ms. Because the batch pass already finishes in about 0.2 s, **streaming STT is not worth the complexity**. faster-whisper has no streaming mode in this setup, so
for it the post-end cost stays the full 0.68 s (base) or 2.1 s (small); that is an argument for Moonshine, not for streaming.

## TTS: Kokoro-82M, voice `bf_isabella`, British English (measured)
Text: one sentence of 8 words (2.7 s of speech) and three sentences (6.5 to 7 s). `kokoro-onnx` 0.6.1, CPU.

| Variant | Speed | First audio, 1 sentence | First audio, 3 sentences in one call | First audio, 3 sentences sent one by one | Total for 3 sentences | Real-time factor | Peak RAM |
|---|---|---|---|---|---|---|---|
| fp32 (326 MB) | 1.00 | 1.47 s | 3.32 s | 1.43 s | 3.65 s | 0.51 | 735 MB |
| fp32 | 1.05 | 1.41 s | 3.12 s | 1.42 s | 3.81 s | 0.55 | 735 MB |
| int8 (92 MB) | 1.00 | 1.40 s | 3.41 s | 1.39 s | 3.44 s | 0.48 | 505 MB |
| int8 | 1.05 | 1.34 s | 3.72 s | 1.28 s | 3.21 s | 0.47 | 505 MB |

Findings:
- `create_stream` does not split sentences: a three-sentence text comes back as one chunk after 3.1 to 3.7 s. **The assistant must split text itself and send one sentence (or clause) at a time.**
- Synthesis time is roughly proportional to length (about 0.5 s of compute per second of speech), with almost no fixed cost: "Sure." took 0.41 s, a 1.5 s phrase 0.85 s, a 4.4 s sentence 2.5 s
  (int8, speed 1.05). So the **first chunk should be short: the first clause**, which brings time to first audio down to 0.4 to 0.9 s.
- Speed 1.0 versus 1.05 makes no timing difference; it shortens the speech by about 4 percent.
- fp32 and int8 take about the same time; int8 uses 230 MB less RAM. Which one sounds better is for the user to judge (files below).
- No espeak-ng or other native install is needed on Windows: `espeakng-loader` bundles it (GPL, see ADR 0007).
- Windows built-in voice (SAPI, via PowerShell): 3.4 s of speech for the one sentence, 1.2 s wall including starting PowerShell; it only writes whole files, so it is a quality baseline, not a latency one.
  Its one-call times are not comparable with Kokoro's streaming figures.
- Not benchmarked: sherpa-onnx (its newest releases are under 14 days old), the bf_isabella + bf_emma blend, and a GPU.

Listen to (all in `bench/out/tts/`): `kokoro-fp32_bf_isabella_one_sentence_speed1.0.wav` and `..._speed1.05.wav`, the same for `kokoro-int8_...`, the three-sentence files, and `windows_sapi_*.wav`.

## What is measured, what is not
Measured: hardware, STT speed/RAM/load, streaming versus batch for Moonshine, Kokoro first-chunk and total times, RAM, SAPI baseline.
Not measured: STT accuracy (needs your reference texts), voice quality (needs your ears), any network or LLM time, any playback over Bluetooth.

## What I need from you for Gate A
1. DONE (Gate A decisions, 2026-10-31): Kokoro int8, bf_isabella, speed 1.05 approved as the TTS default; fp32 stays an option. No streaming STT.
2. Run `aiglasses bench-record` (about 12 sentences), then tell me; I run `bench.table` and report speed, accuracy and RAM together. Recordings stay local and gitignored.
3. STT: Moonshine base is the provisional default, Moonshine tiny the fallback, faster-whisper base.en selectable. Final choice after the accuracy table.

## Round 2: more accurate models, hints and a glossary corrector (2026-10-31)

Status: engines, harness and corrector are built and measured on the 12 existing takes only. The
glossary results are NOT measured yet: the glossary file (`bench/data/glossary.txt`, private,
gitignored) does not exist, so no glossary sentences have been recorded.

### What the takes showed about the glasses (measured, 16 takes)
15 of 16 takes ended at 4.76 to 4.78 s with an end marker, whatever the sentence length; one ended
at 5.78 s (speech began at 4.0 s). Speech started about 1.3 s after the click because the recorder
told you to wait about 2 s. So a sentence has roughly 3.4 s. The 10 s and "longer" sentences
(s02, s05, s07, s08, s09) were cut off mid-sentence. What decides the end is still INFERRED
(fixed window from the click versus speech-aware); a take that speaks immediately for 8 s would
tell. Cut-off takes are detected automatically (speech still loud in the last 100 ms) and kept out
of the WER totals, which leaves only 7 usable sentences. Treat every number below as a first look.

### Result, 7 complete ordinary sentences, raw input, int8, 4 CPU threads
| Engine | WER | Median wall s | RTF | Load s | Peak RAM MB |
|---|---|---|---|---|---|
| moonshine tiny | 37.1% | 0.19 | 0.04 | 2.8 | 158 |
| moonshine base | 27.4% | 0.27 | 0.06 | 4.7 | 337 |
| moonshine v2 small | 27.4% | 1.01 | 0.21 | 4.9 | 334 |
| moonshine v2 medium | 25.8% | 1.26 | 0.26 | 9.1 | 529 |
| Parakeet TDT 0.6B v2 | 17.7% | 0.41 | 0.09 | 18.2 | 838 |
| faster-whisper base.en | 21.0% | 0.69 | 0.15 | 6.0 | 320 |
| faster-whisper small.en | 21.0% | 2.10 | 0.44 | 8.9 | 697 |
| faster-whisper small (en fixed) | 21.0% | 2.89 | 0.61 | 16.7 | 697 |

Load time is with warm file cache (first load from disk is slower). Several errors are in the
audio itself (all engines write "that sound to me" for "that sounds good to me").
On this sample Parakeet is the most accurate and is faster than Whisper base.en; Moonshine v2 is
slower than v1 base and not clearly better. Seven sentences cannot separate 21% from 17.7%.

Streaming versus batch (Moonshine, 5.76 s capture, time from last audio to final text):
v2 small 0.54 s streaming vs 0.84 s batch; v2 medium 0.30 s vs 1.05 s; v1 base 0.22 s vs 0.33 s.
Streaming v2 medium is the fastest to final text, but needs the live stream API.

### Install effort on Windows, licences, size
- Moonshine v2 small (136 MB) and medium (257 MB): same `moonshine-voice==0.1.5` wheel, models
  via `python -m moonshine_voice.download --model-arch 4|5`; MIT (code and English models). No compiler.
- Parakeet TDT 0.6B v2 int8 (631 MB on disk): `sherpa-onnx==1.13.8` plus `sherpa-onnx-core==1.13.8`
  (the core package carries the ONNX Runtime DLL; without it Windows loads its own older
  `onnxruntime.dll` and fails with "API version 28 is not available"). Model files pinned to a
  Hugging Face revision with published SHA-256; CC-BY-4.0 (NVIDIA). sherpa-onnx 2.0.0/2.0.1
  (23/27 Oct 2026) are too new, so 1.13.8 (10 Sep 2026) is used. No compiler.
- faster-whisper small (464 MB, MIT), already supported by the pinned library.

### Hints and custom vocabulary
- Moonshine v1/v2: no hint mechanism in the transcription path (a `bias_terms` field exists only in
  the agent-flow helper and is not passed to the model). Said plainly: none.
- Parakeet through sherpa-onnx: hotword biasing exists for transducers (`modified_beam_search`
  plus a hotwords file); NOT tested yet because it needs the glossary. It may need a SentencePiece
  model that this export does not ship; the harness records the error if so.
- faster-whisper: `initial_prompt` and `hotwords` both exist in 1.2.1; both are in the harness and
  run once the glossary exists.

### Glossary corrector (src/aiglasses/assistant/speech/glossary.py)
Deterministic, post-STT: windows of 1 to N+1 words are compared with each term and its spoken
forms by letters-only fuzzy ratio (threshold 88) or equal Metaphone key plus ratio 72. Never the
edge of a span: common function words; never fewer than 4 letters; never across sentence
punctuation. Every correction is returned with original, corrected, score and reason, and the echo
loop logs them to `logs/glossary_corrections.jsonl`. Unit-tested with synthetic terms only.
NOT measured on real takes yet (no glossary): glossary-term accuracy before/after and the false-
correction rate on ordinary sentences come from `python -m bench.glossary_eval`.
The LLM side is `Glossary.prompt_note()`: a short note that the transcript may contain recognition
errors plus the list of terms, built at runtime. No LLM is called.

### To finish this round (needs you)
1. Create `bench/data/glossary.txt`, one term per line (`RapDuo SM | rap duo s m` adds spoken forms).
2. `uv run aiglasses bench-record --glossary-file bench/data/glossary.txt`: records 5 short ordinary
   and 10 glossary sentences (skips what is already recorded). Click, wait about 1 s, speak.
3. Then I run `uv run python -m bench.round2` and `uv run python -m bench.glossary_eval`.

### Round 2 results with the glossary (measured 2026-10-31, 21 complete sentences)
Data: 11 ordinary and 10 glossary sentences (all contain the single term "RapDuo SM"); sentences cut
off by the 4.8 s window are excluded. Median of 3 runs, one process per engine, int8, 4 threads.
Only ONE glossary term appears in the recordings, so term accuracy means "RapDuo SM" only.

| Engine | Hints | WER ordinary | WER glossary | Term hits | Median wall s | Peak RAM MB |
|---|---|---|---|---|---|---|
| moonshine tiny | none available | 28.3% | 56.2% | 0/10 | 0.22 | 159 |
| moonshine base | none available | 17.2% | 51.6% | 0/10 | 0.32 | 336 |
| moonshine v2 small | none available | 17.2% | 34.4% | 0/10 | 1.00 | 336 |
| moonshine v2 medium | none available | 16.2% | 31.2% | 0/10 | 1.18 | 527 |
| parakeet int8 | none | 11.1% | 28.1% | 0/10 | 0.43 | 851 |
| parakeet int8 | sherpa hotwords | 11.1% | 26.6% | 1/10 | 0.58 | 852 |
| faster-whisper base.en | none | 13.1% | 42.2% | 0/10 | 0.87 | 320 |
| faster-whisper base.en | initial_prompt | 13.1% | 15.6% | 8/10 | 0.69 | 320 |
| faster-whisper base.en | hotwords | 13.1% | 6.2% | 9/10 | 0.58 | 320 |
| faster-whisper small.en | none | 13.1% | 34.4% | 0/10 | 2.31 | 697 |
| faster-whisper small.en | initial_prompt | 13.1% | 17.2% | 8/10 | 1.84 | 697 |
| faster-whisper small.en | hotwords | 11.1% | 17.2% | 8/10 | 1.90 | 697 |
| faster-whisper small (en fixed) | none | 13.1% | 39.1% | 0/10 | 2.33 | 697 |
| faster-whisper small (en fixed) | initial_prompt | 14.1% | 1.6% | 10/10 | 1.96 | 697 |
| faster-whisper small (en fixed) | hotwords | 14.1% | 1.6% | 10/10 | 2.43 | 697 |

Reading: without hints no engine writes the name correctly (0/10). Hints change that for
faster-whisper: base.en with `hotwords` gets 9/10 at 0.58 s, small with a hint gets 10/10 at about
2 s. Parakeet, the best engine on ordinary speech, barely benefits from sherpa hotwords (1/10).
Hints can bend ordinary sentences a little (small: 13.1% to 14.1%). One term and 21 sentences is a
small sample: treat this as a direction, not a final ranking. An earlier hinted run was discarded
because the glossary file was mis-parsed and then because the recordings were deleted mid-run.
"faster-whisper small + hotwords" hung for more than 30 minutes once while other work was using the
CPU; the harness now times a configuration out after 900 s.

### Corrector on the same transcripts (no hints), glossary of 4 terms
| Engine | Terms before -> after | WER glossary | Ordinary sentences changed (false corrections) |
|---|---|---|---|
| moonshine tiny | 0 -> 1 | 56.2% -> 53.1% | 0 of 11 |
| moonshine base | 0 -> 1 | 51.6% -> 48.4% | 0 of 11 |
| moonshine v2 medium | 0 -> 4 | 31.2% -> 18.8% | 0 of 11 |
| parakeet | 0 -> 1 | 28.1% -> 25.0% | 0 of 11 |
| faster-whisper small | 0 -> 1 | 39.1% -> 35.9% | 0 of 11 |
| others | 0 -> 0 | unchanged | 0 of 11 |
The corrector made no false correction, but it also fixes few misses at the conservative threshold.
Spoken forms in the glossary (`RapDuo SM | rap do s m`) are the lever to try next.
