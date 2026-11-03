from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from typing import Any

from vosk import KaldiRecognizer, Model

from .audio import Microphone
from .commands import parse_command
from .model import ensure_model
from .spotify import SpotifyClient
from .stt import CommandRecognizer
from .wake import wake_word_detected

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
FRAME_LENGTH = 160  # 10 ms for low-latency wake detection.
PRE_ROLL_FRAMES = 24  # 240 ms to preserve the command onset after the wake word.
WAKE_GRAMMAR = json.dumps(["echo", "hey echo", "[unk]"])

COMMAND_WINDOW_SECONDS = 4.5
MIN_CAPTURE_SECONDS = 0.8
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

    def _restore_playback(self, spotify: SpotifyClient, snapshot: dict[str, Any]) -> None:
        try:
            spotify.resume_after_command(snapshot)
        except Exception:
            log.exception("Could not restore Spotify after listening")

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

            self.emit("status", "listening")
            self.emit("message", "Ready · Spotify pauses briefly while Echo listens")

            # Idle mode only recognizes the restricted wake grammar. It never
            # runs general transcription on the music or room audio.
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

                if not self._wake_detected(wake, raw):
                    continue

                self.emit("status", "awake")

                # Pause Spotify immediately after the wake word. This is more
                # dependable than trying to cancel sung lyrics from the mic.
                playback_snapshot: dict[str, Any] = {"was_playing": False, "device_id": None}
                try:
                    playback_snapshot = spotify.pause_for_command()
                    if playback_snapshot.get("was_playing"):
                        self.emit("message", "Spotify paused · listening")
                except Exception as exc:
                    log.warning("Could not pause Spotify before command capture: %s", exc)
                    self.emit("message", "Spotify couldn't be paused; use headphones to avoid music bleed")

                frames = self._capture_command(list(history))
                history.clear()
                wake.Reset()

                if not frames:
                    self._restore_playback(spotify, playback_snapshot)
                    self.emit("message", "Wake heard · no command detected")
                    self.emit("status", "listening")
                    continue

                self.emit("status", "working")
                command = None
                try:
                    transcript = command_recognizer.transcribe_frames(frames)
                    command = parse_command(transcript)
                    if command is None:
                        self.emit("message", f"Didn't catch that · {transcript[:48]}" if transcript else "Didn't catch that")
                        self._restore_playback(spotify, playback_snapshot)
                    elif command.kind == "pause":
                        # Playback was already paused before command capture.
                        self.emit("message", "Paused")
                    else:
                        result = spotify.execute(command)
                        if command.kind in {"next", "previous", "volume"}:
                            self._restore_playback(spotify, playback_snapshot)
                        self.emit("message", result)
                except Exception as exc:
                    log.exception("Command failed")
                    if command is None or command.kind != "pause":
                        self._restore_playback(spotify, playback_snapshot)
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
        """Capture only the short utterance after Spotify has been paused."""
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

        # Whisper has its own VAD. The tiny pre-roll protects the first
        # consonant if "Echo next" is spoken without a long pause.
        return frames if speech_started else []
