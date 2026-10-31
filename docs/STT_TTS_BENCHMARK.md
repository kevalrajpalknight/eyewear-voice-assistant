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

### Accuracy: NOT MEASURED yet
No word error rate is reported because there are no reference transcripts. Needed from the user: type the reference text for each recording into `bench/data/<name>.txt` (the harness
reads it and computes WER with `jiwer` after lower-casing and stripping punctuation). Existing captures: `capture_a` (5.76 s) and `capture_b` (an earlier, much quieter burst, peak -39 dBFS;
several engines return nothing for it and one returned 29 words, so its content is unknown and it should not be used until the user says what it contains). The plan asks for about 10 new
sentences recorded with `aiglasses ai-voice record`; see "What I need from you" at the end.

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
1. Listen to the TTS WAVs and say whether bf_isabella at 1.05 sounds right, and fp32 versus int8.
2. Run `ai-voice record` for about 10 different sentences (include one of ~2 s and one of ~10 s), type each reference text into `bench/data/<name>.txt` next to its WAV, and tell me the file names.
   Then I rerun the accuracy table. Do not paste the recordings anywhere; they stay local.
3. Choose the STT default after seeing the accuracy table. Current speed ranking: Moonshine tiny, Moonshine base, faster-whisper base.en, faster-whisper small.en.
