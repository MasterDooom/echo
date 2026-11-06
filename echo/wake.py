from __future__ import annotations

import logging
import os
import re
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

# The fallback recognizer is deliberately limited to the two most plausible
# spellings of the user's AY-koh pronunciation. The independent Whisper
# verification stage only accepts the canonical "Echo" spelling at the start
# of the utterance.
_CANDIDATE_PREFIX = re.compile(
    r"^(?:hey\\s+)?(?:echo|ayko|aiko)(?=\\s|$)",
    re.IGNORECASE,
)
_CONFIRMED_PREFIX = re.compile(
    r"^(?:hey\\s+)?echo(?=\\s|$)",
    re.IGNORECASE,
)


def wake_candidate_detected(text: str) -> bool:
    """Recognize a candidate only when the wake name starts the utterance."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return bool(_CANDIDATE_PREFIX.match(normalized))


def wake_word_detected(text: str) -> bool:
    """Accept only a canonical Echo wake phrase at the beginning of a transcript."""
    normalized = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    return bool(_CONFIRMED_PREFIX.match(normalized))


class WakeWordStabilizer:
    """Require consecutive speech-gated detections before emitting one wake."""

    def __init__(self, required_hits: int = 4) -> None:
        if required_hits < 1:
            raise ValueError("required_hits must be at least 1")
        self.required_hits = required_hits
        self._hits = 0

    def reset(self) -> None:
        self._hits = 0

    def update(self, candidate: bool, speech_active: bool) -> bool:
        if not candidate or not speech_active:
            self._hits = 0
            return False
        self._hits += 1
        if self._hits < self.required_hits:
            return False
        self._hits = 0
        return True


class OpenWakeWordDetector:
    """Optional openWakeWord streaming detector for a trained custom Echo model.

    A custom model is required because the upstream bundle does not ship an
    Echo model. Inference stays local; the model file must be supplied locally.
    """

    FRAME_BYTES = 160 * 2  # 10 ms, 16 kHz, mono int16
    INFERENCE_BYTES = 1280 * 2  # 80 ms is the recommended efficient batch

    def __init__(self, model_path: str | None = None) -> None:
        configured_path = (model_path or os.environ.get("ECHO_WAKE_MODEL_PATH", "")).strip()
        self.model_path = Path(configured_path).expanduser() if configured_path else None
        self.threshold = float(os.environ.get("ECHO_WAKE_THRESHOLD", "0.65"))
        self.vad_threshold = float(os.environ.get("ECHO_WAKE_VAD_THRESHOLD", "0.20"))
        self._buffer = bytearray()
        self._scores: deque[float] = deque(maxlen=3)
        self._numpy = None
        self.model = None

        if self.model_path is None:
            return
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"ECHO_WAKE_MODEL_PATH does not exist: {self.model_path}"
            )

        try:
            import numpy as np
            from openwakeword.model import Model
        except ImportError as exc:
            raise RuntimeError(
                "A custom Echo wake model is configured, but openWakeWord is not installed. "
                "Install it with: python -m pip install openwakeword"
            ) from exc

        self._numpy = np
        self.model = Model(
            wakeword_models=[str(self.model_path)],
            inference_framework="onnx",
            vad_threshold=self.vad_threshold,
        )
        log.info(
            "Loaded local openWakeWord model %s (threshold=%.2f, vad_threshold=%.2f)",
            self.model_path,
            self.threshold,
            self.vad_threshold,
        )

    @property
    def enabled(self) -> bool:
        return self.model is not None

    def reset(self) -> None:
        self._buffer.clear()
        self._scores.clear()
        if self.model is not None:
            self.model.reset()

    def process(self, pcm16: bytes) -> tuple[bool, float] | None:
        """Feed one 10 ms PCM frame; return only when an 80 ms score is ready."""
        if self.model is None:
            return None
        self._buffer.extend(pcm16)
        if len(self._buffer) < self.INFERENCE_BYTES:
            return None

        chunk = bytes(self._buffer[: self.INFERENCE_BYTES])
        del self._buffer[: self.INFERENCE_BYTES]
        samples = self._numpy.frombuffer(chunk, dtype=self._numpy.int16)
        predictions = self.model.predict(samples)
        score = max((float(value) for value in predictions.values()), default=0.0)
        self._scores.append(score)

        # A single short spike must not wake Echo. Require two of the last
        # three model scores to clear the configured threshold.
        hits = sum(value >= self.threshold for value in self._scores)
        detected = len(self._scores) == self._scores.maxlen and hits >= 2
        return detected, score
