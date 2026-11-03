import unittest
from unittest.mock import Mock, call

from echo.spotify import SpotifyClient


class SpotifyDuckTests(unittest.TestCase):
    def make_client(self):
        client = object.__new__(SpotifyClient)
        client._request = Mock()
        return client

    def test_pause_for_command_pauses_only_when_playing(self):
        client = self.make_client()
        client._request.side_effect = [
            {"is_playing": True, "device": {"id": "device-123"}},
            {},
        ]

        snapshot = client.pause_for_command()

        self.assertEqual(snapshot, {"was_playing": True, "device_id": "device-123"})
        self.assertEqual(
            client._request.call_args_list,
            [
                call("GET", "/me/player"),
                call("PUT", "/me/player/pause", params={"device_id": "device-123"}),
            ],
        )

    def test_pause_for_command_leaves_already_paused_player_alone(self):
        client = self.make_client()
        client._request.return_value = {
            "is_playing": False,
            "device": {"id": "device-123"},
        }

        snapshot = client.pause_for_command()

        self.assertFalse(snapshot["was_playing"])
        self.assertEqual(client._request.call_count, 1)

    def test_resume_after_command_restores_only_previous_playback(self):
        client = self.make_client()
        client.resume_after_command({"was_playing": True, "device_id": "device-123"})
        client._request.assert_called_once_with(
            "PUT", "/me/player/play", params={"device_id": "device-123"}
        )

        client._request.reset_mock()
        client.resume_after_command({"was_playing": False, "device_id": "device-123"})
        client._request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
