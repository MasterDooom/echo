import unittest

from echo.wake import wake_word_detected


class WakeWordTests(unittest.TestCase):
    def test_standard_spelling(self):
        self.assertTrue(wake_word_detected("echo"))
        self.assertTrue(wake_word_detected("hey echo"))
        self.assertTrue(wake_word_detected("next echo"))

    def test_owner_pronunciation_aliases(self):
        for transcript in (
            "aiko",
            "ayko",
            "eyko",
            "ayco",
            "eyco",
            "ay ko",
            "ey ko",
            "ay-ko",
            "ey-ko",
            "hey aiko",
        ):
            with self.subTest(transcript=transcript):
                self.assertTrue(wake_word_detected(transcript))

    def test_no_substring_or_unrelated_speech_false_positives(self):
        for transcript in ("echoes", "gecko", "i go next", "a goal"):
            with self.subTest(transcript=transcript):
                self.assertFalse(wake_word_detected(transcript))


if __name__ == "__main__":
    unittest.main()
