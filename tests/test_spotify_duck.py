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

        self.assertEqual(
            snapshot,
            {"was_playing": True, "device_id": "device-123", "pause_error": None},
        )
        self.assertEqual(
            client._request.call_args_list,
            [
                call("GET", "/me/player"),
                call("PUT", "/me/player/pause", params={"device_id": "device-123"}),
            ],
        )

    def test_pause_error_keeps_snapshot_for_later_restore(self):
        client = self.make_client()
        client._request.side_effect = [
            {"is_playing": True, "device": {"id": "device-123"}},
            RuntimeError("malformed Spotify pause response"),
            {},
        ]

        snapshot = client.pause_for_command()

        self.assertTrue(snapshot["was_playing"])
        self.assertEqual(snapshot["device_id"], "device-123")
        self.assertEqual(snapshot["pause_error"], "malformed Spotify pause response")
        client.resume_after_command(snapshot)
        self.assertEqual(
            client._request.call_args_list[-1],
            call("PUT", "/me/player/play", params={"device_id": "device-123"}),
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

    def test_device_prefers_active_device_over_inactive_computer(self):
        client = self.make_client()
        client._request.return_value = {
            "devices": [
                {"id": "inactive-pc", "type": "Computer", "is_active": False, "is_restricted": False},
                {"id": "active-phone", "type": "Smartphone", "is_active": True, "is_restricted": False},
            ]
        }

        self.assertEqual(client._device()["id"], "active-phone")

    def test_device_prefers_device_paused_at_wake_time(self):
        client = self.make_client()
        client._request.return_value = {
            "devices": [
                {"id": "active-pc", "type": "Computer", "is_active": True, "is_restricted": False},
                {"id": "paused-device", "type": "Smartphone", "is_active": False, "is_restricted": False},
            ]
        }

        self.assertEqual(client._device("paused-device")["id"], "paused-device")

    def test_next_targets_the_device_that_was_paused(self):
        client = self.make_client()
        client._request.return_value = {}

        client.execute(type("Command", (), {"kind": "next", "query": ""})(), device_id="device-123")

        client._request.assert_called_once_with(
            "POST", "/me/player/next", params={"device_id": "device-123"}
        )


if __name__ == "__main__":
    unittest.main()
