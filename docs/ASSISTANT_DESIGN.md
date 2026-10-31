# Voice assistant design (phases A to G)

Goal: single-click the rear button, speak; the glasses stream the voice over BLE; the PC transcribes locally, sends only text to a cloud LLM, and speaks the reply locally (Kokoro) through
the glasses speaker. Later, a rear double-click takes a photo on the glasses and the assistant can answer questions about it.

Legend: **measured** = on this PC or on the real glasses; **assumed** = from the app's code or an estimate; **open** = nothing known yet.

## Hard rules (unchanged)
Nothing new is sent to the glasses (Phases A to D need only BLE notifications and normal Bluetooth audio). The user answers every prompt and runs every audible or state-changing test. Test tones:
1 s, at most 10 percent volume, glasses endpoint only. API keys only from environment variables; no audio or transcripts saved unless a debug flag is set; fixtures are synthetic.
Heavy ML dependencies stay in `bench/` or optional extras (ADR 0007). No winrt package before 2026-11-07 (ADR 0006).

## Architecture
```
glasses --BLE 0x59--> Glasses.voice_requests() --PCM--> SpeechToText --text--> LlmClient (cloud, text only)
                                                                        |
                       AudioSink <--chunks-- TextToSpeech <-- sentence chunker <-- text deltas
```
Package `aiglasses.assistant` (built in Phase A): `SpeechToText`, `TextToSpeech`, `LlmClient`, `AudioSink` protocols plus fakes. Every cloud, audio and BLE interaction sits behind one of them.
All interfaces take a `language` so Hindi can be added later; only English is implemented.

## Phase A: foundations (done, waiting for Gate A)
- Voice codec CONFIRMED (user listened). Markers `03 01` and `0a 01`; one live burst of 288 frames (5.76 s) ended by the glasses' end marker. (measured)
- `Glasses.voice_requests()`: one decoded utterance per rear-click session with start/end timestamps, frame count and `end_reason` (end_marker, timeout, disconnect, restarted). Handles frames before
  the start marker and a missing marker. Unit-tested with synthetic frames; not yet run live.
- What ends a session: the glasses send `0a 01` and the app only reacts (assumed, from app code); the app has its own 15 s guard in chat mode (assumed). The user's 2 s and 10 s tests will measure the glasses' real behaviour.
- Benchmark and latency budget: `docs/STT_TTS_BENCHMARK.md`, `docs/LATENCY_BUDGET.md`. Accuracy is not measured yet (needs the user's reference texts).
- Gate A: STT default and voice choice.

## Fast-track: echo loop (built 2026-10-31, before the rest of Phase B)
`aiglasses assistant echo --output pc`: rear click -> `voice_requests()` -> STT -> clause splitter -> Kokoro -> PC speakers. No LLM, no network, nothing sent to the glasses.
- The reply is split into clauses by `assistant/chunker.py` (first clause 3 to 6 words, about 1 to 2 s of speech; later clauses up to 10 words; cuts at sentence ends, commas and before conjunctions).
- A new click (marker `03 01` or the first voice frame) calls `TurnRunner.interrupt()` at once: the speaker stops, generation is cancelled, a fresh turn starts. The PC speaker runs in its own process
  (`assistant/audio_worker.py`) because sounddevice must not share a process with BLE (ADR 0004).
- Per-turn latency log `logs/assistant_turns.jsonl`: decode, STT, first chunk, playback start, end-to-sound, with UTC and local stamps. No text and no audio unless `--debug`.
- Optional extra `assistant` in the root project (pins in `pyproject.toml`, 14-day rule enforced with constraints for new transitive packages; existing locked packages, including the BLE stack, unchanged).
- `--output null` runs everything without sound (latency check); `--wav FILE` runs one turn from a 16 kHz mono WAV without the glasses.

## Phase B: assistant core (built 2026-10-31)
```
rear click -> voice_requests() -> TurnJoiner -> TurnRunner -> ConversationPipeline
  STT -> glossary correction -> RoutedLlmClient -> ClauseStream -> clean_reply -> TTS -> AudioSink
                                   |-- tier 0: local handlers (time, date, stop, repeat, heard)
                                   |-- tier 1: fast model (LLM_FAST_*)   |-- tier 2: main model
```
- **Settings:** `aiglasses.settings.AssistantSettings`, environment > `.env` > `config.toml` >
  defaults; `docs/CONFIG.md`. A test forbids model names, voice ids and engine names elsewhere.
- **LLM:** `LlmClient` protocol; `LangChainLlmClient` is the only module importing LangChain
  (ADR 0009). Providers are factories in a registry. Streaming with `astream`, retries only before
  the first token, a whole-request timeout, cancellation closes the provider stream.
- **Router:** `ROUTER_MODE` off, rules or model. Tier 0 never calls an LLM. Every turn logs tier,
  role, reason and router time; never the key, and no text unless `LOG_TRANSCRIPTS`.
- **Speaking early:** the reply is cut into clauses while it streams (first clause up to
  `TTS_FIRST_CLAUSE_MAX_WORDS` words); each is cleaned (no markdown, emoji, links; numbers, times
  and percentages as words) and sent to TTS while the LLM keeps writing.
- **Barge-in and joining:** a new rear click stops playback and generation at once; the interrupted
  reply stays in history marked as interrupted. If the previous request was cut off by the 4.8 s
  window and the new click starts within `TURN_JOIN_WINDOW_S`, the two audio clips are joined and
  the earlier exchange is replaced.
- **Caps:** `HISTORY_TURNS`, `MAX_TURNS_PER_SESSION`, `MAX_REPLY_SECONDS`, `LLM_MAX_TOKENS`.
- **Failure modes:** LLM or network failure before speech: a short local line
  (`FALLBACK_LLM_ERROR`); mid-reply: the speaker is stopped cleanly; empty recognition: the
  no-speech line; TTS failure: logged, loop continues; BLE drop: reconnect with 2, 5, 10, 20, 30 s
  backoff (the glasses can need about 30 s to advertise again); backoff resets only after a session
  that served a request.
- **Privacy:** text and audio are not kept unless `LOG_TRANSCRIPTS` / `DEBUG_SAVE_AUDIO`; the
  logging filter removes configured keys and key-shaped strings; `assistant check` prints secrets
  only as set / NOT set; tests block non-loopback network access and never read `.env`.
- **Not verified live:** the whole loop with the real glasses, real provider replies and real
  speaker output. See PROGRESS.md for what has and has not been run.

## Phase C: glasses speaker
Pair the glasses as a Windows audio device (the user does this). Match endpoints by name, record the sample rates Windows reports (earlier: A2DP 48 kHz stereo, HFP 16 kHz mono), fall back to PC speakers.
Risk (open): pairing may change what the rear button does; re-test that a single click still produces the BLE voice stream, and if not, fall back to PC speakers instead of working around it.
The assistant never uses the Windows headset microphone (low-quality profile); the BLE voice stream is the microphone. Current state: `aiglasses audio devices` found no glasses endpoint on 2026-10-31.
Gate C.

## Phase D: live loop
`assistant run --input glasses --output glasses`: connect, wait for a click, answer, repeat. Reconnect with backoff (advertising can take up to about 30 s), clean Ctrl+C, a clear message when the glasses
sleep. Short turns are designed for if the 10 s test shows the glasses cutting the voice. Measure end-to-end latency over several turns. Gate D: the user's conversations.

## Phase E: Wi-Fi Direct (design only; blocked until 2026-11-07 and explicit approval)
Already built and tested with fakes: interface, preflight, session with 16 s / 15 s limits, one retry, rollback. Needs the winrt pin (ADR 0006), a 2.4 GHz or disconnected-Wi-Fi preflight,
the enable frame `bc410400935c02010401` approved by the user, and a full rollback (disconnect, restore the recorded network, verify internet). HTTP stays GET-only. `02 01 09` stays blocked.

## Phase F: vision (design only)
Rear double-click makes the glasses take the photo themselves (assumed; a 0x73 type-2 "image ready" event follows). No photo command from the PC is needed. Flow:
event -> (PC goes to Wi-Fi Direct) -> `GET /files/media.config` -> download the newest image -> restore Wi-Fi -> vision model (cloud) -> speech.
Problems: (1) the PC's single adapter may drop the internet while the group is up, so the cloud call must wait for the restore (adds seconds; a second USB Wi-Fi adapter removes this);
(2) the album read must run before and after so we know which file is new; (3) the glasses may clear imported files (deletion risk, accepted for test data only); (4) the image goes to a cloud vision model,
so it needs an on/off privacy switch, default off, and a spoken or logged notice when it is on; (5) the double-click's actual event has not been seen live (open).

## Phase G: polish
README quickstart, privacy switches (cloud vision, saving audio/transcripts), clean install from scratch on a fresh machine (including `bench/` separation and model download with checksums).

## Risks
| Risk | Status |
|---|---|
| The glasses end a session earlier or later than expected | open until the 2 s and 10 s tests |
| Pairing for audio breaks the rear-click voice stream | open (Phase C check) |
| TTS first audio about 1.4 s per sentence on this CPU | measured; mitigated by clause-level first chunk (planned) |
| Quiet microphone signal hurts STT | partly measured (capture_a peaked at -14 dBFS, capture_b at -39); normalise before STT |
| Cloud cost or runaway loop | turn cap and token cap planned |
| GPL components (espeak-ng, phonemizer) | fine for personal use; would matter if distributed |
| One BLE connection at a time | the Cyan app and the phone's Bluetooth must stay off |

## Recognition errors and the glossary (round 2)
`aiglasses assistant echo --stt <engine> --glossary` runs the deterministic corrector after STT and
prints raw and corrected text. Phase B puts `Glossary.prompt_note()` into the LLM system prompt so
the model also reads near-miss words as the user's terms. Hard limit found: the glasses stop a
voice session about 4.8 s after the click, so requests must be short (see STT_TTS_BENCHMARK.md).

## Local-first lanes (built 2026-11-01; `ROUTER_MODE=local_first`)
```
utterance -> STT -> LocalFirstClient
  lane 0  rules: time/date/stop/repeat/heard handlers + intents.yaml (photo, battery, media counts,
          forget, describe) -> tool -> short spoken result            (no model at all)
  lane 1  ONE constrained call per step to llama-server (forced JSON, 127.0.0.1):
          {"action": answer | tool | escalate, "text", "tool", "args", "reason"}
          at most AGENT_MAX_STEPS steps within AGENT_TIME_BUDGET_S
  lane 2  cloud (LLM_*): same history, native tool calling, after a one-line filler
```
- **Stable prompt prefix.** Persona, rules and tool schemas come first, then history, then the
  utterance with the current time appended, so llama-server reuses its KV cache between turns.
  Non-thinking mode (`--reasoning off`). No separate router model.
- **Escalation (categories are logged, never content):** `research` (deep dive, think hard, big
  model), `current_facts` (time-sensitive facts with no search tool, or an unaided local answer to
  one), `dissatisfied` (a correction such as "no, I meant", or the same question again inside the
  memory window), `unsure` (the model must escalate instead of guessing), `invalid_json` (after one
  retry), `step_cap`, `time_budget`, `local_down` (server missing or down), `local_slow` (no first
  token within `LOCAL_FIRST_TOKEN_TIMEOUT_S`). Rules live in `escalation.yaml` (data).
- **Time-sensitive questions** go to `search_web` first (ddgs, titles and snippets only, results
  tainted), then the local model answers from the results; a blocked or rate-limited search gets a
  short spoken line and never crashes the turn.
- **Memory.** `MEMORY_MAX_TURNS` or `MEMORY_TTL_S`, whichever hits first; RAM only; tool calls and
  search results kept as one-line notes; interrupted replies flagged; `forget_all_that` (rules and
  model) clears memory and pending turn joins, leaves no trace of the forget request, and never
  touches log files.
- **Tool rules, enforced in code (`tools/base.py`):** only registered and enabled tools run; a
  state-changing tool (photo) runs only if the user's own words asked for it this turn (intents
  file, with near-miss rejects); once web or camera text has entered the turn (taint) every device
  and state-changing tool is refused for the rest of that turn; per-turn call cap and timeout;
  recording tools stay blocked (they need `TOOLS_ENABLED`, `ALLOW_RECORDING_TOOLS` and a CONFIRMED
  command, and even then the facade has no recording method).
- **Short answers.** The persona asks for `REPLY_TARGET_SENTENCES`; speech stops at
  `REPLY_MAX_SENTENCES` sentences for every lane.
- **Fallback.** If the local server is down the filler plays and the cloud answers; if the cloud
  also fails the usual spoken error line is used.
- **Turn taking.** `turn_end.py` decides if a request was finished (timeout, trailing silence of
  `TURN_END_SILENCE_MS`, final punctuation). Only cut-off requests are joined with the next click,
  and joining adds no waiting.
- **Not verified live:** the whole loop with real glasses and a real cloud model; local speed
  numbers (see LOCAL_LLM_BENCHMARK.md).
