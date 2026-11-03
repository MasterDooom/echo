import unittest

from echo.commands import Command, clean_transcript, parse_command
from echo.spotify import rank_track


class CommandParserTests(unittest.TestCase):
    def test_wake_word_and_keyword_command(self):
        self.assertEqual(parse_command("Echo next"), Command("next"))
        self.assertEqual(parse_command("Hey Echo, skip the song"), Command("next"))

    def test_previous_pause_resume(self):
        self.assertEqual(parse_command("echo previous"), Command("previous"))
        self.assertEqual(parse_command("echo pause"), Command("pause"))
        self.assertEqual(parse_command("echo resume"), Command("resume"))

    def test_play_query_keeps_song_and_artist(self):
        self.assertEqual(
            parse_command("Echo play Save Your Tears by The Weeknd and Ariana Grande"),
            Command("play", "save your tears by the weeknd and ariana grande"),
        )

    def test_short_play_phrase(self):
        self.assertEqual(parse_command("play Blinding Lights"), Command("play", "blinding lights"))
        self.assertEqual(parse_command("Echo play Next to Me"), Command("play", "next to me"))
        self.assertEqual(parse_command("Echo play Back to Black"), Command("play", "back to black"))

    def test_volume_commands(self):
        self.assertEqual(parse_command("echo volume up"), Command("volume", "+10"))
        self.assertEqual(parse_command("echo quieter"), Command("volume", "-10"))
        self.assertEqual(parse_command("echo mute"), Command("volume", "0"))

    def test_empty_wake_is_not_a_command(self):
        self.assertEqual(clean_transcript("Echo"), "")
        self.assertIsNone(parse_command("Echo"))

    def test_track_ranker_uses_artist_and_title(self):
        tracks = [
            {"name": "Save Your Tears", "artists": [{"name": "The Weeknd"}], "uri": "spotify:track:one"},
            {"name": "Save Your Tears (Remix)", "artists": [{"name": "The Weeknd"}, {"name": "Ariana Grande"}], "uri": "spotify:track:two"},
        ]
        best = rank_track("save your tears the weeknd ariana grande", tracks)
        self.assertEqual(best["uri"], "spotify:track:two")


if __name__ == "__main__":
    unittest.main()
