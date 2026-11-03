from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Load simple KEY=VALUE settings from the project .env without dependencies."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "Echo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_dir() -> Path:
    return app_data_dir() / "models" / "vosk-model-small-en-us-0.15"


def spotify_client_id() -> str:
    return os.environ.get("SPOTIFY_CLIENT_ID", "").strip()


def spotify_redirect_uri() -> str:
    return os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip()
