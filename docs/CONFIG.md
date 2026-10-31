# Configuration reference

All assistant settings live in `src/aiglasses/settings.py` (`AssistantSettings`). Nothing else in
the code holds a model name, voice, speed, URL or tuning number; a test (`test_forbidden_literals`)
fails if one appears. Precedence, highest first:

1. real environment variables
2. `.env` in the repository root (gitignored; copy `.env.example`)
3. `config.toml` (gitignored; non-secret values, the glasses address is written there by `scan`)
4. the defaults in `settings.py`

An empty value (`KEY=`) means "not set". Secrets are held as `SecretStr` and show only as
`set` / `NOT set` in `aiglasses assistant check`. Run `uv run aiglasses assistant check` after every
change. The command-line flags of `assistant run` and `assistant echo` override the settings for
one run.

## LLM, main (the strong model)
| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai`, `anthropic`, `google`, `openrouter`, `openai_compatible` |
| `LLM_MODEL` | none | Exact model id from your provider. No default: `check` fails without it. |
| `LLM_API_KEY` | none | Provider key. Environment or `.env` only. |
| `LLM_BASE_URL` | provider's own | Needed for `openai_compatible`; overrides the built-in URL (OpenRouter's is built in). |
| `LLM_TEMPERATURE` | `0.6` | 0 to 2 |
| `LLM_MAX_TOKENS` | `300` | Upper bound on generated tokens per reply |
| `LLM_TIMEOUT_S` | `30` | Whole-request time limit, seconds |
| `LLM_MAX_RETRIES` | `2` | Retries, only before the first token arrives |

## LLM, fast (optional)
`LLM_FAST_PROVIDER`, `LLM_FAST_MODEL`, `LLM_FAST_API_KEY`, `LLM_FAST_BASE_URL`,
`LLM_FAST_TEMPERATURE`, `LLM_FAST_MAX_TOKENS`, `LLM_FAST_TIMEOUT_S`, `LLM_FAST_MAX_RETRIES`.
Every unset field inherits the main `LLM_*` value, so only `LLM_API_KEY` is mandatory. The fast
model exists only when `LLM_FAST_MODEL` is set.

## Router
| Variable | Default | Meaning |
|---|---|---|
| `ROUTER_MODE` | `rules` | `off` (always main), `rules`, `model` (classifier call, adds latency) |
| `ROUTER_MAX_EASY_WORDS` | `8` | Requests up to this many words may go to the fast model |
| `ROUTER_HARD_KEYWORDS` | search, latest, news, weather, price, look, see, compare, explain in detail, ... | Comma list; a hit sends the request to the main model |
| `ROUTER_MODEL_PROVIDER` / `_MODEL` / `_API_KEY` / `_BASE_URL` | inherit `LLM_FAST_*`, then `LLM_*` | The classifier for `ROUTER_MODE=model` |
| `ROUTER_TIMEOUT_S` | `3` | Classifier time limit, seconds |
| `ROUTER_FALLBACK` | `strong` | `strong` or `fast`: where to go if the classifier fails |

## Speech to text
| Variable | Default | Meaning |
|---|---|---|
| `STT_ENGINE` | `moonshine` | `moonshine`, `parakeet`, `faster-whisper` |
| `STT_MODEL` | engine default | moonshine: `tiny`, `base`, `small-v2`, `medium-v2`; faster-whisper: `base.en`, `small.en`, `small`; parakeet: ignored |
| `STT_COMPUTE_TYPE` | `int8` | faster-whisper compute type |
| `STT_LANGUAGE` | `en` | Only English is implemented |
| `STT_THREADS` | `4` | CPU threads |
| `STT_MODEL_DIR` | `bench/models` | Where the speech-to-text models are |
| `STT_HINTS_ENABLED` | `false` | Give glossary terms to engines that accept hints (faster-whisper only) |
| `GLOSSARY_ENABLED` | `false` | Correct names after recognition |
| `GLOSSARY_PATH` | `bench/data/glossary.txt` | One term per line, private |
| `GLOSSARY_THRESHOLD` | `88` | Fuzzy match 0 to 100; higher is more careful |

## Text to speech
`TTS_ENGINE` (`kokoro`), `TTS_VARIANT` (`int8` or `fp32`), `TTS_VOICE` (`bf_isabella`),
`TTS_SPEED` (`1.05`), `TTS_FIRST_CLAUSE_MAX_WORDS` (`6`: fewer words, earlier first sound),
`TTS_MODEL_DIR` (`bench/models`).

## Audio and session
| Variable | Default | Meaning |
|---|---|---|
| `AUDIO_OUTPUT` | `pc` | `pc` or `glasses`. `glasses` is unsupported until pairing and falls back to `pc` with a message. |
| `AUDIO_VOLUME_LIMIT` | `1.0` | Ceiling 0 to 1 applied before the speaker |
| `TURN_JOIN_WINDOW_S` | `4` | A click within this many seconds after a cut-off request is joined to it |

## Assistant behaviour
`PERSONA_PATH` (empty: the persona in the package), `ASSISTANT_TIMEZONE` (`Asia/Kolkata`),
`HISTORY_TURNS` (`6` earlier exchanges sent to the model), `MAX_TURNS_PER_SESSION` (`50`),
`MAX_REPLY_SECONDS` (`25` seconds of speech at most), and the three fixed lines spoken on
failures: `FALLBACK_LLM_ERROR`, `FALLBACK_NO_SPEECH`, `FALLBACK_TTS_ERROR`.

## Logging and privacy
`LOG_LEVEL` (`INFO`), `LOG_TRANSCRIPTS` (`false`: logs hold lengths and timings only),
`DEBUG_SAVE_AUDIO` (`false`: input audio is not kept). The log filter also removes the configured
keys and anything shaped like an API key from every log line.
