import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call

from echo.commands import Command
from echo.spotify import (
    REQUIRED_SCOPES,
    SpotifyClient,
    rank_tracks,
)


class SpotifyFeatureTests(unittest.TestCase):
    def make_client(self):
        client = object.__new__(SpotifyClient)
        client.token = {"scope": " ".join(sorted(REQUIRED_SCOPES))}
        client._request = Mock(return_value={})
        client._target_params = Mock(return_value={"device_id": "device-123"})
        client.playlist_cache_file = Path(tempfile.gettempdir()) / "echo-test-playlists-cache.json"
        return client

    def test_play_track_reports_success_only_after_player_confirms_requested_track(self):
        from unittest.mock import patch

        client = self.make_client()
        track = {
            "id": "weeknd-id",
            "uri": "spotify:track:weeknd-id",
            "name": "Blinding Lights",
            "artists": [{"name": "The Weeknd"}],
            "album": {"uri": "spotify:album:after-hours"},
        }
        client.search_track = Mock(return_value=track)
        client.get_playback_state = Mock(return_value={
            "is_playing": True,
            "item": {"id": "weeknd-id", "uri": "spotify:track:weeknd-id"},
            "device": {"name": "Desktop"},
        })

        with patch("echo.spotify.time.sleep"):
            result = client._play_track("blinding lights", "the weeknd", "device-123")

        self.assertEqual(result, "Playing Blinding Lights — The Weeknd")
        client._request.assert_called_once_with(
            "PUT",
            "/me/player/play",
            params={"device_id": "device-123"},
            body={
                "context_uri": "spotify:album:after-hours",
                "offset": {"uri": "spotify:track:weeknd-id"},
            },
        )

    def test_play_track_does_not_claim_success_when_player_remains_idle(self):
        from unittest.mock import patch
        from echo.spotify import SpotifyError

        client = self.make_client()
        client.search_track = Mock(return_value={
            "id": "weeknd-id",
            "uri": "spotify:track:weeknd-id",
            "name": "Blinding Lights",
            "artists": [{"name": "The Weeknd"}],
        })
        client.get_playback_state = Mock(return_value={
            "is_playing": False,
            "item": None,
            "device": {"name": "Desktop"},
        })

        with patch("echo.spotify.time.sleep"):
            with self.assertRaisesRegex(SpotifyError, "playback was not confirmed"):
                client._play_track("blinding lights", "the weeknd", "device-123")

        self.assertEqual(client.get_playback_state.call_count, 6)

    def test_queue_track_uses_official_queue_endpoint_and_active_device(self):
        client = self.make_client()
        client.search_track = Mock(return_value={
            "name": "Starboy",
            "artists": [{"name": "The Weeknd"}],
            "uri": "spotify:track:starboy",
        })

        result = client.execute(
            Command("queue", "starboy", target_type="track"),
            device_id="device-123",
        )

        self.assertEqual(result, "Added to queue: Starboy — The Weeknd")
        client._request.assert_called_once_with(
            "POST",
            "/me/player/queue",
            params={"device_id": "device-123", "uri": "spotify:track:starboy"},
        )

    def test_queue_read_handles_empty_queue(self):
        client = self.make_client()
        client._request.return_value = {"currently_playing": None, "queue": []}
        self.assertEqual(client.execute(Command("get_queue")), "Your Spotify queue is empty.")
        client._request.assert_called_once_with("GET", "/me/player/queue")

    def test_queue_read_formats_current_and_upcoming_items(self):
        client = self.make_client()
        client._request.return_value = {
            "currently_playing": {
                "name": "Current Song",
                "artists": [{"name": "Artist A"}],
            },
            "queue": [
                {"name": "Next Song", "artists": [{"name": "Artist B"}]},
            ],
        }
        result = client.execute(Command("get_queue"))
        self.assertIn("Now: Current Song — Artist A", result)
        self.assertIn("1. Next Song — Artist B", result)

    def test_playlist_pagination_and_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.make_client()
            client.playlist_cache_file = Path(folder) / "playlists.json"
            first = [{"id": f"id-{i}", "uri": f"spotify:playlist:{i}", "name": f"Mix {i}"} for i in range(50)]
            second = [{
                "id": "id-final",
                "uri": "spotify:playlist:final",
                "name": "Workout",
                "owner": {"display_name": "Me"},
                "collaborative": True,
            }]
            client._request.side_effect = [
                {"items": first, "next": "https://api.spotify.com/v1/me/playlists?offset=50"},
                {"items": second, "next": None},
            ]

            playlists = client.list_playlists(refresh=True)

            self.assertEqual(len(playlists), 51)
            self.assertEqual(playlists[-1]["name"], "Workout")
            self.assertEqual(client._request.call_args_list, [
                call("GET", "/me/playlists", params={"limit": 50, "offset": 0}),
                call("GET", "/me/playlists", params={"limit": 50, "offset": 50}),
            ])
            cached = json.loads(client.playlist_cache_file.read_text(encoding="utf-8"))
            self.assertEqual(len(cached["playlists"]), 51)

    def test_playlist_cache_used_until_expired(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.make_client()
            client.playlist_cache_file = Path(folder) / "playlists.json"
            client.playlist_cache_file.write_text(
                json.dumps({"fetched_at": __import__("time").time(), "playlists": [
                    {"id": "id1", "uri": "spotify:playlist:id1", "name": "Workout"}
                ]}),
                encoding="utf-8",
            )

            playlists = client.list_playlists()

            self.assertEqual(playlists[0]["name"], "Workout")
            client._request.assert_not_called()

    def test_playlist_match_uses_personal_playlist_name(self):
        client = self.make_client()
        client.list_playlists = Mock(return_value=[
            {"id": "p1", "uri": "spotify:playlist:p1", "name": "Workout Mix"},
            {"id": "p2", "uri": "spotify:playlist:p2", "name": "Chill"},
        ])

        result = client.find_playlist("workout")

        self.assertEqual(result["id"], "p1")

    def test_short_playlist_name_prefers_a_strong_personal_playlist_match(self):
        client = self.make_client()
        playlist = {"id": "p1", "uri": "spotify:playlist:p1", "name": "Workout Mix"}
        client.find_playlist = Mock(return_value=playlist)
        client._play_playlist = Mock(return_value="Playing playlist Workout Mix")

        result = client._play_media(Command("play", "workout", target_type="auto"))

        self.assertEqual(result, "Playing playlist Workout Mix")
        client._play_playlist.assert_called_once_with(playlist, None, shuffle=False)
        client._request.assert_not_called()

    def test_track_play_falls_back_when_playlist_access_is_unavailable(self):
        from echo.spotify import SpotifyError

        client = self.make_client()
        client.find_playlist = Mock(side_effect=SpotifyError("missing playlist scope"))
        client._play_track = Mock(return_value="Playing Blinding Lights — The Weeknd")

        result = client._play_media(
            Command("play", "blinding lights", target_type="auto")
        )

        self.assertEqual(result, "Playing Blinding Lights — The Weeknd")
        client._play_track.assert_called_once_with("blinding lights", "", None)

    def test_explicit_playlist_request_does_not_silently_fall_back_to_track(self):
        from echo.spotify import SpotifyError

        client = self.make_client()
        client.find_playlist = Mock(side_effect=SpotifyError("missing playlist scope"))
        client._play_track = Mock()

        with self.assertRaisesRegex(SpotifyError, "missing playlist scope"):
            client._play_media(
                Command("play", "workout", target_type="playlist")
            )
        client._play_track.assert_not_called()

    def test_play_playlist_uses_context_uri(self):
        client = self.make_client()
        playlist = {
            "id": "p1",
            "uri": "spotify:playlist:p1",
            "name": "Workout",
        }

        result = client._play_playlist(playlist, "device-123", shuffle=True)

        self.assertEqual(result, "Playing playlist Workout on shuffle")
        self.assertEqual(client._request.call_args_list, [
            call("PUT", "/me/player/shuffle", params={"device_id": "device-123", "state": "true"}),
            call("PUT", "/me/player/play", params={"device_id": "device-123"}, body={"context_uri": "spotify:playlist:p1"}),
        ])

    def test_shuffle_and_repeat_use_official_endpoints(self):
        client = self.make_client()

        self.assertEqual(client.execute(Command("shuffle", mode="off"), device_id="device-123"), "Shuffle off")
        self.assertEqual(client.execute(Command("repeat", mode="track"), device_id="device-123"), "Repeating this track")

        self.assertEqual(client._request.call_args_list, [
            call("PUT", "/me/player/shuffle", params={"device_id": "device-123", "state": "false"}),
            call("PUT", "/me/player/repeat", params={"device_id": "device-123", "state": "track"}),
        ])

    def test_seek_relative_and_clamped_to_track_length(self):
        client = self.make_client()
        client._request.side_effect = [
            {"progress_ms": 59000, "item": {"duration_ms": 60000}},
            {},
        ]

        result = client.execute(
            Command("seek", "30000", mode="forward"),
            device_id="device-123",
        )

        self.assertEqual(result, "Moved to 0:59")
        self.assertEqual(client._request.call_args_list, [
            call("GET", "/me/player"),
            call("PUT", "/me/player/seek", params={"device_id": "device-123", "position_ms": 59999}),
        ])

    def test_scope_check_reports_permissions_to_reauthorize(self):
        client = self.make_client()
        client.token = {"scope": "user-read-playback-state"}
        missing = client.missing_scopes()
        self.assertIn("playlist-read-private", missing)
        self.assertIn("user-modify-playback-state", missing)
        with self.assertRaisesRegex(RuntimeError, "spotify-login"):
            client.require_scopes()

    def test_search_uses_title_artist_filters_and_spotify_limit(self):
        client = self.make_client()
        client._request.return_value = {"tracks": {"items": []}}

        client.search_tracks("blinding lights", "the weeknd")

        client._request.assert_called_once_with(
            "GET",
            "/search",
            params={
                "q": "track:blinding lights artist:the weeknd",
                "type": "track",
                "limit": 10,
            },
        )

    def test_empty_http_204_response_is_a_successful_empty_object(self):
        from unittest.mock import MagicMock, patch

        client = self.make_client()
        client._access_token = Mock(return_value="access-token")
        response = MagicMock()
        response.read.return_value = b""
        response.__enter__.return_value = response

        with patch("echo.spotify.urlopen", return_value=response):
            result = SpotifyClient._request(client, "PUT", "/me/player/next")

        self.assertEqual(result, {})

    def test_non_json_success_response_has_a_useful_error(self):
        from unittest.mock import MagicMock, patch
        from echo.spotify import SpotifyError

        client = self.make_client()
        client._access_token = Mock(return_value="access-token")
        response = MagicMock()
        response.read.return_value = b"not-json"
        response.__enter__.return_value = response

        with patch("echo.spotify.urlopen", return_value=response):
            with self.assertRaisesRegex(SpotifyError, "invalid response"):
                SpotifyClient._request(client, "GET", "/me/player")

    def test_ranker_prefers_original_when_no_version_requested(self):
        tracks = [
            {"name": "Perfect (Live)", "artists": [{"name": "Ed Sheeran"}], "uri": "live"},
            {"name": "Perfect", "artists": [{"name": "Ed Sheeran"}], "uri": "original"},
        ]
        ranked = rank_tracks("Perfect", tracks, "Ed Sheeran")
        self.assertEqual(ranked[0].item["uri"], "original")

    def test_ranker_prefers_requested_acoustic_version(self):
        tracks = [
            {"name": "Perfect", "artists": [{"name": "Ed Sheeran"}], "uri": "original"},
            {"name": "Perfect (Acoustic)", "artists": [{"name": "Ed Sheeran"}], "uri": "acoustic"},
        ]
        ranked = rank_tracks("Perfect Acoustic", tracks, "Ed Sheeran")
        self.assertEqual(ranked[0].item["uri"], "acoustic")


if __name__ == "__main__":
    unittest.main()
