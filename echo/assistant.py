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
FRAME_LENGTH = 160
PRE_ROLL_FRAMES = 48  # Preserve the tail of commands spoken immediately after AY-koh
WAKE_GRAMMAR = json.dumps([
    "echo", "ecco", "aiko", "ayko", "eyko", "ayco", "eyco",
    "ay ko", "ey ko", "ay-ko", "ey-ko", "hey echo", "hey aiko", "[unk]"
])

COMMAND_WINDOW_SECONDS = 8.0
MIN_CAPTURE_SECONDS = 0.55
NO_COMMAND_WAIT_SECONDS = 3.0
VAD_THRESHOLD = 0.20
END_SILENCE_FRAMES = 65


class EchoAssistant(threading.Thread):
    def __init__(self, events: queue.Queue, *, daemon: bool = True) -> None:
        super().__init__(name="EchoAssistant", daemon=daemon)
        self.events = events
        self.shutdown = threading.Event()
        # Privacy mode closes the PortAudio input stream and releases the device.
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
            self.emit("message", "Privacy mode off · reopening microphone")
        else:
            self.paused.set()
            self.emit("status", "privacy")
            self.emit("message", "Privacy mode on · releasing microphone")

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
            self.emit("message", "Command finished, but Spotify could not resume. Check Spotify playback.")

    def _open_microphone(self) -> Microphone:
        microphone = Microphone(SAMPLE_RATE, FRAME_LENGTH)
        microphone.start()
        return microphone

    def _release_microphone(self) -> None:
        """Close and forget the input stream so Windows releases the device."""
        microphone = self.microphone
        self.microphone = None
        if microphone is not None:
            microphone.close()

    def run(self) -> None:
        try:
            self.emit("status", "loading")
            model_path = ensure_model(lambda message: self.emit("message", message))
            wake_model = Model(str(model_path))
            wake = KaldiRecognizer(wake_model, SAMPLE_RATE, WAKE_GRAMMAR)
            wake.SetWords(False)

            spotify = SpotifyClient()
            spotify._access_token()
            spotify.require_scopes()

            command_recognizer = CommandRecognizer()
            self.microphone = self._open_microphone()
            self.emit("status", "listening")
            self.emit("message", "Ready · say AY-koh, then your command")

            history: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
            while not self.shutdown.is_set():
                if self.paused.is_set():
                    self._release_microphone()
                    history.clear()
                    wake.Reset()
                    self.emit("status", "privacy")
                    while self.paused.is_set() and not self.shutdown.is_set():
                        time.sleep(0.10)
                    if self.shutdown.is_set():
                        break
                    self.microphone = self._open_microphone()
                    history.clear()
                    wake.Reset()
                    self.emit("status", "listening")
                    self.emit("message", "Microphone active · local wake detection")
                    continue

                if self.microphone is None:
                    self.microphone = self._open_microphone()

                pair = self.microphone.read_pair(timeout=0.20)
                if pair is None:
                    continue
                wake_audio, original_audio = pair
                history.append(original_audio)

                if not self._wake_detected(wake, wake_audio):
                    continue

                self.emit("status", "awake")
                playback_snapshot: dict[str, Any] = {
                    "was_playing": False,
                    "device_id": None,
                }
                try:
                    playback_snapshot = spotify.pause_for_command()
                    if playback_snapshot.get("was_playing"):
                        self.emit("message", "Spotify paused · listening")
                except Exception as exc:
                    log.warning("Could not pause Spotify before command capture: %s", exc)
                    self.emit("message", f"Spotify pause failed: {exc}")

                frames = self._capture_command(list(history))
                history.clear()
                wake.Reset()

                if not frames:
                    self._restore_playback(spotify, playback_snapshot)
                    if self.paused.is_set():
                        continue
                    self.emit("message", "Wake heard · no command detected")
                    self.emit("status", "listening")
                    continue

                self.emit("status", "working")
                command = None
                try:
                    transcript = command_recognizer.transcribe_frames(frames)
                    log.info("Recognized command transcript: %r", transcript)
                    command = parse_command(transcript)
                    if command is None:
                        detail = f"I heard: {transcript[:90]}" if transcript else "I didn't hear a command"
                        self.emit("message", f"{detail} · try again")
                        self._restore_playback(spotify, playback_snapshot)
                    elif command.kind == "pause":
                        self.emit("message", "Paused")
                    else:
                        log.info("Parsed command: %s query=%r artist=%r mode=%r target=%r",
                                 command.kind, command.query, command.artist, command.mode, command.target_type)
                        result = spotify.execute(
                            command,
                            device_id=playback_snapshot.get("device_id"),
                        )
                        self.emit("message", f"{result} · heard: {transcript[:45]}")
                        if command.kind not in {"play", "resume"}:
                            self._restore_playback(spotify, playback_snapshot)
                except Exception as exc:
                    log.exception("Command failed")
                    if command is None or command.kind != "pause":
                        self._restore_playback(spotify, playback_snapshot)
                    self.emit("message", str(exc))

                if not self.paused.is_set():
                    self.emit("status", "listening")

        except Exception as exc:
            log.exception("Echo stopped")
            self.emit("status", "error")
            self.emit("message", str(exc))
        finally:
            try:
                self._release_microphone()
            except Exception:
                log.exception("Could not close microphone")

    def _capture_command(self, pre_roll: list[bytes]) -> list[bytes]:
        """Capture a short command after Spotify has been paused."""
        assert self.microphone is not None
        frames = list(pre_roll)
        speech_started = False
        trailing_silence = 0
        started_at = time.monotonic()
        deadline = started_at + COMMAND_WINDOW_SECONDS

        while (
            not self.shutdown.is_set()
            and not self.paused.is_set()
            and time.monotonic() < deadline
        ):
            pair = self.microphone.read_pair(timeout=0.15)
            if pair is None:
                continue
            _processed_audio, original_audio = pair
            frames.append(original_audio)

            probability = self.microphone.speech_probability
            # WebRTC VAD can under-score quiet Indian-English consonants.
            # Add a conservative raw-energy fallback for laptop microphones.
            samples = self.microphone._numpy.frombuffer(original_audio, dtype=self.microphone._numpy.int16)
            rms = float(self.microphone._numpy.sqrt(self.microphone._numpy.mean(
                samples.astype(self.microphone._numpy.float32) ** 2
            ))) / 32768.0
            speech_detected = probability >= VAD_THRESHOLD or rms >= 0.005
            if speech_detected:
                speech_started = True
                trailing_silence = 0
            elif speech_started:
                trailing_silence += 1

            elapsed = time.monotonic() - started_at
            if (
                speech_started
                and elapsed >= MIN_CAPTURE_SECONDS
                and trailing_silence >= END_SILENCE_FRAMES
            ):
                break
            if not speech_started and elapsed >= NO_COMMAND_WAIT_SECONDS:
                return []

        return frames if speech_started and not self.paused.is_set() else []
