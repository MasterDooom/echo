from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from collections import deque

from vosk import KaldiRecognizer, Model

from . import config
from .audio import Microphone
from .commands import parse_command
from .model import ensure_model
from .spotify import SpotifyClient

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
FRAME_LENGTH = 800  # 50 ms frames for responsive local wake detection
PRE_ROLL_FRAMES = 32  # 1.6 seconds; keeps words spoken directly after "Echo"
COMMAND_WINDOW_SECONDS = 5.0
WAKE_GRAMMAR = json.dumps(["echo", "hey echo", "[unk]"])
WAKE_WORD_RE = re.compile(r"\\becho\\b", re.IGNORECASE)


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
        return bool(WAKE_WORD_RE.search(text))

    def run(self) -> None:
        try:
            self.emit("status", "loading")
            model_path = ensure_model(lambda message: self.emit("message", message))
            model = Model(str(model_path))
            wake = KaldiRecognizer(model, SAMPLE_RATE, WAKE_GRAMMAR)
            wake.SetWords(False)

            self.microphone = Microphone(SAMPLE_RATE, FRAME_LENGTH)
            self.microphone.start()
            spotify = SpotifyClient()
            spotify._access_token()  # Fail early if Spotify has not been connected once.
            self.emit("status", "listening")
            self.emit("message", "Echo is ready")

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
                command_text = self._capture_command(model, list(history))
                history.clear()
                wake.Reset()

                if command_text:
                    command = parse_command(command_text)
                    if command is None:
                        self.emit("message", "Didn't catch that command")
                    else:
                        self.emit("status", "working")
                        try:
                            self.emit("message", spotify.execute(command))
                        except Exception as exc:
                            log.exception("Command failed")
                            self.emit("message", str(exc))
                else:
                    self.emit("message", "Didn't catch that command")
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

    def _capture_command(self, model: Model, pre_roll: list[bytes]) -> str:
        """Reuse the wake-word audio, then transcribe only a short command window."""
        assert self.microphone is not None
        recognizer = KaldiRecognizer(model, SAMPLE_RATE)
        recognizer.SetWords(False)
        recognized: list[str] = []

        def accept(raw: bytes) -> str:
            if not recognizer.AcceptWaveform(raw):
                return ""
            text = json.loads(recognizer.Result()).get("text", "").strip()
            if text:
                recognized.append(text)
            combined = " ".join(recognized).strip()
            return combined if parse_command(combined) else ""

        for raw in pre_roll:
            result = accept(raw)
            if result:
                return result

        deadline = time.monotonic() + COMMAND_WINDOW_SECONDS
        while not self.shutdown.is_set() and time.monotonic() < deadline:
            raw = self.microphone.read(timeout=0.2)
            if raw is None:
                continue
            result = accept(raw)
            if result:
                return result

        final_text = json.loads(recognizer.FinalResult()).get("text", "").strip()
        if final_text:
            recognized.append(final_text)
        combined = " ".join(recognized).strip()
        return combined if parse_command(combined) else ""
