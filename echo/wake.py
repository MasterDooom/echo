from __future__ import annotations

import re

_WAKE_WORD_RE = re.compile(r"\becho\b", re.IGNORECASE)


def wake_word_detected(text: str) -> bool:
    """Return True when a Vosk transcript contains the standalone wake word."""
    return bool(_WAKE_WORD_RE.search(text))
