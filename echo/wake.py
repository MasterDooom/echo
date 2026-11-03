from __future__ import annotations

import re

# The user pronounces the wake name "AY-koh". Allow common offline-ASR
# spellings, while retaining word boundaries to limit accidental activations.
_WAKE_PATTERNS = (
    re.compile(r"\becho\b", re.IGNORECASE),
    re.compile(r"\b(?:aiko|ayko|eyko|ayco|eyco|ecco)\b", re.IGNORECASE),
    re.compile(r"\b(?:ay|ey)[ -]+ko\b", re.IGNORECASE),
)


def wake_word_detected(text: str) -> bool:
    """Return True for Echo / AY-koh variants, but not arbitrary similar speech."""
    return any(pattern.search(text or "") for pattern in _WAKE_PATTERNS)
