from __future__ import annotations

import json
import logging
import queue
import threading
import time
from array import array
from collections import deque

import pvporcupine
from vosk import KaldiRecognizer, Model

from . import config
from .audio import Microphone
from .commands import parse_command
from .model import ensure_model
from .spotify import SpotifyClient

log = logging.getLogger(__name__)
PRE_ROLL_FRAMES = 24  # about 770 ms; preserves words spoken directly after "Echo"
COMMAND_WINDOW_SECONDS = 7.0


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

    def run(self) -> None:
        porcupine = None
        try:
            self.emit("status", "loading")
            access_key = config.picovoice_access_key()
            keyword_file = config.keyword_path()
            if not access_key:
                raise RuntimeError("Add PICOVOICE_ACCESS_KEY to .env.")
            if not keyword_file.is_file():
                raise RuntimeError(f"Add your custom Echo wake-word file here: {keyword_file}")

            model_path = ensure_model(lambda message: self.emit("message", message))
            model = Model(str(model_path))
            porcupine = pvporcupine.create(access_key=access_key, keyword_paths=[str(keyword_file)])
            self.microphone = Microphone(porcupine.sample_rate, porcupine.frame_length)
            self.microphone.start()
            spotify = SpotifyClient()
            spotify._access_token()  # Fail early if the one-time Spotify login has not been done.
            self.emit("status", "listening")
            self.emit("message", "Echo is ready")

            history: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
            while not self.shutdown.is_set():
                if self.paused.is_set():
                    time.sleep(0.12)
                    self.microphone.clear()
                    continue
                raw = self.microphone.read()
                if raw is None:
                    continue
                history.append(raw)
                pcm = array("h")
                pcm.frombytes(raw)
                if porcupine.process(pcm) >= 0:
                    self.emit("status", "awake")
                    command_text = self._capture_command(model, list(history))
                    history.clear()
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
            if porcupine is not None:
                porcupine.delete()

    def _capture_command(self, model: Model, pre_roll: list[bytes]) -> str:
        assert self.microphone is not None
        recognizer = KaldiRecognizer(model, self.microphone.sample_rate)
        recognizer.SetWords(False)
        deadline = time.monotonic() + COMMAND_WINDOW_SECONDS

        def consume(raw: bytes) -> str:
            if recognizer.AcceptWaveform(raw):
                return json.loads(recognizer.Result()).get("text", "").strip()
            return ""

        for raw in pre_roll:
            result = consume(raw)
            if result:
                command = parse_command(result)
                if command:
                    return result
                recognizer.Reset()  # The wake word may have ended before the command began.

        while not self.shutdown.is_set() and time.monotonic() < deadline:
            raw = self.microphone.read(timeout=0.2)
            if raw is None:
                continue
            result = consume(raw)
            if result:
                if parse_command(result):
                    return result
                recognizer.Reset()

        final_text = json.loads(recognizer.FinalResult()).get("text", "").strip()
        return final_text if parse_command(final_text) else ""
