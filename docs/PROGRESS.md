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
| M9 verification | see final report | |

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
