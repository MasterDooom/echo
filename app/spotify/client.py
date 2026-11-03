"""Spotify Web API adapter."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import spotipy
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

LOGGER = logging.getLogger(__name__)
SCOPES = "user-modify-playback-state user-read-playback-state user-read-currently-playing"


class SpotifyController:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        cache_dir = Path(__file__).resolve().parents[2] / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPES,
            cache_path=str(cache_dir / "spotify-token.json"),
            open_browser=True,
        )
        self._spotify = spotipy.Spotify(auth_manager=auth, requests_timeout=8, retries=1)

    def _device_id(self) -> str | None:
        devices = self._spotify.devices().get("devices", [])
        if not devices:
            return None
        active = next((device for device in devices if device.get("is_active")), None)
        chosen = active or next(
            (device for device in devices if device.get("type") == "Computer"), None
        ) or devices[0]
        return chosen.get("id") if chosen else None

    def play(self, query: str) -> tuple[str, str]:
        results = self._spotify.search(q=query, type="track", limit=5)
        tracks = results.get("tracks", {}).get("items", [])
        if not tracks:
            raise ValueError(f"I couldn't find {query}")
        track = tracks[0]
        device_id = self._device_id()
        if not device_id:
            raise RuntimeError("Open Spotify on a device first")
        self._spotify.start_playback(device_id=device_id, uris=[track["uri"]])
        artists = ", ".join(artist["name"] for artist in track.get("artists", []))
        return track.get("name", query), artists

    def next(self) -> None:
        device_id = self._device_id()
        self._spotify.next_track(**({"device_id": device_id} if device_id else {}))

    def previous(self) -> None:
        device_id = self._device_id()
        self._spotify.previous_track(**({"device_id": device_id} if device_id else {}))

    def pause(self) -> None:
        device_id = self._device_id()
        self._spotify.pause_playback(**({"device_id": device_id} if device_id else {}))

    def resume(self) -> None:
        device_id = self._device_id()
        self._spotify.start_playback(**({"device_id": device_id} if device_id else {}))

    def change_volume(self, delta: int) -> int:
        playback = self._spotify.current_playback()
        if not playback or not playback.get("device"):
            raise RuntimeError("Spotify isn't playing on an active device")
        device = playback["device"]
        current = device.get("volume_percent")
        if current is None:
            current = 50
        target = max(0, min(100, int(current) + delta))
        self._spotify.volume(target, device_id=device.get("id"))
        return target

    def mute(self) -> None:
        playback = self._spotify.current_playback()
        if not playback or not playback.get("device"):
            raise RuntimeError("Spotify isn't playing on an active device")
        self._spotify.volume(0, device_id=playback["device"].get("id"))

    def currently_playing(self) -> dict[str, Any] | None:
        return self._spotify.currently_playing()

    @staticmethod
    def user_message(error: Exception) -> str:
        if isinstance(error, SpotifyException):
            if error.http_status == 401:
                return "Spotify login expired. Restart Echo to sign in again"
            if error.http_status in {403, 404}:
                return "Open Spotify and start playback once, then try again"
            if error.http_status == 429:
                return "Spotify is rate limiting requests. Try again shortly"
        return str(error)
