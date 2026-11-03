from __future__ import annotations

import re

# Echo's owner pronounces the wake name "AY-koh". Accept that spelling as
# well as the conventional English spelling so ASR variations still wake it.
_WAKE_PATTERNS = (
    re.compile(r"\becho\b", re.IGNORECASE),
    re.compile(r"\b(?:aiko|ayko|eyko|ayco|eyco|ecco)\b", re.IGNORECASE),
    re.compile(r"\b(?:ay|ey)\s+ko\b", re.IGNORECASE),
)


def wake_word_detected(text: str) -> bool:
    """Return True for Echo / AY-koh variants, but not arbitrary similar speech."""
    return any(pattern.search(text) for pattern in _WAKE_PATTERNS)
