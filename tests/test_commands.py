import unittest

from echo.commands import Command, clean_transcript, parse_command
from echo.spotify import rank_track


class CommandParserTests(unittest.TestCase):
    def test_wake_word_and_keyword_command(self):
        self.assertEqual(parse_command("Echo next"), Command("next"))
        self.assertEqual(parse_command("AY-ko, next please"), Command("next"))
        self.assertEqual(parse_command("Hey Aiko, skip the song"), Command("next"))

    def test_full_sentence_transport_priority(self):
        self.assertEqual(parse_command("Echo, can you play the next?"), Command("next"))
        self.assertEqual(parse_command("Echo skip this song"), Command("next"))
        self.assertEqual(parse_command("Echo go back"), Command("previous"))
        self.assertEqual(parse_command("Echo pause the music"), Command("pause"))
        self.assertEqual(parse_command("Echo continue playing"), Command("resume"))

    def test_song_title_is_not_mistaken_for_next_command(self):
        self.assertEqual(
            parse_command("Echo play The Next Episode by Dr Dre"),
            Command("play", "the next episode", artist="dr dre"),
        )
        self.assertEqual(
            parse_command("Echo play Next to Me"),
            Command("play", "next to me", target_type="auto"),
        )
        self.assertEqual(
            parse_command("Echo play Back to Black"),
            Command("play", "back to black", target_type="auto"),
        )

    def test_track_play_request_with_artist(self):
        self.assertEqual(
            parse_command("Echo please play Save Your Tears by The Weeknd and Ariana Grande"),
            Command(
                "play",
                "save your tears",
                artist="the weeknd and ariana grande",
                target_type="auto",
            ),
        )
        self.assertEqual(
            parse_command("Echo play Blinding Lights"),
            Command("play", "blinding lights", target_type="auto"),
        )

    def test_playlist_intent(self):
        self.assertEqual(
            parse_command("Echo play my workout playlist"),
            Command("play", "workout", target_type="playlist"),
        )
        self.assertEqual(
            parse_command("Echo shuffle my workout playlist"),
            Command("play", "workout", mode="shuffle", target_type="playlist"),
        )
        self.assertEqual(
            parse_command("Echo play workout"),
            Command("play", "workout", target_type="auto"),
        )
        self.assertEqual(
            parse_command("Echo play playlist workout"),
            Command("play", "workout", target_type="playlist"),
        )
        self.assertEqual(
            parse_command("Echo shuffle workout"),
            Command("play", "workout", mode="shuffle", target_type="auto"),
        )

    def test_queue_intent(self):
        self.assertEqual(
            parse_command("Echo queue Starboy"),
            Command("queue", "starboy", target_type="track"),
        )
        self.assertEqual(
            parse_command("Echo add Starboy to my queue"),
            Command("queue", "starboy", target_type="track"),
        )
        self.assertEqual(
            parse_command("Echo put Save Your Tears by The Weeknd in the queue"),
            Command(
                "queue",
                "save your tears",
                artist="the weeknd",
                target_type="track",
            ),
        )

    def test_shuffle_and_repeat(self):
        self.assertEqual(parse_command("Echo shuffle"), Command("shuffle", mode="on"))
        self.assertEqual(parse_command("Echo turn shuffle off"), Command("shuffle", mode="off"))
        self.assertEqual(parse_command("Echo repeat this song"), Command("repeat", mode="track"))
        self.assertEqual(parse_command("Echo repeat playlist"), Command("repeat", mode="context"))
        self.assertEqual(parse_command("Echo turn repeat off"), Command("repeat", mode="off"))

    def test_volume_commands(self):
        self.assertEqual(parse_command("echo volume up"), Command("volume", "+10"))
        self.assertEqual(parse_command("echo quieter"), Command("volume", "-10"))
        self.assertEqual(parse_command("echo mute"), Command("volume", "0"))
        self.assertEqual(parse_command("echo set volume to 35 percent"), Command("volume", "35"))

    def test_seek_commands(self):
        self.assertEqual(
            parse_command("Echo forward 30 seconds"),
            Command("seek", "30000", mode="forward"),
        )
        self.assertEqual(
            parse_command("Echo rewind 1 minute"),
            Command("seek", "60000", mode="backward"),
        )
        self.assertEqual(
            parse_command("Echo seek to 1:25"),
            Command("seek", "85000", mode="absolute"),
        )

    def test_playback_information(self):
        self.assertEqual(parse_command("Echo what's playing"), Command("now_playing"))
        self.assertEqual(parse_command("Echo what's next"), Command("get_queue"))
        self.assertEqual(parse_command("Echo show my queue"), Command("get_queue"))

    def test_empty_wake_is_not_a_command(self):
        self.assertEqual(clean_transcript("Echo"), "")
        self.assertIsNone(parse_command("Echo"))

    def test_track_ranker_uses_artist_and_title(self):
        tracks = [
            {
                "name": "Save Your Tears",
                "artists": [{"name": "The Weeknd"}],
                "uri": "spotify:track:one",
            },
            {
                "name": "Save Your Tears (Remix)",
                "artists": [
                    {"name": "The Weeknd"},
                    {"name": "Ariana Grande"},
                ],
                "uri": "spotify:track:two",
            },
        ]
        best = rank_track(
            "save your tears",
            tracks,
            artist_hint="the weeknd and ariana grande",
        )
        self.assertEqual(best["uri"], "spotify:track:two")


if __name__ == "__main__":
    unittest.main()
