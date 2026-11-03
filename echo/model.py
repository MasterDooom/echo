from __future__ import annotations

import urllib.request
import zipfile
from pathlib import Path

from .config import model_dir

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"


def ensure_model(on_progress=None) -> Path:
    """Download and unpack the small English Vosk model on first launch."""
    destination = model_dir()
    if (destination / "am").is_dir() and (destination / "conf").is_dir():
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    archive = destination.parent / "vosk-model-small-en-us-0.15.zip"
    if on_progress:
        on_progress("Downloading offline speech model (about 40 MB)")
    urllib.request.urlretrieve(MODEL_URL, archive)
    if on_progress:
        on_progress("Installing speech model")

    with zipfile.ZipFile(archive) as zf:
        root = destination.parent.resolve()
        for member in zf.infolist():
            target = (destination.parent / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError("Unsafe path in speech model archive")
        zf.extractall(destination.parent)
    archive.unlink(missing_ok=True)

    if not (destination / "am").is_dir() or not (destination / "conf").is_dir():
        raise RuntimeError("The Vosk model archive did not contain the expected model files")
    return destination
