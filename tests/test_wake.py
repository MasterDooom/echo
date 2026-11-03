import unittest

from echo.wake import wake_word_detected


class WakeWordTests(unittest.TestCase):
    def test_detects_standalone_echo_case_insensitively(self):
        self.assertTrue(wake_word_detected("echo"))
        self.assertTrue(wake_word_detected("hey Echo"))
        self.assertTrue(wake_word_detected("next, ECHO"))

    def test_does_not_match_substrings_or_literal_escape(self):
        self.assertFalse(wake_word_detected("echoes"))
        self.assertFalse(wake_word_detected("gecko"))
        self.assertFalse(wake_word_detected(r"\\becho\\b"))


if __name__ == "__main__":
    unittest.main()
