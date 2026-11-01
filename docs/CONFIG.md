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
| `LLM_MAX_TOKENS` | `1200` | Upper bound on generated tokens per reply |
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
| `ROUTER_MODE` | `rules` | `off` (always main), `rules`, `model` (classifier call, adds latency), `local_first` |
| `ROUTER_MAX_EASY_WORDS` | `8` | Requests up to this many words may go to the fast model |
| `ROUTER_HARD_KEYWORDS` | search, latest, news, weather, price, look, see, compare, explain in detail, ... | Comma list; a hit sends the request to the main model |
| `ROUTER_MODEL_PROVIDER`, `ROUTER_MODEL_MODEL`, `ROUTER_MODEL_API_KEY`, `ROUTER_MODEL_BASE_URL` | inherit `LLM_FAST_*`, then `LLM_*` | The classifier for `ROUTER_MODE=model` |
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
`TTS_ENGINE` (`kokoro` or `piper`; for `piper`, `TTS_VOICE` is a registry model name such as
`piper-en-gb-alba-medium`, pulled with `aiglasses models pull`), `TTS_VARIANT` (`int8` or `fp32`), `TTS_VOICE` (`bf_isabella`),
`TTS_SPEED` (`1.05`; `1.0` is the voice's natural pace), `TTS_SENTENCE_PAUSE_S` (`0.3`) and
`TTS_CLAUSE_PAUSE_S` (`0.12`) (silence between sentences and at commas), `TTS_VOICE` may be a blend
such as `bf_isabella:0.6+bf_emma:0.4`, `TTS_FIRST_CLAUSE_MAX_WORDS` (`6`: fewer words, earlier first sound),
`TTS_MODEL_DIR` (`bench/models`).

## Audio and session
| Variable | Default | Meaning |
|---|---|---|
| `AUDIO_OUTPUT` | `pc` | `pc` or `glasses`. `glasses` is unsupported until pairing and falls back to `pc` with a message. |
| `AUDIO_VOLUME_LIMIT` | `1.0` | Ceiling 0 to 1 applied before the speaker |
| `TURN_JOIN_WINDOW_S` | `4` | A click within this many seconds after a cut-off request is joined to it |

Note on `AUDIO_OUTPUT=pc`: it plays through the Windows default output device. Once the glasses
are paired as an audio device and are the default ("Headphones (AIMB-G2_2324)"), "pc" means the
glasses speaker. Keep `AUDIO_VOLUME_LIMIT` low in your own `.env`; `assistant check` prints the
default device. The glossary file format is one term per line with spoken forms after a single
`|` (`RapDuo SM | rap duo s m, rap do`); `assistant check` explains it and warns about extra pipes.

## Identity and wake phrases
`ASSISTANT_NAME` (`EVA`), `ASSISTANT_NAME_LONG` (`Eyewear Voice Assistant`), `STARTUP_LINE`
(spoken once when ready; empty = silent), `WAKE_MODE` (`button`, `glasses_phrase`,
`hfp_experimental`; only `button` is implemented), `WAKE_REQUIRED` (`false`: a request that arrives
from the rear click needs no wake phrase; `true` ignores speech without one), `WAKE_PHRASES_PATH`
(empty = shipped `wake_phrases.yaml`: names with common mis-hearings, greetings), `WAKE_ACK`
(`Yes?`, said when only the wake phrase was heard) and `FOLLOWUP_WINDOW_S` (`0`, reserved).
A wake phrase at the start of a request is always removed before routing.

## Assistant behaviour
`PERSONA_PATH` (empty: the persona in the package), `ASSISTANT_TIMEZONE` (`Asia/Kolkata`),
`MAX_TURNS_PER_SESSION` (`50`),
`MAX_REPLY_SECONDS` (`60` seconds of speech, soft), and the three fixed lines spoken on
failures: `FALLBACK_LLM_ERROR`, `FALLBACK_NO_SPEECH`, `FALLBACK_TTS_ERROR`.

## Logging and privacy
`LOG_LEVEL` (`INFO`), `LOG_TRANSCRIPTS` (`false`: logs hold lengths and timings only), `SHOW_TRANSCRIPTS` (`true`: print `you:` and `EVA:` lines on screen only),
`AGENT_ENGINE` (`agent`: the LangChain agent; `lanes`: the older fixed lanes), `AGENT_LOCAL_MAX_TOKENS` (`220`), `LOCAL_STEP_TIMEOUT_S` (`20`),
`INPUT_MODE` (`button` or `mic`: with `mic` a microphone listens all the time, the wake phrase starts a
conversation and the assistant sleeps again after `LISTEN_IDLE_SLEEP_S` (`3`) of quiet), `WAKE_LEARNED_PATH` (names learned from your voice by `assistant wake-train`), `MIC_DEVICE` (part of an
input device name; empty = system default), `DUPLEX_BARGE_IN` (`true`: speaking over the assistant stops it),
`BARGE_IN_RATIO` (`2.5`), `VAD_MIN_RMS` (`120`; run `assistant mic-test` to see your levels), `VAD_RATIO` (`3`), `VAD_END_SILENCE_MS` (`800`),
`DEBUG_SAVE_AUDIO` (`false`: input audio is not kept). The log filter also removes the configured
keys and anything shaped like an API key from every log line.

## Memory
`MEMORY_MAX_TURNS` (`10` exchanges; the old name `HISTORY_TURNS` still works), `MEMORY_TTL_S`
(`600` seconds), `MEMORY_MAX_TOKENS` (`1500` tokens for the local context). Whichever limit hits
first applies; expiry is silent and memory lives in RAM only.

## Reply length
`REPLY_TARGET_SENTENCES` (`3`) goes into the persona. `REPLY_SOFT_MAX_SENTENCES` (`8`; old name
`REPLY_MAX_SENTENCES`) is a soft limit: speech never stops mid-sentence, past the limit the reply is
wrapped up with `WRAP_UP_LINE`. `FIRST_CHUNK_MIN_WORDS` (`8`) sets how early speech starts.

## Local model (llama.cpp)
`LOCAL_LLM_BIN_DIR` (`tools/llama.cpp`), `LOCAL_LLM_MODEL` (a name from the registry; empty means no
local model), `LOCAL_LLM_THREADS` (empty = physical cores), `LOCAL_LLM_CTX` (`4096` tokens),
`LOCAL_LLM_PORT` (`8081`, bound to 127.0.0.1), `LOCAL_LLM_BATCH` (`512`), `LOCAL_LLM_MAX_TOKENS` (`120` tokens per local step), `LOCAL_LLM_PRELOAD`
(`true`), `LOCAL_LLM_PROMPT_CACHE` (`true`), `LOCAL_FIRST_TOKEN_TIMEOUT_S` (`6`), `MODELS_DIR`
(`models`), `MODEL_REGISTRY_PATH` (the shipped `models.yaml`), `MODEL_DOWNLOAD_BUDGET_GB` (`8`),
`ESCALATION_FILLER` (empty: a random phrase from `fillers.yaml`, also used when a tool starts;
set text to always say that instead), `SEARCH_CONTEXT_CHARS` (`700`: how much search text the local model reads),
`SEARCH_FALLBACK` (`wikipedia` or `off`: a second source after one retry of the first) and `SEARCH_FALLBACK_URL`. `ROUTER_MODE=local_first` enables the lanes.

## Agent loop and tools
`AGENT_MAX_STEPS` (`6`, at most 10), `AGENT_TIME_BUDGET_S` (`60`), `TOOLS_ENABLED` (comma list;
default only read-only tools), `TOOL_MAX_CALLS_PER_TURN` (`3`), `TOOL_TIMEOUT_S` (`10`),
`ALLOW_RECORDING_TOOLS` (`false`), `CAMERA_OPEN_ASKS_FIRST` (`false`: "open the camera" takes a photo at once; `true` asks first), `PHOTO_MIN_INTERVAL_S` (`10`), `PHOTO_MAX_PER_SESSION` (`10`),
`INTENTS_PATH` (empty = the shipped intents file), `TURN_END_SILENCE_MS` (`300`).

## Search
`SEARCH_PROVIDER` (`ddgs`, no key), `SEARCH_MAX_RESULTS` (`5`), `SEARCH_REGION` (`wt-wt`),
`SEARCH_CACHE_TTL_S` (`300`), `SEARCH_TIMEOUT_S` (`5`).

## Weather
`WEATHER_LOCATION` (empty: the assistant asks which city), `WEATHER_GEOCODING_URL` and
`WEATHER_FORECAST_URL` (a free public forecast service, no key). Tools `get_time` and `get_weather`
are enabled by default together with `search_web`; if your `.env` sets `TOOLS_ENABLED` itself, add
them there.

## Vision
`VISION_PROVIDER` (`off`, `local` or `cloud`; cloud sends the image to your provider),
`VISION_ALLOW_CLOUD` (`false`; cloud vision also needs this), `WIFI_DIRECT_ENABLED` (`false`),
`WIFI_DIRECT_HELPER_PYTHON` (the isolated helper's Python), `VISION_MAX_IMAGE_SIDE` (`768` pixels).
Pictures come over Bluetooth (the AI-photo thumbnail) unless `WIFI_DIRECT_ENABLED` is true.
`VISION_CAPTURE` (`wait` waits for your rear double click, the default; `ai_photo` asks the glasses for the photo, hands-free, using the one frame you approved) and
`VISION_CAPTURE_WAIT_S` (`30`).

## Live calls and reasoning effort
`LIVE_CALL_BUDGET` (`3`) is the lifetime number of real calls `assistant check` may make; reset it
with `assistant check --reset-live-budget`. `LLM_REASONING_EFFORT` and `LLM_FAST_REASONING_EFFORT`
(empty = not sent) are passed to the provider only when set and supported.
