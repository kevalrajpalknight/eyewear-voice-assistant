"""Join two rear clicks into one request when the first was cut off by the session window."""

from dataclasses import dataclass

import numpy as np

from aiglasses.assistant.recorder import cut_off
from aiglasses.services.voice import EndReason, VoiceUtterance


@dataclass(frozen=True)
class Joined:
    utterance: VoiceUtterance
    replaces_previous: bool  # the previous turn's exchange must be dropped from history


class TurnJoiner:
    """A session that ended by timeout, or while speech was still going, may continue in the next
    click. If that click starts within `window_s` of the previous end, the audio is concatenated."""

    def __init__(self, window_s: float) -> None:
        self.window_s = window_s
        self._previous: VoiceUtterance | None = None

    def _was_cut(self, utterance: VoiceUtterance) -> bool:
        return utterance.end_reason is EndReason.TIMEOUT or cut_off(
            utterance.pcm, utterance.sample_rate
        )

    def feed(self, utterance: VoiceUtterance) -> Joined:
        previous = self._previous
        joined = (
            previous is not None
            and previous.sample_rate == utterance.sample_rate
            and 0.0 <= utterance.started_at - previous.ended_at <= self.window_s
        )
        if joined and previous is not None:
            utterance = VoiceUtterance(
                pcm=np.concatenate([previous.pcm, utterance.pcm]),
                sample_rate=utterance.sample_rate,
                started_at=previous.started_at,
                ended_at=utterance.ended_at,
                frame_count=previous.frame_count + utterance.frame_count,
                end_reason=utterance.end_reason,
                start_marker_at=previous.start_marker_at,
                end_marker_at=utterance.end_marker_at,
            )
        # `previous` only stays joinable while it is cut off; a complete request ends the chain.
        self._previous = utterance if self._was_cut(utterance) else None
        return Joined(utterance, joined)
