"""One place that scrubs secrets from text before it is logged, stored or shown."""

import re
from collections.abc import Sequence

KEY_LIKE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}|\bAIza[0-9A-Za-z_-]{20,}")
MASK = "[redacted]"


def redact(text: str, secrets: Sequence[str] = ()) -> str:
    """Remove known secret values and anything shaped like an API key."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, MASK)
    return KEY_LIKE.sub(MASK, text)
