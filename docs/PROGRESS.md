# Progress: assistant conversation core (M1 to M9)

Started and worked 2026-10-31. Rules in force: no commits while a benchmark runs, `.env` is never
opened or printed, at most 3 live LLM calls (only `assistant check --live`), nothing new is sent
to the glasses, no pairing.

## Facts found at the start (they differ from the task text)
- There was no turn-joining code and no earlier backup script. Both were created here
  (`assistant/joiner.py`, `scripts/backup_bench.py`; backups go to `backups/`, gitignored).
- `bench/src` disappeared from the working tree once for an unknown reason (not by my command);
  it is tracked in git and was restored with `git checkout -- bench/src`. Nothing was lost. This is
  the "unexplained loss of a file" case; it happened before this task and was reported then.
- The echo `--glossary` flag shipped in commit aa025e1 never reached the pipeline (a silent failed
  string replace). Fixed by rebuilding the echo setup on settings; covered by a test.
- The user's glossary file puts several terms on one line separated by `|`. The format is
  `term | spoken form, spoken form`, one term per line. The file was not edited; the benchmark uses
  the first field of each line, `check` warns about the extra `|`, and
  `bench/data/glossary_split.txt` (private, gitignored) holds a one-term-per-line version.

## Milestones
| | State | Notes |
|---|---|---|
| M1 settings | done | `settings.py`, `.env.example`, `docs/CONFIG.md`, forbidden-literals test, secrets guard hook, tzdata pin |
| M2 LLM layer | done | `llm_client.py` (only LangChain module), extra `llm`, ADR 0009; no LiteLLM |
| M3 router | done | tier 0 handlers, rules and model modes, fallbacks, decision log |
| M4 conversation core | done | cleaner, clause stream, joiner, history, barge-in, failure lines, reconnect backoff |
| M5 CLI | done | `assistant run`, `assistant check [--live]`, `assistant echo` kept |
| M6 engine registries | done | `registry.py`: STT and TTS by name from settings only |
| M7 tests | done | see the final report for counts and coverage |
| M8 docs | done | README quickstart, CONFIG, ASSISTANT_DESIGN, LATENCY_BUDGET, ADR 0009 |
| M9 verification | done except the live call | 356 tests pass, new code 86 to 100 % covered, offline dry run with real Parakeet and Kokoro OK, `assistant check` OK; live call NOT made by me (budget used up, see below) |

## Decisions (conservative defaults, no user input needed)
- `STT_MODEL_DIR` added next to `TTS_MODEL_DIR` (the task named only the TTS one).
- `AUDIO_VOLUME_LIMIT` scales samples before the speaker process.
- Fallback lines are settings (`FALLBACK_*`), with plain English defaults.
- `--no-router` keeps tier-0 local answers and sends everything else to the main model.
- Backoff for reconnects resets only after a session that served a request.
- A lifetime counter in `logs/live_calls.count` enforces the three live calls in code.
- Tests block all non-loopback network access (`conftest.py`).
- Commits are grouped, not one per milestone: the pieces import each other and the pre-commit hook
  runs mypy and the whole test suite on every commit.

## Blockers
- none. Things only the user can check are listed in the final report.

## Resolved
- The deleted `bench/data` files (and probably the earlier `bench/src`) were deleted by the user by
  accident; recordings were restored byte-identical from `backups/`.

## Live-call budget
`logs/live_calls.count` reads 3 (last written 2026-10-31 23:47 IST) although this session made no
live call: the user's own `assistant check --live` runs used the budget. No live result has been
seen by me. To allow more calls, the user can reset or delete that file.

## Commit
b980c7e holds M1 to M8 together (the pre-commit hook type-checks the whole tree, so the
milestones could not be committed separately).

## Round N (2026-11-01): search, camera, natural speech, EVA, Wi-Fi Direct helper
| | State | Notes |
|---|---|---|
| N0 diagnose | done | `assistant diagnose`, docs/DIAGNOSIS.md (causes confirmed vs suspected) |
| N1 search | done | explicit search intent, search first (even if local is down), results survive escalation, empty-cloud fallback, per-tool trace |
| N2 camera | done | open the camera / snap / identify / what is this intents; plain "can't fetch the photo yet" |
| N3a replies | done | soft limits at sentence ends, wrap-up line, finish-reason handling, cause of every early stop logged |
| N3b prosody | done | whole-sentence chunks, pauses between chunks, more speech normalisation, voice blends |
| N3c bake-off | partly | Kokoro (6 variants) and Piper (2 voices) rendered, WAVs and numbers in docs/TTS_BAKEOFF.md; Indic Parler and Pocket TTS NOT run (gated, over budget / too new) |
| N4 EVA / wake | partly | identity, wake phrases, ack, `wake-test`, HFP probe built; hands-free modes not implemented (need your hardware tests) |
| N5 Wi-Fi Direct | built with fakes | isolated env, helper, flow, tests; STOPPED before any real connection or the enable write |
| N6 vision | partly | `describe_scene` wired to the fetcher; cloud vision needs `VISION_ALLOW_CLOUD`; local vision benchmark not run (no images in bench/data/images, RAM) |
| N7 docs | done | CONFIG, README, design, latency notes updated |

`.env` (edited with your permission; only these keys, no secret touched or printed): `TOOLS_ENABLED`
(full list of eight tools) and `TTS_SPEED=1.0`.

Decisions: wake phrases handled in the pipeline (always stripped; ignored only if `WAKE_REQUIRED`);
EVA mis-hearings live in the wake list, not the glossary (the glossary skips words under four letters);
chunk gaps are silence inserted by the pipeline; Piper is not wired into the assistant (judge by ear first).

Open for you: listen to docs/TTS_BAKEOFF.md files; `wake-test`; approve (or not) the exact bytes in
docs/WIFI_DIRECT_HELPER.md; put test images in bench/data/images for the vision benchmark.
