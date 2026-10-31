# Latency budget: end marker to first spoken word

Target: 2 to 5 s to the first spoken word (not 30 ms). "M" = measured on this PC (i5-1135G7, CPU only); "E" = estimate, not measured.

| Stage | Time | Basis |
|---|---|---|
| Glasses send the end marker `0a 01` (the user stops speaking, the glasses decide when to end) | unknown, not measured | The glasses end the session themselves; how long after the last word is UNVERIFIED (Phase A voice tests will show it) |
| Decode Opus packets to PCM | about 0.02 s | E (PyAV decode of 288 packets) |
| STT, 5.8 s utterance | 0.2 s Moonshine tiny, 0.3 s Moonshine base, 0.7 s faster-whisper base.en, 2.1 s small.en | M |
| Cloud LLM, first token | 0.4 to 1.2 s | E (provider not chosen) |
| LLM time to complete the first sentence or clause | +0.3 to 0.8 s | E (about 15 tokens at 30 to 60 tokens/s) |
| Kokoro, first chunk (first clause of 2 to 4 words / one 8-word sentence) | 0.4 to 0.9 s / 1.3 to 1.5 s | M (about 0.5 s of compute per second of speech) |
| Playback start, PC speakers | about 0.05 s | E |
| Playback start, glasses over Bluetooth A2DP | 0.15 to 0.4 s | E (to be measured in Phase C) |

## Totals
- Best case (Moonshine tiny, fast LLM, first clause spoken): 0.2 + 0.4 + 0.3 + 0.5 + 0.05 = about 1.5 s (E for the cloud part).
- Typical (Moonshine base, full first sentence): 0.3 + 0.8 + 0.5 + 1.4 + 0.3 = about 3.3 s.
- Pessimistic (faster-whisper small.en, slow LLM, Bluetooth): 2.1 + 1.2 + 0.8 + 1.5 + 0.4 = about 6 s.

## Where the time can be cut
1. Cut the first TTS chunk at a clause boundary (comma, "and", about 4 words) instead of a full sentence: saves about 0.5 to 0.9 s. Synthesis speed is about 2x real time, so later chunks stay ahead of playback.
2. Prefer Moonshine: it is 3 to 10 times faster than faster-whisper here. Streaming STT would save only about 50 ms (measured), so it is not planned.
3. Start TTS as soon as the first clause arrives from the LLM stream; keep the LLM reply short (the persona already asks for it).
4. Warm up STT and TTS at startup (the first call is slower: Moonshine load 3 to 5 s, faster-whisper 11 s on a cold start, Kokoro one warm-up synthesis).

## What this document does not cover
The time between the user's last word and the glasses' end marker, any network latency to the LLM, and Bluetooth playback are not measured. They are filled in by the Phase A voice tests,
Phase B (with a real key) and Phase C/D respectively.
