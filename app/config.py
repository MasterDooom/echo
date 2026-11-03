"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    vosk_model_path: Path
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    tts_enabled: bool
    command_timeout: float
    log_level: str

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ROOT_DIR / ".env")
        raw_path = Path(
            os.getenv("VOSK_MODEL_PATH", "models/vosk-model-small-en-us-0.15")
        ).expanduser()
        model_path = raw_path if raw_path.is_absolute() else ROOT_DIR / raw_path

        return cls(
            vosk_model_path=model_path,
            spotify_client_id=os.getenv("SPOTIPY_CLIENT_ID", "").strip(),
            spotify_client_secret=os.getenv("SPOTIPY_CLIENT_SECRET", "").strip(),
            spotify_redirect_uri=os.getenv(
                "SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
            ).strip(),
            tts_enabled=os.getenv("ECHO_TTS_ENABLED", "true").lower()
            in {"1", "true", "yes", "on"},
            command_timeout=max(1.0, float(os.getenv("ECHO_COMMAND_TIMEOUT", "4.5"))),
            log_level=os.getenv("ECHO_LOG_LEVEL", "INFO").upper(),
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.vosk_model_path.is_dir():
            problems.append(f"Vosk model directory not found: {self.vosk_model_path}")
        if not self.spotify_client_id or not self.spotify_client_secret:
            problems.append("Spotify client ID/secret are missing")
        return problems
