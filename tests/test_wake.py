import unittest

from echo.wake import (
    OpenWakeWordDetector,
    WakeWordStabilizer,
    wake_candidate_detected,
    wake_word_detected,
)


class WakeWordTests(unittest.TestCase):
    def test_confirmed_wake_must_be_at_the_start_of_the_transcript(self):
        for transcript in ("echo", "hey echo", "Echo, play Starboy", "ECHO pause", "ayko play Starboy", "aiko next"):
            with self.subTest(transcript=transcript):
                self.assertTrue(wake_word_detected(transcript))

        for transcript in (
            "next echo",
            "I heard an echo",
            "there was an echo in the room",
            "echoes",
            "gecko",
            "my friend said ayko yesterday",
        ):
            with self.subTest(transcript=transcript):
                self.assertFalse(wake_word_detected(transcript))

    def test_vosk_candidates_are_prefix_limited(self):
        self.assertTrue(wake_candidate_detected("echo"))
        self.assertTrue(wake_candidate_detected("ayko play blinding lights"))
        self.assertTrue(wake_candidate_detected("aiko next"))
        self.assertTrue(wake_candidate_detected("hey echo"))
        self.assertFalse(wake_candidate_detected("next echo"))
        self.assertFalse(wake_candidate_detected("I heard an echo"))

    def test_stabilizer_requires_consecutive_speech_gated_hits(self):
        gate = WakeWordStabilizer(required_hits=4)
        self.assertFalse(gate.update(True, True))
        self.assertFalse(gate.update(True, True))
        self.assertFalse(gate.update(True, True))
        self.assertTrue(gate.update(True, True))
        self.assertFalse(gate.update(True, True))

    def test_stabilizer_resets_on_silence_or_non_candidate(self):
        gate = WakeWordStabilizer(required_hits=3)
        self.assertFalse(gate.update(True, True))
        self.assertFalse(gate.update(True, False))
        self.assertFalse(gate.update(True, True))
        self.assertFalse(gate.update(False, True))
        self.assertFalse(gate.update(True, True))
        self.assertFalse(gate.update(True, True))
        self.assertTrue(gate.update(True, True))

    def test_openwakeword_is_optional_until_a_custom_model_is_configured(self):
        detector = OpenWakeWordDetector(model_path="")
        self.assertFalse(detector.enabled)
        self.assertIsNone(detector.process(b"\\x00" * 320))


if __name__ == "__main__":
    unittest.main()
