from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import webbrowser
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from . import config
from .commands import Command

API_BASE = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "user-modify-playback-state user-read-playback-state"


class SpotifyError(RuntimeError):
    pass


def _normal_words(value: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+", value.lower()))


def rank_track(query: str, tracks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Choose the most relevant title/artist match from Spotify's ranked results."""
    if not tracks:
        return None
    wanted = _normal_words(query)

    def score(track: dict[str, Any]) -> float:
        artists = " ".join(a.get("name", "") for a in track.get("artists", []))
        combined = f"{track.get('name', '')} {artists}"
        actual = _normal_words(combined)
        overlap = len(wanted & actual) / max(1, len(wanted))
        ratio = SequenceMatcher(None, query.lower(), combined.lower()).ratio()
        return 0.72 * overlap + 0.28 * ratio

    return max(tracks, key=score)


class _OAuthCallback(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        self.server.oauth_result = {
            "code": params.get("code", [""])[0],
            "state": params.get("state", [""])[0],
            "error": params.get("error", [""])[0],
        }
        body = b"Echo is connected to Spotify. You can close this tab."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


class SpotifyClient:
    def __init__(self) -> None:
        self.client_id = config.spotify_client_id()
        if not self.client_id:
            raise SpotifyError("Set SPOTIFY_CLIENT_ID in .env first.")
        self.redirect_uri = config.spotify_redirect_uri()
        self.token_file = config.app_data_dir() / "spotify-token.json"
        self.token = self._read_token()

    @staticmethod
    def login() -> None:
        client_id = config.spotify_client_id()
        if not client_id:
            raise SpotifyError("Set SPOTIFY_CLIENT_ID in .env first.")
        redirect_uri = config.spotify_redirect_uri()
        parsed = urlparse(redirect_uri)
        if parsed.hostname not in {"127.0.0.1", "::1"} or not parsed.port:
            raise SpotifyError("Use a loopback redirect such as http://127.0.0.1:8888/callback.")

        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(20)
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        auth_url = "https://accounts.spotify.com/authorize?" + urlencode(params)

        server = HTTPServer((parsed.hostname, parsed.port), _OAuthCallback)
        server.timeout = 180
        server.oauth_result = {}
        print("Opening Spotify login in your browser…")
        webbrowser.open(auth_url)
        deadline = time.monotonic() + 180
        try:
            while time.monotonic() < deadline and not server.oauth_result:
                server.handle_request()
        finally:
            server.server_close()

        result = server.oauth_result
        if result.get("state") != state:
            raise SpotifyError("Spotify login timed out or returned an invalid state. Try again.")
        if result.get("error"):
            raise SpotifyError(f"Spotify authorization failed: {result['error']}")
        if not result.get("code"):
            raise SpotifyError("Spotify login timed out. Run the login command again.")

        payload = urlencode({
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }).encode()
        request = Request(TOKEN_URL, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(request, timeout=15) as response:
                token = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise SpotifyError(f"Could not exchange Spotify authorization code: {exc}") from exc

        token_file = config.app_data_dir() / "spotify-token.json"
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
        token_file.write_text(json.dumps(token, indent=2), encoding="utf-8")
        print("Spotify connected. Token stored locally in your Windows AppData folder.")

    def _read_token(self) -> dict[str, Any] | None:
        if not self.token_file.exists():
            return None
        try:
            return json.loads(self.token_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save_token(self) -> None:
        if self.token is None:
            return
        self.token_file.write_text(json.dumps(self.token, indent=2), encoding="utf-8")

    def _refresh(self) -> None:
        if not self.token or not self.token.get("refresh_token"):
            raise SpotifyError("Spotify is not connected. Run: python -m echo --spotify-login")
        payload = urlencode({
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.token["refresh_token"],
        }).encode()
        request = Request(TOKEN_URL, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(request, timeout=15) as response:
                fresh = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError) as exc:
            raise SpotifyError(f"Spotify token refresh failed: {exc}") from exc
        fresh.setdefault("refresh_token", self.token["refresh_token"])
        fresh["expires_at"] = time.time() + int(fresh.get("expires_in", 3600)) - 60
        self.token = fresh
        self._save_token()

    def _access_token(self) -> str:
        if not self.token:
            raise SpotifyError("Spotify is not connected. Run: python -m echo --spotify-login")
        if time.time() >= float(self.token.get("expires_at", 0)):
            self._refresh()
        return str(self.token["access_token"])

    def _request(self, method: str, path: str, *, params=None, body=None, retry=True):
        query = f"?{urlencode(params)}" if params else ""
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            API_BASE + path + query,
            data=data,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            if exc.code == 401 and retry:
                self._refresh()
                return self._request(method, path, params=params, body=body, retry=False)
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code == 403:
                # Spotify's error body can distinguish Premium, scope, quota,
                # and device restrictions. Preserve it instead of guessing.
                try:
                    payload = json.loads(detail)
                    error_payload = payload.get("error", payload)
                    message = str(error_payload.get("message", detail))
                    reason = str(error_payload.get("reason", "")).strip()
                except (TypeError, ValueError, AttributeError):
                    message, reason = detail, ""
                reason_text = f" [{reason}]" if reason else ""
                raise SpotifyError(
                    f"Spotify refused playback{reason_text}: {message}. "
                    "Check the Premium account connected to Echo."
                ) from exc
            if exc.code == 404:
                raise SpotifyError("No controllable Spotify device found. Open Spotify on this PC and start a track once.") from exc
            if exc.code == 429:
                raise SpotifyError("Spotify rate-limited the request. Try again in a moment.") from exc
            raise SpotifyError(f"Spotify API error ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise SpotifyError(f"Could not reach Spotify: {exc.reason}") from exc

    def pause_for_command(self) -> dict[str, Any]:
        """Pause current Spotify playback while Echo listens to the user's command."""
        state = self._request("GET", "/me/player")
        device_id = (state.get("device") or {}).get("id")
        was_playing = bool(state.get("is_playing"))
        if was_playing:
            params = {"device_id": device_id} if device_id else None
            self._request("PUT", "/me/player/pause", params=params)
        return {"was_playing": was_playing, "device_id": device_id}

    def resume_after_command(self, snapshot: dict[str, Any]) -> None:
        """Restore playback only when Echo paused a track that was playing."""
        if not snapshot or not snapshot.get("was_playing"):
            return
        device_id = snapshot.get("device_id")
        params = {"device_id": device_id} if device_id else None
        self._request("PUT", "/me/player/play", params=params)

    def _device(self, preferred_id: str | None = None) -> dict[str, Any] | None:
        response = self._request("GET", "/me/player/devices")
        devices = [d for d in response.get("devices", []) if d.get("id") and not d.get("is_restricted")]
        if not devices:
            return None
        # Prefer the exact device paused at wake time, then the active device.
        # Never select an inactive computer ahead of the active Spotify device.
        preferred = next((d for d in devices if preferred_id and d.get("id") == preferred_id), None)
        active = next((d for d in devices if d.get("is_active")), None)
        computer = next((d for d in devices if str(d.get("type", "")).lower() == "computer"), None)
        return preferred or active or computer or devices[0]

    def search_track(self, query: str) -> dict[str, Any]:
        results = self._request("GET", "/search", params={"q": query, "type": "track", "limit": 5})
        tracks = results.get("tracks", {}).get("items", [])
        track = rank_track(query, tracks)
        if not track:
            raise SpotifyError(f"I couldn't find a Spotify track matching '{query}'.")
        return track

    def _play_track(self, query: str, device_id: str | None = None) -> str:
        track = self.search_track(query)
        device = self._device(device_id) if not device_id else None
        target_id = device_id or (device["id"] if device else None)
        params = {"device_id": target_id} if target_id else None
        self._request("PUT", "/me/player/play", params=params, body={"uris": [track["uri"]]})
        artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
        return f"Playing {track.get('name', 'track')} — {artists}".strip(" —")

    def _playback_action(self, endpoint: str, method: str = "PUT", device_id: str | None = None) -> str:
        device = self._device(device_id) if not device_id else None
        target_id = device_id or (device["id"] if device else None)
        params = {"device_id": target_id} if target_id else None
        self._request(method, endpoint, params=params)
        labels = {"/me/player/next": "Skipped to next track", "/me/player/previous": "Went to previous track", "/me/player/pause": "Paused", "/me/player/play": "Resumed playback"}
        return labels[endpoint]

    def _volume(self, amount: str, device_id: str | None = None) -> str:
        device = self._device(device_id)
        if not device:
            raise SpotifyError("Open Spotify on this PC and start a track once.")
        current = device.get("volume_percent")
        if current is None or not device.get("supports_volume", False):
            raise SpotifyError("Spotify does not expose volume control for this device.")
        if amount == "+10":
            value = min(100, int(current) + 10)
        elif amount == "-10":
            value = max(0, int(current) - 10)
        else:
            value = max(0, min(100, int(amount)))
        target_id = device_id or device["id"]
        self._request("PUT", "/me/player/volume", params={"volume_percent": value, "device_id": target_id})
        return f"Volume {value}%"

    def execute(self, command: Command, *, device_id: str | None = None) -> str:
        if command.kind == "play":
            return self._play_track(command.query, device_id=device_id)
        if command.kind == "next":
            return self._playback_action("/me/player/next", "POST", device_id=device_id)
        if command.kind == "previous":
            return self._playback_action("/me/player/previous", "POST", device_id=device_id)
        if command.kind == "pause":
            return self._playback_action("/me/player/pause", device_id=device_id)
        if command.kind == "resume":
            return self._playback_action("/me/player/play", device_id=device_id)
        if command.kind == "volume":
            return self._volume(command.query, device_id=device_id)
        raise SpotifyError("I don't know that command yet.")
