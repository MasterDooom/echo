"""Single-stream microphone listener using Vosk for wake and command recognition."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import sounddevice as sd
from vosk import KaldiRecognizer, Model

LOGGER = logging.getLogger(__name__)
SAMPLE_RATE = 16_000
BLOCK_SIZE = 1_600  # 100 ms: responsive without hammering the recognizer.
WAKE_GRAMMAR = json.dumps(["echo", "hey echo", "[unk]"])


def _has_wake_word(text: str) -> bool:
    return "echo" in re.findall(r"[a-z]+", text.lower())


class VoiceListener:
    """Keep one microphone stream open and only use free-form STT after wake-up."""

    def __init__(self, vosk_model_path: Path, command_timeout: float = 4.5) -> None:
        self._model = Model(str(vosk_model_path))
        self._wake_recognizer = KaldiRecognizer(self._model, SAMPLE_RATE, WAKE_GRAMMAR)
        self._wake_recognizer.SetWords(False)
        self._command_timeout = command_timeout
        self._history: deque[bytes] = deque(maxlen=12)  # 1.2 s of pre-roll.
        self._stream: sd.RawInputStream | None = None

    def start(self) -> None:
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="int16",
        )
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _read_frame(self) -> bytes:
        if self._stream is None:
            raise RuntimeError("Microphone stream is not running")
        data, overflowed = self._stream.read(BLOCK_SIZE)
        if overflowed:
            LOGGER.debug("Microphone input overflow")
        return bytes(data)

    def wait_for_wake_word(self, should_stop: Callable[[], bool]) -> bool:
        """Listen for 'Echo' using a restricted-vocabulary streaming recognizer."""
        while not should_stop():
            frame = self._read_frame()
            self._history.append(frame)
            if self._wake_recognizer.AcceptWaveform(frame):
                result = json.loads(self._wake_recognizer.Result()).get("text", "")
                if _has_wake_word(result):
                    self._wake_recognizer.Reset()
                    return True
            else:
                partial = json.loads(self._wake_recognizer.PartialResult()).get("partial", "")
                if _has_wake_word(partial):
                    self._wake_recognizer.Reset()
                    return True
        return False

    def transcribe_command(self, timeout: float | None = None) -> str:
        """Transcribe the wake-word tail plus the next short utterance."""
        recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)
        recognizer.SetWords(False)
        started = time.monotonic()
        audio_frames = list(self._history)
        self._history.clear()
        max_seconds = timeout if timeout is not None else self._command_timeout

        # Preserve the audio around "Echo" so "Echo next" works in one utterance.
        buffered_results: list[str] = []
        for frame in audio_frames:
            if recognizer.AcceptWaveform(frame):
                text = json.loads(recognizer.Result()).get("text", "").strip()
                if text:
                    buffered_results.append(text)
        if buffered_results:
            return " ".join(buffered_results)

        while time.monotonic() - started < max_seconds:
            frame = self._read_frame()
            if recognizer.AcceptWaveform(frame):
                text = json.loads(recognizer.Result()).get("text", "").strip()
                if text:
                    return text

        return json.loads(recognizer.FinalResult()).get("text", "").strip()
