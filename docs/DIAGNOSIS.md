# Diagnosis of the four live-test bugs (2026-11-01)

Method: `aiglasses assistant diagnose` (offline; real intents, rules, registry and your `.env` tool
list, fake devices) plus a read of the lane code. "CONFIRMED" means reproduced by that trace or by a
test; "SUSPECTED" means consistent with your report but not reproduced here.

## 1. Voice not natural
- CONFIRMED (code): the first spoken chunk was 3 to 6 words and later chunks 4 to 10 words, so most
  sentences reached the speech engine as fragments ("One moment, let me think about" / "that.").
  Each fragment gets its own start and end intonation. Nothing was inserted between chunks.
- CONFIRMED (code): speed 1.05 and a 25 s limit; abbreviations, units and symbols were not read out.
- Fixes in N3: chunks are whole sentences (first chunk: sentence end, or a comma after 8 words), short
  sentences merge, a short silence is put between chunks (0.3 s after a sentence, 0.12 s at a comma),
  speed 1.0, more speech normalisation, persona asks for contractions and commas. The voice bake-off
  (docs/TTS_BAKEOFF.md) is for your ears; I did not change the voice because I cannot hear it.

## 2. Replies feel cut off
- CONFIRMED (test): at the old limit of five sentences speech stopped with no closing words
  (`REPLY_MAX_SENTENCES=5`), and the 25 s audio limit could stop it at a clause boundary.
- CONFIRMED (code): `LLM_MAX_TOKENS=300` (your `.env` had 1500) can end a reply in mid-sentence for a
  reasoning model; the provider's finish reason was never looked at.
- Fixes: soft limits (8 sentences, 60 s) checked only at a sentence end; wrap-up line "Do you want me to
  go on?"; if the provider reports `length` the unfinished tail is dropped and the same offer is made.
  Every early stop now has a logged cause: `provider_length`, `sentence_soft_cap`, `time_soft_cap`,
  `barge_in`, `tts_error`, `llm_error` (field `truncation` in the turn log and the CLI report).

## 3. Web search does not work
Three separate causes, two CONFIRMED:
- CONFIRMED: "search for ..." matched no rule. Unless the question contained "latest", "news", "today"
  and so on, the local model chose what to do and usually answered unaided.
- CONFIRMED (code): for time-sensitive questions the local model was checked BEFORE the search ran.
  With the server down or slow (`local_slow`) the turn went to the cloud without any search at all, and
  when the search ran first its results were thrown away on escalation.
- SUSPECTED (matches "only the filler was spoken", 37 characters): the cloud model returned no text
  after the filler. gpt-5-family models spend the token budget on hidden reasoning; with a small
  `LLM_MAX_TOKENS` nothing is left to say. An empty cloud reply now speaks the error line instead of
  staying silent. Your `.env` already has `LLM_REASONING_EFFORT=minimal` and 1500 tokens.
- Fixes (N1): explicit search intent; search runs first, even when the local model is down; its results
  go to the cloud in the prompt (marked untrusted); the turn report lists every tool and its outcome;
  a blocked or rate-limited search gets a short spoken line.

## 4. "Open the camera" and "identify this object"
- CONFIRMED: no intent matched either phrase, so the local model chose, and it answered
  "I can't do that". Also CONFIRMED: your `.env` `TOOLS_ENABLED` lacked `take_photo` and
  `describe_scene`, so even "take a photo" returned "That is switched off."
- Object recognition cannot work yet for a deeper reason (CONFIRMED): the image source for the glasses
  is a stub that raises "not supported" until the Wi-Fi Direct path exists (N5/N6). The reply now says
  so plainly: "I can't fetch the photo from the glasses yet, so I can't look at it."
- Fixes (N2): intents `open_camera` (explains, then takes the photo, or asks first with
  `CAMERA_OPEN_ASKS_FIRST=true`), snap/capture/click, `identify`, `what is this/that`; near-miss tests
  ("I took a photo yesterday", "how do I open the camera").
- Your `.env` tool list was updated to the full set (see PROGRESS.md).

## Not reproduced
- `local_slow` on every turn: not a bug but Gemma 4 E2B being slower than the 2.5 s limit. Limit now
  6 s, the prompt prefix is primed at start, and Qwen3 1.7B is the configured local model.
- Battery "I can't do that" (fixed earlier): the connection was in dry-run mode, which refuses every write.
