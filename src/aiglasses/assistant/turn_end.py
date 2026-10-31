"""Is a request finished, or did the glasses cut the session in the middle of a sentence?

Three signals, strongest first:
1. the session ended by timeout (no end marker): cut off;
2. trailing silence: the last TURN_END_SILENCE_MS are quiet: finished;
3. the transcript ends with sentence punctuation: finished even if the tail was loud.
Only cut-off requests can be joined with the next click, and joining adds no waiting: the first
part is answered at once and replaced if a continuation arrives.
"""

from typing import Any

import numpy as np

from aiglasses.services.voice import EndReason, VoiceUtterance

QUIET_FRACTION = 0.1  # a frame below this fraction of the loudest one counts as silence
FRAME_MS = 20
SENTENCE_END = (".", "?", "!")


def trailing_silence_ms(pcm: "np.ndarray[Any, Any]", sample_rate: int) -> int:
    """How long the end of the audio stays quiet, in milliseconds."""
    if len(pcm) == 0:
        return 0
    samples = np.abs(pcm.astype(np.float32))
    peak = float(samples.max())
    if peak == 0.0:
        return len(pcm) * 1000 // sample_rate
    frame = sample_rate * FRAME_MS // 1000
    quiet = 0
    for end in range(len(samples), 0, -frame):
        chunk = samples[max(end - frame, 0) : end]
        if float(chunk.mean()) > QUIET_FRACTION * peak:
            break
        quiet += 1
    return quiet * FRAME_MS


def is_complete(utterance: VoiceUtterance, silence_ms: int, text: str | None = None) -> bool:
    if utterance.end_reason is EndReason.TIMEOUT:
        return False
    if trailing_silence_ms(utterance.pcm, utterance.sample_rate) >= silence_ms:
        return True
    return bool(text) and text.strip().endswith(SENTENCE_END)  # type: ignore[union-attr]
