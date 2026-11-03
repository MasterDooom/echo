from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    kind: str
    query: str = ""


_WAKE_RE = re.compile(
    r"\b(?:(?:hey|okay|ok)\s+)?(?:echo|ecco|aiko|ayko|eyko|ayco|eyco|(?:ay|ey)\s+ko)\b",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")


def clean_transcript(text: str) -> str:
    text = text.lower().strip()
    text = _WAKE_RE.sub(" ", text)
    text = re.sub(r"[,.!?;:]", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def parse_command(transcript: str) -> Command | None:
    """Map short spoken phrases to a small, deterministic command set."""
    text = clean_transcript(transcript)
    if not text:
        return None

    words = set(text.split())

    # Explicit track intent takes priority: titles such as "Next to Me" and
    # "Back to Black" must not be mistaken for playback shortcuts.
    play_match = re.match(r"^(?:please\s+)?(?:play|put on|start)(?:\s+(.*))?$", text)
    if play_match:
        query = play_match.group(1) or ""
        query = re.sub(r"^(?:the\s+)?(?:song|track)\s+", "", query)
        query = re.sub(r"\b(?:please|for me)\b", "", query)
        query = _SPACE_RE.sub(" ", query).strip()
        return Command("play", query) if query else Command("resume")

    if words.intersection({"next", "skip", "forward"}):
        return Command("next")
    if words.intersection({"previous", "prev", "back"}):
        return Command("previous")
    if words.intersection({"pause", "stop"}):
        return Command("pause")
    if words.intersection({"resume", "continue", "unpause"}):
        return Command("resume")
    if "unmute" in words:
        return Command("volume", "50")
    if "mute" in words:
        return Command("volume", "0")
    if words.intersection({"up", "increase", "louder", "higher"}) and words.intersection(
        {"volume", "sound", "audio", "up", "louder", "higher"}
    ):
        return Command("volume", "+10")
    if words.intersection({"down", "decrease", "quieter", "lower", "softer"}) and words.intersection(
        {"volume", "sound", "audio", "down", "quieter", "lower", "softer"}
    ):
        return Command("volume", "-10")

    return None
