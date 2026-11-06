from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
import unicodedata
import webbrowser
from dataclasses import dataclass
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from . import config
from .commands import Command

log = logging.getLogger(__name__)

API_BASE = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = (
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-read-currently-playing "
    "playlist-read-private "
    "playlist-read-collaborative"
)
REQUIRED_SCOPES = frozenset(SCOPES.split())
PLAYLIST_CACHE_SECONDS = 15 * 60
SEARCH_LIMIT = 10  # Spotify's February 2026 Web API limit.


class SpotifyError(RuntimeError):
    pass


class AmbiguousSpotifyMatch(SpotifyError):
    """A request matches multiple plausible Spotify items."""

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


@dataclass(frozen=True)
class RankedTrack:
    score: float
    item: dict[str, Any]


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _tokens(value: str) -> set[str]:
    return set(_plain(value).split())


def _similarity(left: str, right: str) -> float:
    a, b = _plain(left), _plain(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sequence = SequenceMatcher(None, a, b).ratio()
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if not a_tokens or not b_tokens:
        return sequence
    # Query coverage helps "gym" match a playlist named "Gym Motivation".
    coverage = len(a_tokens & b_tokens) / len(a_tokens)
    precision = len(a_tokens & b_tokens) / len(b_tokens)
    token_score = 0.75 * coverage + 0.25 * precision
    return max(sequence, 0.78 * token_score + 0.22 * sequence)


_VERSION_TERMS = {
    "remix", "live", "acoustic", "karaoke", "cover", "instrumental",
    "slowed", "sped", "speed", "nightcore", "edit", "demo", "rework",
    "bootleg", "radio edit", "extended mix", "8d", "8d audio", "8d tunes",
    "bass boosted", "reverb", "loop", "tribute",
}


def _version_requested(query: str) -> bool:
    normalized = _plain(query)
    return any(term in normalized for term in _VERSION_TERMS)


def rank_tracks(
    query: str,
    tracks: list[dict[str, Any]],
    artist_hint: str = "",
) -> list[RankedTrack]:
    """Rank Spotify results using title, artist, version and result position."""
    if not tracks:
        return []

    ranked: list[RankedTrack] = []
    requested_version = _version_requested(query)
    for index, track in enumerate(tracks):
        title = str(track.get("name") or "")
        artists = " ".join(
            str(artist.get("name") or "") for artist in track.get("artists", [])
        )
        title_score = _similarity(query, title)
        artist_score = _similarity(artist_hint, artists) if artist_hint else 0.0

        # Spotify's search endpoint already ranks results using its relevance
        # system and (where available) listening preferences. Preserve that
        # ordering as a meaningful signal: our old title-only score treated
        # an obscure same-title upload as equal to the canonical recording.
        position_score = 1.0 / (index + 1)
        if artist_hint:
            score = 0.62 * title_score + 0.28 * artist_score + 0.10 * position_score
        else:
            score = 0.72 * title_score + 0.28 * position_score

        album = track.get("album") or {}
        item_version_text = " ".join((
            title,
            str(album.get("name") or ""),
            artists,
        ))
        has_alternate_version = any(
            term in _plain(item_version_text) for term in _VERSION_TERMS
        )
        # Do not penalize an alternate mix when the user explicitly names
        # multiple collaborating artists and the candidate matches them.
        collaboration_requested = (
            artist_hint
            and len(_tokens(artist_hint)) >= 4
            and artist_score >= 0.78
        )
        if not requested_version and has_alternate_version and not collaboration_requested:
            score -= 0.16

        ranked.append(RankedTrack(max(0.0, min(1.0, score)), track))

    return sorted(ranked, key=lambda value: value.score, reverse=True)


def rank_track(
    query: str,
    tracks: list[dict[str, Any]],
    artist_hint: str = "",
) -> dict[str, Any] | None:
    """Backward-compatible helper returning the highest-ranked candidate."""
    ranked = rank_tracks(query, tracks, artist_hint)
    return ranked[0].item if ranked else None


def _track_label(track: dict[str, Any]) -> str:
    title = str(track.get("name") or "Unknown track")
    artists = ", ".join(
        str(artist.get("name") or "") for artist in track.get("artists", [])
    )
    return f"{title} — {artists}".strip(" —")


def _playlist_label(playlist: dict[str, Any]) -> str:
    owner = (playlist.get("owner") or {}).get("display_name") or ""
    name = str(playlist.get("name") or "Untitled playlist")
    return f"{name} by {owner}" if owner else name


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
        self.playlist_cache_file = config.app_data_dir() / "playlists-cache.json"
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
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
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
        request = Request(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                token = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            raise SpotifyError(f"Could not exchange Spotify authorization code: {exc}") from exc

        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
        token_file = config.app_data_dir() / "spotify-token.json"
        token_file.write_text(json.dumps(token, indent=2), encoding="utf-8")
        # A re-login may switch Spotify accounts; never keep a cache tied to
        # the previous account or previous playlist permissions.
        (config.app_data_dir() / "playlists-cache.json").unlink(missing_ok=True)
        print("Spotify connected. Updated permissions stored locally.")

    def _read_token(self) -> dict[str, Any] | None:
        if not self.token_file.exists():
            return None
        try:
            value = json.loads(self.token_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _save_token(self) -> None:
        if self.token is not None:
            self.token_file.write_text(json.dumps(self.token, indent=2), encoding="utf-8")

    def missing_scopes(self) -> set[str]:
        if not self.token:
            return set(REQUIRED_SCOPES)
        granted = set(str(self.token.get("scope", "")).split())
        return set(REQUIRED_SCOPES - granted)

    def require_scopes(self) -> None:
        missing = self.missing_scopes()
        if missing:
            names = ", ".join(sorted(missing))
            raise SpotifyError(
                f"Spotify permissions need updating ({names}). Close Echo and run: "
                "python -m echo --spotify-login"
            )

    def _refresh(self) -> None:
        if not self.token or not self.token.get("refresh_token"):
            raise SpotifyError("Spotify is not connected. Run: python -m echo --spotify-login")
        payload = urlencode({
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.token["refresh_token"],
        }).encode()
        request = Request(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                fresh = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            raise SpotifyError(f"Spotify token refresh failed: {exc}") from exc
        fresh.setdefault("refresh_token", self.token["refresh_token"])
        fresh.setdefault("scope", self.token.get("scope", ""))
        fresh["expires_at"] = time.time() + int(fresh.get("expires_in", 3600)) - 60
        self.token = fresh
        self._save_token()

    def _access_token(self) -> str:
        if not self.token:
            raise SpotifyError("Spotify is not connected. Run: python -m echo --spotify-login")
        if time.time() >= float(self.token.get("expires_at", 0)):
            self._refresh()
        return str(self.token["access_token"])

    @staticmethod
    def _error_detail(status: int, body: bytes) -> str:
        raw = body.decode("utf-8", errors="replace").strip()
        if not raw:
            return f"Spotify returned HTTP {status} with an empty response."
        try:
            payload = json.loads(raw)
            error = payload.get("error", payload)
            if isinstance(error, dict):
                reason = str(error.get("reason") or "").strip()
                message = str(error.get("message") or raw)
                return f"{reason}: {message}" if reason else message
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        return raw[:500]

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> dict[str, Any]:
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
                if not raw:  # Several Spotify playback commands return HTTP 204.
                    return {}
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise SpotifyError(
                        f"Spotify returned an invalid response for {path}: {raw[:160]!r}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise SpotifyError(f"Spotify returned an unexpected response for {path}.")
                return parsed
        except HTTPError as exc:
            detail = self._error_detail(exc.code, exc.read())
            if exc.code == 401 and retry:
                self._refresh()
                return self._request(method, path, params=params, body=body, retry=False)
            if exc.code == 403:
                raise SpotifyError(f"Spotify denied {method} {path}: {detail}") from exc
            if exc.code == 404:
                raise SpotifyError(f"Spotify could not find the playback target: {detail}") from exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After", "a moment")
                raise SpotifyError(f"Spotify rate-limited Echo. Retry after {retry_after} seconds.") from exc
            raise SpotifyError(f"Spotify API error ({exc.code}) on {path}: {detail}") from exc
        except URLError as exc:
            raise SpotifyError(f"Could not reach Spotify: {exc.reason}") from exc

    def pause_for_command(self) -> dict[str, Any]:
        """Pause current playback and return the precise device/state snapshot."""
        state = self._request("GET", "/me/player")
        device_id = (state.get("device") or {}).get("id")
        was_playing = bool(state.get("is_playing"))
        if was_playing:
            params = {"device_id": device_id} if device_id else None
            self._request("PUT", "/me/player/pause", params=params)
        return {"was_playing": was_playing, "device_id": device_id}

    def resume_after_command(self, snapshot: dict[str, Any]) -> None:
        if not snapshot or not snapshot.get("was_playing"):
            return
        device_id = snapshot.get("device_id")
        params = {"device_id": device_id} if device_id else None
        self._request("PUT", "/me/player/play", params=params)

    def _device(self, preferred_id: str | None = None) -> dict[str, Any] | None:
        response = self._request("GET", "/me/player/devices")
        devices = [
            item for item in response.get("devices", [])
            if item.get("id") and not item.get("is_restricted")
        ]
        if not devices:
            return None
        preferred = next(
            (item for item in devices if preferred_id and item.get("id") == preferred_id),
            None,
        )
        active = next((item for item in devices if item.get("is_active")), None)
        computer = next(
            (item for item in devices if str(item.get("type", "")).lower() == "computer"),
            None,
        )
        return preferred or active or computer or devices[0]

    def _target_params(self, device_id: str | None) -> dict[str, str] | None:
        if device_id:
            return {"device_id": device_id}
        device = self._device()
        if not device:
            raise SpotifyError("No active Spotify device. Open Spotify and start playback once.")
        return {"device_id": str(device["id"])}

    def search_tracks(self, query: str, artist: str = "") -> list[dict[str, Any]]:
        query_parts = []
        if query:
            query_parts.append(f"track:{query}")
        if artist:
            query_parts.append(f"artist:{artist}")
        search_query = " ".join(query_parts) or query
        result = self._request(
            "GET",
            "/search",
            params={"q": search_query, "type": "track", "limit": SEARCH_LIMIT},
        )
        return result.get("tracks", {}).get("items", [])

    def search_track(self, query: str, artist: str = "") -> dict[str, Any]:
        tracks = self.search_tracks(query, artist)
        ranked = rank_tracks(query, tracks, artist)
        if not ranked or ranked[0].score < 0.50:
            raise SpotifyError(f"I couldn't find a confident Spotify match for '{query}'. Try adding the artist.")
        best = ranked[0]
        if len(ranked) > 1 and ranked[1].score >= best.score - 0.025:
            first, second = ranked[0].item, ranked[1].item
            first_artist = ", ".join(x.get("name", "") for x in first.get("artists", []))
            second_artist = ", ".join(x.get("name", "") for x in second.get("artists", []))
            if _plain(first_artist) != _plain(second_artist):
                choices = [_track_label(first), _track_label(second)]
                raise AmbiguousSpotifyMatch(
                    "I found multiple close matches. Please say the song title and artist: "
                    + " or ".join(choices),
                    choices,
                )
        return best.item

    def _read_cached_playlists(self) -> list[dict[str, Any]] | None:
        try:
            data = json.loads(self.playlist_cache_file.read_text(encoding="utf-8"))
            age = time.time() - float(data.get("fetched_at", 0))
            playlists = data.get("playlists")
            if age <= PLAYLIST_CACHE_SECONDS and isinstance(playlists, list):
                return playlists
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return None

    def list_playlists(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        self.require_scopes()
        if not refresh:
            cached = self._read_cached_playlists()
            if cached is not None:
                return cached

        playlists: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._request(
                "GET", "/me/playlists", params={"limit": 50, "offset": offset}
            )
            items = page.get("items") or []
            for item in items:
                if not item or not item.get("id") or not item.get("uri"):
                    continue
                # Cache only the metadata needed for local name resolution.
                playlists.append({
                    "id": item["id"],
                    "uri": item["uri"],
                    "name": item.get("name") or "",
                    "description": item.get("description") or "",
                    "owner": {
                        "display_name": (item.get("owner") or {}).get("display_name") or ""
                    },
                    "public": item.get("public"),
                    "collaborative": bool(item.get("collaborative")),
                })
            next_page = page.get("next")
            if not next_page or not items:
                break
            offset += len(items)
            if offset >= 100_000:
                log.warning("Stopped playlist pagination at Spotify's offset limit")
                break

        try:
            self.playlist_cache_file.write_text(
                json.dumps({"fetched_at": time.time(), "playlists": playlists}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            log.exception("Could not write playlist cache")
        return playlists

    def _playlist_candidates(self, query: str) -> list[tuple[float, dict[str, Any]]]:
        query_normalized = _plain(query)
        if not query_normalized:
            return []
        candidates = []
        for playlist in self.list_playlists():
            name = str(playlist.get("name") or "")
            score = _similarity(query, name)
            description = str(playlist.get("description") or "")
            # Keep the name dominant; description is only a weak tie-breaker.
            if description:
                score = 0.92 * score + 0.08 * _similarity(query, description)
            candidates.append((score, playlist))
        return sorted(candidates, key=lambda pair: pair[0], reverse=True)

    def find_playlist(self, query: str) -> dict[str, Any] | None:
        candidates = self._playlist_candidates(query)
        if not candidates:
            return None
        best_score, best = candidates[0]
        if best_score < 0.67:
            return None
        if len(candidates) > 1:
            second_score, second = candidates[1]
            if second_score >= 0.82 and second_score >= best_score - 0.055:
                choices = [str(best.get("name", "")), str(second.get("name", ""))]
                raise AmbiguousSpotifyMatch(
                    "I found multiple playlists: " + " or ".join(choices) + ". Say the exact playlist name.",
                    choices,
                )
        return best

    def _play_track(self, query: str, artist: str = "", device_id: str | None = None) -> str:
        track = self.search_track(query, artist)
        params = self._target_params(device_id)
        target_device_id = (params or {}).get("device_id")
        label = _track_label(track)
        track_uri = str(track.get("uri") or "")
        album_uri = str((track.get("album") or {}).get("uri") or "")

        # Spotify desktop clients have a current Web API regression: a bare
        # {"uris": [track_uri]} request can return 204 yet leave the player
        # empty. Starting the album context at the requested track works around
        # it while keeping the exact selected track as the first item.
        if album_uri and track_uri:
            play_body = {
                "context_uri": album_uri,
                "offset": {"uri": track_uri},
            }
            play_mode = "album_context"
        else:
            play_body = {"uris": [track_uri]}
            play_mode = "track_uri_fallback"

        log.info(
            "Starting Spotify track=%r uri=%r album_uri=%r mode=%s device_id=%r",
            label,
            track_uri,
            album_uri or None,
            play_mode,
            target_device_id,
        )
        self._request("PUT", "/me/player/play", params=params, body=play_body)

        # Spotify's 204 response only acknowledges the command. Confirm the
        # player actually switched to the requested track before reporting success.
        requested_id = str(track.get("id") or "")
        requested_uri = track_uri
        last_state: dict[str, Any] = {}
        for attempt in range(6):
            time.sleep(0.5)
            last_state = self.get_playback_state()
            item = last_state.get("item") or {}
            actual_id = str(item.get("id") or "")
            actual_uri = str(item.get("uri") or "")
            linked_from = item.get("linked_from") or {}
            same_track = (
                bool(requested_uri and actual_uri == requested_uri)
                or bool(requested_id and actual_id == requested_id)
                or bool(requested_uri and str(linked_from.get("uri") or "") == requested_uri)
                or bool(requested_id and str(linked_from.get("id") or "") == requested_id)
            )
            log.info(
                "Spotify playback verification attempt=%d is_playing=%r item=%r uri=%r device=%r",
                attempt + 1,
                last_state.get("is_playing"),
                item.get("name"),
                actual_uri,
                (last_state.get("device") or {}).get("name"),
            )
            if last_state.get("is_playing") is True and same_track:
                return f"Playing {label}"

        item = last_state.get("item") or {}
        current_label = _track_label(item) if item else "no current track"
        device_name = (last_state.get("device") or {}).get("name") or "no active device"
        raise SpotifyError(
            f"Spotify accepted the request for {label}, but playback was not confirmed. "
            f"Player state: {current_label}; device: {device_name}; "
            f"is_playing={last_state.get('is_playing')!r}. "
            "The Spotify desktop app may be rejecting the track-only playback request."
        )

    def _play_playlist(
        self,
        playlist: dict[str, Any],
        device_id: str | None = None,
        *,
        shuffle: bool = False,
    ) -> str:
        params = self._target_params(device_id)
        if shuffle:
            self._request("PUT", "/me/player/shuffle", params={**(params or {}), "state": "true"})
        self._request(
            "PUT",
            "/me/player/play",
            params=params,
            body={"context_uri": playlist["uri"]},
        )
        return f"Playing playlist {playlist.get('name', 'playlist')}" + (" on shuffle" if shuffle else "")

    def _play_media(self, command: Command, device_id: str | None = None) -> str:
        if command.target_type == "playlist":
            playlist = self.find_playlist(command.query)
            if not playlist:
                raise SpotifyError(f"I couldn't find a playlist named '{command.query}'.")
            return self._play_playlist(playlist, device_id, shuffle=command.mode == "shuffle")

        if command.target_type == "auto" and not command.artist:
            playlist_query = re.sub(r"^my\s+", "", command.query, flags=re.IGNORECASE)
            try:
                playlist = self.find_playlist(playlist_query)
            except AmbiguousSpotifyMatch:
                # A genuine ambiguity should be clarified, not silently treated as a song.
                raise
            except SpotifyError as exc:
                # A failed playlist lookup must never block ordinary track search.
                # Explicit playlist requests above still surface their permission/API errors.
                log.warning("Personal playlist lookup unavailable; falling back to track search: %s", exc)
                playlist = None
            # A strong personal-playlist name match wins over the public
            # catalogue; otherwise interpret the request as a track.
            if playlist and _similarity(playlist_query, str(playlist.get("name", ""))) >= 0.78:
                return self._play_playlist(playlist, device_id, shuffle=command.mode == "shuffle")

        return self._play_track(command.query, command.artist, device_id)

    def _queue_track(self, query: str, artist: str = "", device_id: str | None = None) -> str:
        track = self.search_track(query, artist)
        params = self._target_params(device_id) or {}
        params["uri"] = str(track["uri"])
        self._request("POST", "/me/player/queue", params=params)
        return f"Added to queue: {_track_label(track)}"

    def get_playback_state(self) -> dict[str, Any]:
        return self._request("GET", "/me/player")

    def _now_playing(self) -> str:
        state = self.get_playback_state()
        item = state.get("item")
        if not item:
            return "Spotify isn't currently playing a track."
        return f"Now playing {_track_label(item)}"

    def _get_queue(self) -> str:
        result = self._request("GET", "/me/player/queue")
        current = result.get("currently_playing")
        upcoming = result.get("queue") or []
        lines = []
        if current:
            lines.append(f"Now: {_track_label(current)}")
        if upcoming:
            lines.extend(
                f"{index}. {_track_label(item)}"
                for index, item in enumerate(upcoming[:5], start=1)
                if item
            )
            if len(upcoming) > 5:
                lines.append(f"And {len(upcoming) - 5} more")
        if not lines:
            return "Your Spotify queue is empty."
        return "Queue: " + " | ".join(lines)

    def _playback_action(
        self,
        endpoint: str,
        method: str = "PUT",
        device_id: str | None = None,
    ) -> str:
        params = self._target_params(device_id)
        self._request(method, endpoint, params=params)
        labels = {
            "/me/player/next": "Skipped to next track",
            "/me/player/previous": "Went to previous track",
            "/me/player/pause": "Paused playback",
            "/me/player/play": "Resumed playback",
        }
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
        target_id = device_id or str(device["id"])
        self._request(
            "PUT",
            "/me/player/volume",
            params={"volume_percent": value, "device_id": target_id},
        )
        return f"Volume {value}%"

    def _shuffle(self, mode: str, device_id: str | None = None) -> str:
        params = self._target_params(device_id) or {}
        enabled = mode == "on"
        self._request(
            "PUT",
            "/me/player/shuffle",
            params={**params, "state": "true" if enabled else "false"},
        )
        return "Shuffle on" if enabled else "Shuffle off"

    def _repeat(self, mode: str, device_id: str | None = None) -> str:
        if mode not in {"off", "track", "context"}:
            raise SpotifyError("Repeat mode must be off, track or context.")
        params = self._target_params(device_id) or {}
        self._request("PUT", "/me/player/repeat", params={**params, "state": mode})
        labels = {"off": "Repeat off", "track": "Repeating this track", "context": "Repeating this playlist"}
        return labels[mode]

    def _seek(self, amount_ms: int, mode: str, device_id: str | None = None) -> str:
        state = self.get_playback_state()
        item = state.get("item")
        if not item:
            raise SpotifyError("There is no active Spotify track to seek.")
        duration = int(item.get("duration_ms") or 0)
        progress = int(state.get("progress_ms") or 0)
        if mode == "forward":
            position = progress + amount_ms
        elif mode == "backward":
            position = progress - amount_ms
        else:
            position = amount_ms
        if duration:
            position = min(max(0, position), max(0, duration - 1))
        else:
            position = max(0, position)
        params = self._target_params(device_id) or {}
        self._request("PUT", "/me/player/seek", params={**params, "position_ms": position})
        return f"Moved to {position // 60000}:{(position // 1000) % 60:02d}"

    def execute(self, command: Command, *, device_id: str | None = None) -> str:
        if command.kind == "play":
            return self._play_media(command, device_id)
        if command.kind == "queue":
            return self._queue_track(command.query, command.artist, device_id)
        if command.kind == "next":
            return self._playback_action("/me/player/next", "POST", device_id)
        if command.kind == "previous":
            return self._playback_action("/me/player/previous", "POST", device_id)
        if command.kind == "pause":
            return self._playback_action("/me/player/pause", device_id=device_id)
        if command.kind == "resume":
            return self._playback_action("/me/player/play", device_id=device_id)
        if command.kind == "volume":
            return self._volume(command.query, device_id)
        if command.kind == "shuffle":
            return self._shuffle(command.mode or "on", device_id)
        if command.kind == "repeat":
            return self._repeat(command.mode or "context", device_id)
        if command.kind == "seek":
            return self._seek(int(command.query), command.mode, device_id)
        if command.kind == "now_playing":
            return self._now_playing()
        if command.kind == "get_queue":
            return self._get_queue()
        raise SpotifyError(f"Unsupported Echo command: {command.kind}")
