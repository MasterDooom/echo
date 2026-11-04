from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """A parsed intent and its entities.

    The query remains the second field for compatibility with the original
    Command(kind, query) call sites.
    """

    kind: str
    query: str = ""
    artist: str = ""
    mode: str = ""
    target_type: str = "auto"


_WAKE_PREFIX = re.compile(
    r"^(?:(?:hey|okay|ok)\s+)?(?:echo|ecco|aiko|ayko|eyko|ayco|eyco|(?:ay|ey)[ -]+ko)\b[,:;.!?\s]*",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_FILLER_PREFIX = re.compile(
    r"^(?:(?:please|can you|could you|would you|will you|i want you to)\s+)+",
    re.IGNORECASE,
)


def clean_transcript(text: str) -> str:
    text = (text or "").strip().lower()
    text = _WAKE_PREFIX.sub("", text)
    text = _FILLER_PREFIX.sub("", text)
    text = text.replace("’", "'")
    text = re.sub(r"[^\w\s:%+\-']", " ", text, flags=re.UNICODE)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def _clean_target(value: str) -> str:
    value = value.strip(" ,.-")
    value = re.sub(r"^(?:the\s+)?(?:song|track)\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+(?:on|in)\s+spotify$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _split_track_artist(value: str) -> tuple[str, str]:
    match = re.match(r"^(.+?)\s+by\s+(.+)$", value, flags=re.IGNORECASE)
    if not match:
        return _clean_target(value), ""
    return _clean_target(match.group(1)), _clean_target(match.group(2))


def _duration_ms(value: str, unit: str) -> int:
    number = float(value)
    multiplier = 60_000 if unit.startswith("min") else 1_000
    return max(1, int(number * multiplier))


def parse_command(transcript: str) -> Command | None:
    """Parse complete natural-language voice commands into deterministic intents.

    No remote model or cloud service is used. Higher-priority transport
    commands are evaluated before media lookup so e.g. "play the next" is
    NEXT, while "play The Next Episode by Dr. Dre" is a track request.
    """
    text = clean_transcript(transcript)
    if not text:
        return None

    # Read-only queries.
    if re.fullmatch(
        r"(?:what(?:'s| is) playing|what song is this|which song is playing|now playing)",
        text,
    ):
        return Command("now_playing")
    if re.fullmatch(
        r"(?:what(?:'s| is) next|what(?:'s| is) in (?:my )?queue|show (?:my )?queue|read (?:my )?queue)",
        text,
    ):
        return Command("get_queue")

    # Queue additions must win over generic play/media parsing.
    queue_match = re.match(
        r"^(?:add|put|place)\s+(.+?)\s+(?:to|in|into|on)\s+(?:(?:my|the)\s+)?queue$",
        text,
    )
    if queue_match:
        title, artist = _split_track_artist(queue_match.group(1))
        return Command("queue", title, artist=artist, target_type="track")
    queue_match = re.match(r"^(?:queue|add to queue)\s+(.+)$", text)
    if queue_match:
        title, artist = _split_track_artist(queue_match.group(1))
        return Command("queue", title, artist=artist, target_type="track")

    # Seek phrases are checked before "back" / "forward" track controls.
    seek_match = re.match(
        r"^(?:go\s+)?(forward|ahead|fast forward|rewind|back)\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?)$",
        text,
    )
    if seek_match:
        direction = "backward" if seek_match.group(1) in {"rewind", "back"} else "forward"
        amount = _duration_ms(seek_match.group(2), seek_match.group(3))
        return Command("seek", str(amount), mode=direction)

    seek_match = re.match(
        r"^(?:seek|jump)\s+(?:to\s+)?(\d{1,2}):(\d{2})$",
        text,
    )
    if seek_match:
        minutes, seconds = int(seek_match.group(1)), int(seek_match.group(2))
        return Command("seek", str((minutes * 60 + seconds) * 1000), mode="absolute")

    # Shuffle/repeat controls. A named playlist becomes a play-with-mode intent.
    if re.fullmatch(r"(?:turn\s+)?shuffle\s+(?:off|false|no)", text):
        return Command("shuffle", mode="off")
    if re.fullmatch(r"(?:turn\s+)?shuffle\s+(?:on|true|yes)", text) or text == "shuffle":
        return Command("shuffle", mode="on")

    shuffle_match = re.match(r"^shuffle\s+(?:my\s+)?(.+?\s+playlist)$", text)
    if shuffle_match:
        target = re.sub(r"\s+playlist$", "", shuffle_match.group(1)).strip()
        return Command("play", _clean_target(target), mode="shuffle", target_type="playlist")

    repeat_match = re.fullmatch(r"(?:turn\s+)?repeat\s+(off|none|no)", text)
    if repeat_match:
        return Command("repeat", mode="off")
    if re.fullmatch(r"(?:repeat|loop)\s+(?:this\s+)?(?:song|track)|repeat track", text):
        return Command("repeat", mode="track")
    if re.fullmatch(r"(?:repeat|loop)\s+(?:the\s+)?(?:playlist|album|context)", text):
        return Command("repeat", mode="context")
    if re.fullmatch(r"(?:turn\s+)?repeat\s+(?:on|yes)", text):
        return Command("repeat", mode="context")

    # Shuffle the current context or resolve a named playlist.
    if re.fullmatch(r"shuffle (?:this|the|the current|current) (?:playlist|album|queue)", text):
        return Command("shuffle", mode="on")
    shuffle_target = re.match(r"^shuffle\s+(?:my\s+)?(.+)$", text)
    if shuffle_target:
        target = _clean_target(shuffle_target.group(1))
        explicit_playlist = target.endswith(" playlist")
        if explicit_playlist:
            target = re.sub(r"\s+playlist$", "", target).strip()
        return Command(
            "play",
            target,
            mode="shuffle",
            target_type="playlist" if explicit_playlist else "auto",
        )

    # Volume.
    if text in {"mute", "mute volume", "silence"}:
        return Command("volume", "0")
    if text in {"unmute", "unmute volume"}:
        return Command("volume", "50")
    volume_match = re.match(
        r"^(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*(?:percent|%)?$",
        text,
    )
    if volume_match:
        return Command("volume", str(min(100, int(volume_match.group(1)))))
    if text in {"volume up", "turn it up", "louder", "increase volume", "increase the volume", "make it louder"}:
        return Command("volume", "+10")
    if text in {"volume down", "turn it down", "quieter", "decrease volume", "decrease the volume", "make it quieter"}:
        return Command("volume", "-10")

    # Transport controls. Strip a trailing politeness marker only for this
    # control-intent check, so song titles ending in "please" are untouched.
    transport_text = re.sub(r"\s+(?:please|thanks|thank you)$", "", text).strip()
    if transport_text in {
        "next", "next song", "next track", "next one", "the next one", "skip", "skip song", "skip the song", "skip this song",
        "skip this track", "skip it", "go next", "go to next", "go to the next", "go to the next song",
        "go to the next track", "play next", "play the next", "play next song",
        "play the next song", "play next track", "play the next track", "play the next one", "move to the next", "move to next",
    }:
        return Command("next")
    if transport_text in {
        "previous", "previous song", "previous track", "previous one", "the previous one", "prev", "back",
        "go back", "go back one", "go to previous", "go to the previous", "go to the previous one", "go to the previous song",
        "go to the previous track", "last song", "last track", "last one", "play previous",
        "play the previous", "play the previous one", "move to the previous",
    }:
        return Command("previous")
    if transport_text in {
        "pause", "pause music", "pause the music", "pause playback", "pause the playback", "pause the song", "pause this song",
        "stop", "stop music", "stop the music", "stop playback", "stop the playback", "stop the song", "hold on",
    }:
        return Command("pause")
    if transport_text in {
        "resume", "continue", "unpause", "resume music", "resume the music",
        "continue playing", "continue the song", "start playing", "start the music", "carry on", "keep playing",
    }:
        return Command("resume")

    # Explicit playlist phrases.
    playlist_named_match = re.match(
        r"^(?:play|put on|start|listen to)\s+(?:my\s+)?playlist\s+(?:called\s+)?(.+)$",
        text,
    )
    if playlist_named_match:
        return Command("play", _clean_target(playlist_named_match.group(1)), target_type="playlist")

    playlist_match = re.match(
        r"^(?:play|put on|start|listen to)\s+(?:my\s+)?(?:spotify\s+)?(?:playlist\s+)?(.+?)\s+playlist$",
        text,
    )
    if playlist_match:
        return Command("play", _clean_target(playlist_match.group(1)), mode="", target_type="playlist")

    # Generic media request. Keep "The Next Episode" as a song when the
    # complete title/artist is present; only the bare control phrase above
    # is treated as NEXT.
    play_match = re.match(
        r"^(?:play me|play|put on|start|listen to|find me|search for)\s+(.+)$",
        text,
    )
    if play_match:
        query, artist = _split_track_artist(play_match.group(1))
        if query in {"music", "something", "anything"}:
            return Command("resume")
        return Command("play", query, artist=artist, target_type="auto")

    return None
