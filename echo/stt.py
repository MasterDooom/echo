from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from .config import app_data_dir

log = logging.getLogger(__name__)

# Small is a deliberate accuracy/latency trade-off for South Asian English.
# It is still local and CPU-int8; users can override it in .env.
MODEL_NAME = os.environ.get("ECHO_STT_MODEL", "small.en").strip() or "small.en"
INITIAL_PROMPT = (
    "This is a short voice command to a music assistant named Echo. "
    "Spotify commands include play, pause, resume, skip, next, previous, "
    "add to queue, queue, playlist, shuffle, repeat, volume, mute and seek. "
    "The speaker uses Indian English and may name songs, artists, albums or playlists. "
    "Transcribe the complete words and names that were actually spoken."
)
HOTWORDS = (
    "Echo, Spotify, queue, add to queue, playlist, play, pause, resume, "
    "skip, next track, previous track, shuffle, repeat, volume, mute, seek"
)


class CommandRecognizer:
    """Local Whisper transcription for short voice commands."""

    def __init__(self) -> None:
        self.model = None
        self.model_name = MODEL_NAME
        self.last_diagnostics: dict[str, float | int | str] = {}
        self.last_segment_diagnostics: list[dict[str, float | str]] = []

    @property
    def download_root(self) -> Path:
        path = app_data_dir() / "models" / "faster-whisper"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def warm(self) -> None:
        if self.model is not None:
            return
        from faster_whisper import WhisperModel

        log.info("Loading local speech model %s", self.model_name)
        self.model = WhisperModel(
            self.model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=max(2, min(4, os.cpu_count() or 4)),
            num_workers=1,
            download_root=str(self.download_root),
        )
        log.info("Local speech model %s is ready", self.model_name)

    @staticmethod
    def _prepare_audio(frames: list[bytes]):
        import numpy as np

        audio_bytes = b"".join(frames)
        if len(audio_bytes) < 6400:  # Under 200 ms of 16 kHz mono int16.
            return None

        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        audio -= float(np.mean(audio))

        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        if rms < 0.0015 or peak < 0.006:
            return None

        # Use conservative peak-safe normalization. This helps lower-volume
        # laptop microphone input without aggressively boosting room noise.
        target_rms = 0.075
        # Raise quiet laptop-mic speech more assertively, but cap gain by the
        # actual peak to avoid clipping consonants and artist/song names.
        peak_safe_gain = 0.95 / max(peak, 0.001)
        gain = min(8.0, target_rms / max(rms, 0.001), peak_safe_gain)
        audio = np.clip(audio * gain, -0.98, 0.98).astype(np.float32)
        return audio, rms, peak, len(audio) / 16000.0

    def transcribe_frames(
        self,
        frames: Iterable[bytes],
        *,
        context_bias: bool = True,
    ) -> str:
        prepared = self._prepare_audio(list(frames))
        if prepared is None:
            self.last_diagnostics = {"reason": "audio_too_short_or_quiet"}
            log.info("Speech capture was too short or too quiet")
            return ""

        audio, input_rms, input_peak, duration = prepared
        self.warm()

        # Do not run a second VAD pass here: capture already uses microphone
        # VAD and clips the utterance. Double filtering disproportionately
        # hurts short, quiet consonants and accented speech.
        segments, info = self.model.transcribe(
            audio,
            language="en",
            task="transcribe",
            beam_size=5,
            patience=1.1,
            initial_prompt=INITIAL_PROMPT if context_bias else None,
            hotwords=HOTWORDS if context_bias else None,
            condition_on_previous_text=False,
            temperature=0.0,
            vad_filter=False,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )

        accepted = []
        segment_diagnostics = []
        # faster-whisper segments are lazy; iterate to complete transcription.
        for segment in segments:
            text = segment.text.strip()
            segment_diagnostics.append({
                "text": text,
                "no_speech_prob": float(segment.no_speech_prob),
                "avg_logprob": float(segment.avg_logprob),
            })
            # no_speech_prob alone is not a sufficient reason to discard a
            # segment: Whisper also considers average log probability.
            if text:
                accepted.append(text)

        transcript = " ".join(accepted).strip()
        self.last_segment_diagnostics = segment_diagnostics
        average_logprob = (
            sum(item["avg_logprob"] for item in segment_diagnostics) / len(segment_diagnostics)
            if segment_diagnostics else -10.0
        )
        no_speech_probability = (
            sum(item["no_speech_prob"] for item in segment_diagnostics) / len(segment_diagnostics)
            if segment_diagnostics else 1.0
        )
        self.last_diagnostics = {
            "model": self.model_name,
            "duration_seconds": round(duration, 2),
            "input_rms": round(input_rms, 5),
            "input_peak": round(input_peak, 5),
            "language": getattr(info, "language", "en") or "en",
            "language_probability": round(float(getattr(info, "language_probability", 0.0)), 3),
            "segments": len(segment_diagnostics),
            "avg_logprob": round(float(average_logprob), 3),
            "no_speech_probability": round(float(no_speech_probability), 3),
            "context_bias": context_bias,
            "transcript": transcript,
        }
        log.info("Speech diagnostics: %s", self.last_diagnostics)
        log.debug("Speech segment diagnostics: %s", segment_diagnostics)
        return transcript
