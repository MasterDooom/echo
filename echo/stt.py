from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .config import app_data_dir

MODEL_NAME = os.environ.get("ECHO_STT_MODEL", "base.en").strip() or "base.en"
INITIAL_PROMPT = (
    "Echo, a voice assistant. Commands include next, skip, previous, pause, resume, "
    "play a song, volume up, volume down, mute, and unmute. Spotify song titles and artists."
)


class CommandRecognizer:
    """Higher-accuracy local transcription for the short command after wake."""

    def __init__(self) -> None:
        self.model = None
        self.model_name = MODEL_NAME

    @property
    def download_root(self) -> Path:
        path = app_data_dir() / "models" / "faster-whisper"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def warm(self) -> None:
        if self.model is not None:
            return
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            self.model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=max(2, min(4, os.cpu_count() or 4)),
            num_workers=1,
            download_root=str(self.download_root),
        )

    def transcribe_frames(self, frames: Iterable[bytes]) -> str:
        import numpy as np

        audio_bytes = b"".join(frames)
        if len(audio_bytes) < 3200:  # Less than 100 ms of 16 kHz mono int16.
            return ""

        self.warm()
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self.model.transcribe(
            audio,
            language="en",
            beam_size=3,
            initial_prompt=INITIAL_PROMPT,
            condition_on_previous_text=False,
            temperature=0.0,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.5,
                "min_speech_duration_ms": 120,
                "min_silence_duration_ms": 280,
                "speech_pad_ms": 120,
            },
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
        )

        accepted = []
        for segment in segments:
            text = segment.text.strip()
            if text and segment.no_speech_prob <= 0.65:
                accepted.append(text)
        return " ".join(accepted).strip()
