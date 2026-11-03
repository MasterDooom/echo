"""Small deterministic command parser; no LLM or network call required."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    NEXT = "next"
    PREVIOUS = "previous"
    PAUSE = "pause"
    RESUME = "resume"
    PLAY = "play"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    MUTE = "mute"
    WHAT_IS_PLAYING = "what_is_playing"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Command:
    action: Action
    query: str = ""


def _clean(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s'&-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_command(transcript: str) -> Command:
    """Parse short phrases such as 'echo next' or 'play Save Your Tears'."""
    text = _clean(transcript)
    text = re.sub(r"^(?:hey\s+)?echo\b[\s,]*", "", text).strip()
    if not text:
        return Command(Action.UNKNOWN)

    if re.fullmatch(r"(?:next|skip)(?: song| track)?", text) or text in {
        "play next", "play next song", "play the next song", "skip to next"
    }:
        return Command(Action.NEXT)
    if re.fullmatch(r"(?:previous|back|go back)(?: song| track)?", text) or text in {
        "play previous", "play previous song", "play the previous song"
    }:
        return Command(Action.PREVIOUS)
    if re.fullmatch(r"(?:pause|stop)(?: the music| music| playback)?", text):
        return Command(Action.PAUSE)
    if re.fullmatch(r"(?:resume|continue|unpause)(?: the music| music| playback)?", text):
        return Command(Action.RESUME)
    if re.fullmatch(r"(?:volume up|louder|increase volume)", text):
        return Command(Action.VOLUME_UP)
    if re.fullmatch(r"(?:volume down|quieter|decrease volume)", text):
        return Command(Action.VOLUME_DOWN)
    if text in {"mute", "mute volume"}:
        return Command(Action.MUTE)
    if text in {"what's playing", "what is playing", "current song", "now playing"}:
        return Command(Action.WHAT_IS_PLAYING)

    match = re.match(r"^(?:play|search for|put on)\s+(.+)$", text)
    if match:
        query = match.group(1).strip()
        return Command(Action.PLAY, query) if query else Command(Action.UNKNOWN)

    return Command(Action.UNKNOWN)
