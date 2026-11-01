# TTS bake-off (for your ears) - 2026-11-01

The same six assistant-style replies (greeting, one fact, three-sentence explanation, numbers, a
question, an apology) spoken sentence by sentence, as the assistant does, with 0.3 s between
sentences. WAVs: `bench/out/tts_bakeoff/<candidate>/<reply>.wav` (gitignored). Nothing was played.
Run again: `cd bench; $env:PYTHONPATH="src"; uv run --no-sync python -m bench.tts_bakeoff`.

## Measured on this PC (i5-1135G7, CPU, 8 candidates, median over the six replies)
| Candidate | Load s | Peak RAM MB | First audio s | RTF |
|---|---|---|---|---|
| Kokoro int8, bf_isabella, 1.00 | 3.2 | 339 | 1.72 | 0.47 |
| Kokoro int8, bf_isabella, 1.05 (old default) | 2.8 | 449 | 1.67 | 0.49 |
| Kokoro int8, bf_emma, 1.00 | 2.5 | 428 | 1.87 | 0.52 |
| Kokoro int8, af_heart, 1.00 | 2.5 | 423 | 1.77 | 0.48 |
| Kokoro int8, blend 0.6 isabella + 0.4 emma | 2.4 | 428 | 1.82 | 0.50 |
| Kokoro fp32, bf_isabella, 1.00 | 6.6 | 675 | 1.82 | 0.51 |
| Piper en_GB alba medium | 3.0 | 273 | 0.19 | 0.05 |
| Piper en_GB jenny_dioco medium | 2.9 | 345 | 0.19 | 0.05 |

First audio is the time to the first full sentence. In the live assistant the first chunk can be a
comma boundary, so Kokoro starts sooner than the table suggests. Piper is about ten times faster
than Kokoro; whether it sounds as good is your call.

## Not run, and why
| Candidate | Licence / size | Why not |
|---|---|---|
| ai4bharat/indic-parler-tts | Apache-2.0, about 3.6 GB plus PyTorch | Gated on Hugging Face (needs your licence click), and the download would exceed the 8 GB budget (7.35 GB used). Also very unlikely to run at real-time factor under 3 on this CPU. Tell me to go ahead (and raise the budget) if you want it measured. |
| Kyutai Pocket TTS | CC-BY-4.0 | Gated "auto" on Hugging Face (licence click needed); the newest PyPI releases (4.0.x, 2026-10-20) are also younger than 14 days. Click-through, then I can use 3.3.0. |

Licences: Kokoro model files Apache-2.0 (ONNX export; the phonemiser pulls GPL espeak data, see ADR
0007); Piper engine GPL-3.0-or-later (installed in the bench environment only, not in the product),
Piper voices MIT per the voice repository with each voice's dataset licence on its model card.

## Parameters now in `.env` / settings (no model change)
`TTS_SPEED` (set to 1.0), `TTS_SENTENCE_PAUSE_S` (0.3), `TTS_CLAUSE_PAUSE_S` (0.12), and `TTS_VOICE` may
be a blend such as `bf_isabella:0.6+bf_emma:0.4`. Try the blend and the pauses first; they cost nothing.

## How to judge
Listen to `greeting`, `explain` and `numbers` for each candidate. Tell me the winner (and a speed) and I
add it to the registry; Piper (Alba) was chosen and is wired: `TTS_ENGINE=piper`, `TTS_VOICE=piper-en-gb-alba-medium`.
