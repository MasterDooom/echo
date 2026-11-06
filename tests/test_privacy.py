import importlib
import queue
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_assistant_without_audio_dependencies():
    """The tested methods do not need the live Vosk or PortAudio libraries."""
    fake_vosk = types.ModuleType("vosk")
    fake_vosk.KaldiRecognizer = object
    fake_vosk.Model = object
    fake_sounddevice = types.ModuleType("sounddevice")

    with patch.dict(
        sys.modules,
        {"vosk": fake_vosk, "sounddevice": fake_sounddevice},
    ):
        sys.modules.pop("echo.assistant", None)
        sys.modules.pop("echo.audio", None)
        return importlib.import_module("echo.assistant").EchoAssistant


class MicrophonePrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assistant_class = load_assistant_without_audio_dependencies()

    def test_privacy_toggle_sets_and_clears_privacy_event(self):
        assistant = self.assistant_class(queue.Queue())
        assistant.is_alive = lambda: True

        assistant.toggle_paused()
        self.assertTrue(assistant.paused.is_set())

        assistant.toggle_paused()
        self.assertFalse(assistant.paused.is_set())

    def test_privacy_mode_closes_and_forgets_microphone(self):
        assistant = self.assistant_class(queue.Queue())
        microphone = Mock()
        assistant.microphone = microphone

        assistant._release_microphone()

        microphone.close.assert_called_once_with()
        self.assertIsNone(assistant.microphone)

    def test_releasing_when_already_closed_is_safe(self):
        assistant = self.assistant_class(queue.Queue())
        assistant.microphone = None
        assistant._release_microphone()
        self.assertIsNone(assistant.microphone)

    def test_wake_verification_rejects_echo_mentioned_mid_sentence(self):
        from unittest.mock import Mock, patch

        assistant = self.assistant_class(queue.Queue())
        assistant.microphone = Mock()
        recognizer = Mock()
        recognizer.transcribe_frames.return_value = "I heard an echo"
        recognizer.last_diagnostics = {
            "avg_logprob": -0.2,
            "no_speech_probability": 0.1,
        }

        with patch("echo.assistant.WAKE_VERIFY_POSTROLL_FRAMES", 0):
            accepted, _frames, transcript = assistant._verify_wake(
                recognizer, [b"seed"]
            )

        self.assertFalse(accepted)
        self.assertEqual(transcript, "I heard an echo")
        recognizer.transcribe_frames.assert_called_once_with(
            [b"seed"], context_bias=False
        )

    def test_wake_verification_accepts_wake_plus_valid_command(self):
        from unittest.mock import Mock, patch

        assistant = self.assistant_class(queue.Queue())
        assistant.microphone = Mock()
        recognizer = Mock()
        recognizer.transcribe_frames.return_value = "Echo, play Starboy"
        recognizer.last_diagnostics = {
            "avg_logprob": -0.25,
            "no_speech_probability": 0.05,
        }

        with patch("echo.assistant.WAKE_VERIFY_POSTROLL_FRAMES", 0):
            accepted, _frames, transcript = assistant._verify_wake(
                recognizer, [b"seed"]
            )

        self.assertTrue(accepted)
        self.assertEqual(transcript, "Echo, play Starboy")

    def test_wake_verification_accepts_the_bare_wake_name(self):
        from unittest.mock import Mock, patch

        assistant = self.assistant_class(queue.Queue())
        assistant.microphone = Mock()
        recognizer = Mock()
        recognizer.transcribe_frames.return_value = "Echo"
        recognizer.last_diagnostics = {
            "avg_logprob": -0.3,
            "no_speech_probability": 0.05,
        }

        with patch("echo.assistant.WAKE_VERIFY_POSTROLL_FRAMES", 0):
            accepted, _frames, _transcript = assistant._verify_wake(
                recognizer, [b"seed"]
            )

        self.assertTrue(accepted)


if __name__ == "__main__":
    unittest.main()
