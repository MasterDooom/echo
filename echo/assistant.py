from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque

from vosk import KaldiRecognizer, Model

from . import config
from .audio import Microphone
from .commands import parse_command
from .model import ensure_model
from .spotify import SpotifyClient
from .stt import CommandRecognizer
from .wake import wake_word_detected

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
FRAME_LENGTH = 160  # WebRTC AEC works in 10 ms frames at 16 kHz.
PRE_ROLL_FRAMES = 12  # 120 ms to preserve the command onset without replaying the wake.
WAKE_GRAMMAR = json.dumps(["echo", "hey echo", "[unk]"])

COMMAND_WINDOW_SECONDS = 4.5
MIN_CAPTURE_SECONDS = 1.2
NO_COMMAND_WAIT_SECONDS = 2.2
VAD_THRESHOLD = 0.30
END_SILENCE_FRAMES = 45  # 450 ms


class EchoAssistant(threading.Thread):
    def __init__(self, events: queue.Queue, *, daemon: bool = True) -> None:
        super().__init__(name="EchoAssistant", daemon=daemon)
        self.events = events
        self.shutdown = threading.Event()
        self.paused = threading.Event()
        self.microphone: Microphone | None = None

    def emit(self, kind: str, value: str) -> None:
        self.events.put((kind, value))

    def stop(self) -> None:
        self.shutdown.set()

    def toggle_paused(self) -> None:
        if self.shutdown.is_set() or not self.is_alive():
            self.emit("status", "error")
            self.emit("message", "Echo is not running. Fix setup, then restart it.")
            return
        if self.paused.is_set():
            self.paused.clear()
            if self.microphone:
                self.microphone.clear()
            self.emit("status", "listening")
        else:
            self.paused.set()
            self.emit("status", "paused")

    @staticmethod
    def _wake_detected(recognizer: KaldiRecognizer, raw: bytes) -> bool:
        if recognizer.AcceptWaveform(raw):
            text = json.loads(recognizer.Result()).get("text", "")
        else:
            text = json.loads(recognizer.PartialResult()).get("partial", "")
        return wake_word_detected(text)

    def run(self) -> None:
        try:
            self.emit("status", "loading")
            model_path = ensure_model(lambda message: self.emit("message", message))
            wake_model = Model(str(model_path))
            wake = KaldiRecognizer(wake_model, SAMPLE_RATE, WAKE_GRAMMAR)
            wake.SetWords(False)

            self.microphone = Microphone(SAMPLE_RATE, FRAME_LENGTH)
            self.microphone.start()
            spotify = SpotifyClient()
            spotify._access_token()  # Fail early if the one-time Spotify login has not been done.
            command_recognizer = CommandRecognizer()

            warning_sent = False
            if self.microphone.warning:
                self.emit("message", self.microphone.warning)
                warning_sent = True
            self.emit("status", "listening")
            if self.microphone.aec_enabled:
                self.emit("message", f"Ready · echo cancellation active ({self.microphone.loopback_name})")
            else:
                self.emit("message", "Ready · use headphones to prevent music bleed")

            # Only the restricted wake grammar runs while idle. The command
            # recognizer is invoked only after the wake word has been detected.
            history: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
            while not self.shutdown.is_set():
                if self.paused.is_set():
                    time.sleep(0.12)
                    self.microphone.clear()
                    history.clear()
                    wake.Reset()
                    continue

                raw = self.microphone.read()
                if raw is None:
                    continue
                history.append(raw)

                if self.microphone.warning and not warning_sent:
                    self.emit("message", self.microphone.warning)
                    warning_sent = True

                if not self._wake_detected(wake, raw):
                    continue

                self.emit("status", "awake")
                frames = self._capture_command(list(history))
                history.clear()
                wake.Reset()

                if not frames:
                    self.emit("message", "Wake heard · waiting for a command timed out")
                    self.emit("status", "listening")
                    continue

                self.emit("status", "working")
                try:
                    transcript = command_recognizer.transcribe_frames(frames)
                    command = parse_command(transcript)
                    if command is None:
                        self.emit("message", f"Didn't catch that · {transcript[:48]}" if transcript else "Didn't catch that")
                    else:
                        self.emit("message", spotify.execute(command))
                except Exception as exc:
                    log.exception("Command failed")
                    self.emit("message", str(exc))

                self.emit("status", "listening")

        except Exception as exc:
            log.exception("Echo stopped")
            self.emit("status", "error")
            self.emit("message", str(exc))
        finally:
            if self.microphone:
                try:
                    self.microphone.close()
                except Exception:
                    log.exception("Could not close microphone")

    def _capture_command(self, pre_roll: list[bytes]) -> list[bytes]:
        """Capture one fresh utterance after wake, with a 120 ms onset buffer."""
        assert self.microphone is not None
        frames = list(pre_roll)
        speech_started = False
        trailing_silence = 0
        started_at = time.monotonic()
        deadline = started_at + COMMAND_WINDOW_SECONDS

        while not self.shutdown.is_set() and time.monotonic() < deadline:
            raw = self.microphone.read(timeout=0.15)
            if raw is None:
                continue
            frames.append(raw)

            probability = self.microphone.speech_probability
            if probability >= VAD_THRESHOLD:
                speech_started = True
                trailing_silence = 0
            elif speech_started:
                trailing_silence += 1

            elapsed = time.monotonic() - started_at
            if speech_started and elapsed >= MIN_CAPTURE_SECONDS and trailing_silence >= END_SILENCE_FRAMES:
                break
            if not speech_started and elapsed >= NO_COMMAND_WAIT_SECONDS:
                return []

        # Whisper's own VAD trims the wake-word tail and any short silence.
        return frames if speech_started else []
