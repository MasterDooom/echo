from __future__ import annotations

import logging
import os
import re
from collections import deque
from pathlib import Path
from urllib.request import urlretrieve

from .config import app_data_dir

log = logging.getLogger(__name__)

# Keep spelling tolerance narrow and require the wake phrase at the beginning.
# Vosk/Whisper may render the spoken AY-koh as "Echo", "ayko" or "aiko".
_CANDIDATE_PREFIX = re.compile(
    r"^(?:hey\s+)?(?:echo|ayko|aiko)(?=\s|$)",
    re.IGNORECASE,
)
_CONFIRMED_PREFIX = re.compile(
    r"^(?:hey\s+)?(?:echo|ayko|aiko)(?=\s|$)",
    re.IGNORECASE,
)


def _normalize_wake_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def wake_candidate_detected(text: str) -> bool:
    """Recognize a candidate only when the wake name starts the utterance."""
    return bool(_CANDIDATE_PREFIX.match(_normalize_wake_text(text)))


def wake_word_detected(text: str) -> bool:
    """Accept only a narrow wake spelling at the beginning of a transcript."""
    return bool(_CONFIRMED_PREFIX.match(_normalize_wake_text(text)))


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
    """Streaming openWakeWord detector for a locally supplied Echo ONNX model."""

    INFERENCE_BYTES = 1280 * 2  # 80 ms at 16 kHz, mono, int16

    def __init__(self, model_path: str | None = None) -> None:
        configured_path = os.path.expandvars(
            (model_path or os.environ.get("ECHO_WAKE_MODEL_PATH", "")).strip()
        )
        self.model_path = Path(configured_path).expanduser() if configured_path else None
        self.threshold = float(os.environ.get("ECHO_WAKE_THRESHOLD", "0.50"))
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
            import openwakeword
            from openwakeword.model import Model
        except ImportError as exc:
            raise RuntimeError(
                "A custom Echo wake model is configured, but openWakeWord is not installed. "
                "Install it with: python -m pip install openwakeword"
            ) from exc

        mel_path, embedding_path = self._ensure_feature_models(openwakeword)
        self._numpy = np
        self.model = Model(
            wakeword_models=[str(self.model_path)],
            inference_framework="onnx",
            # WebRTC VAD in Echo gates every candidate, so openWakeWord's
            # optional Silero VAD is disabled to avoid an extra model download.
            vad_threshold=0.0,
            melspec_model_path=str(mel_path),
            embedding_model_path=str(embedding_path),
        )
        log.info(
            "Loaded local openWakeWord model %s (threshold=%.2f)",
            self.model_path,
            self.threshold,
        )

    @staticmethod
    def _ensure_feature_models(openwakeword_module) -> tuple[Path, Path]:
        """Download only the two ONNX feature models required for inference."""
        target_dir = app_data_dir() / "models" / "openwakeword"
        target_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        for name, metadata in openwakeword_module.FEATURE_MODELS.items():
            target = target_dir / (Path(metadata["model_path"]).stem + ".onnx")
            if not target.is_file():
                url = str(metadata["download_url"]).replace(".tflite", ".onnx")
                temporary = target.with_suffix(target.suffix + ".download")
                log.info("Downloading openWakeWord feature model: %s", name)
                try:
                    urlretrieve(url, temporary)
                    if not temporary.is_file() or temporary.stat().st_size == 0:
                        raise RuntimeError("downloaded model file is empty")
                    temporary.replace(target)
                except Exception as exc:
                    temporary.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Could not download openWakeWord feature model '{name}' from the "
                        "official release. Check your internet connection and retry."
                    ) from exc
            paths[name] = target

        return paths["melspectrogram"], paths["embedding"]

    @property
    def enabled(self) -> bool:
        return self.model is not None

    def reset(self) -> None:
        self._buffer.clear()
        self._scores.clear()
        if self.model is not None:
            self.model.reset()

    def process(self, pcm16: bytes) -> tuple[bool, float] | None:
        """Feed 10 ms mic frames; return after each 80 ms model inference batch."""
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

        # Require two of the last three scores to clear the threshold.
        hits = sum(value >= self.threshold for value in self._scores)
        detected = len(self._scores) == self._scores.maxlen and hits >= 2
        return detected, score
