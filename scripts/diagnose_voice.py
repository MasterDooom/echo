from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from echo.audio import Microphone
from echo.config import load_env, model_dir
from echo.wake import wake_word_detected
from vosk import KaldiRecognizer, Model

WAKE_GRAMMAR = json.dumps(["echo", "hey echo", "[unk]"])


def main() -> int:
    load_env()
    path = model_dir()
    if not (path / "am").is_dir():
        print(f"Vosk wake model not found: {path}")
        return 1

    print("Loading local wake-word model...")
    model = Model(str(path))
    microphone = Microphone(sample_rate=16000, frame_length=160)
    recognizer = KaldiRecognizer(model, 16000, WAKE_GRAMMAR)
    recognizer.SetWords(False)

    try:
        microphone.start()
        print(f"Microphone device: {microphone.device}")
        print("\nLeave Spotify playing. Stay silent for 5 seconds.")
        print("Then say 'Echo' or 'Echo next'.")
        print("This diagnostic never prints general speech or song lyrics.\n")

        deadline = time.monotonic() + 15
        silent_phase_end = time.monotonic() + 5
        detections = 0
        last_detection = 0.0

        while time.monotonic() < deadline:
            frame = microphone.read(timeout=0.25)
            if frame is None:
                continue

            if recognizer.AcceptWaveform(frame):
                text = json.loads(recognizer.Result()).get("text", "")
            else:
                text = json.loads(recognizer.PartialResult()).get("partial", "")

            now = time.monotonic()
            if wake_word_detected(text) and now - last_detection > 1.5:
                detections += 1
                last_detection = now
                phase = "during Spotify playback" if now < silent_phase_end else "after your voice command"
                print(f"WAKE DETECTED ({phase})")
                recognizer.Reset()

        if detections == 0:
            print("\nNo wake-word detections. Check microphone permission and speak clearly.")
        else:
            print(f"\nTotal wake detections: {detections}")
        print("Diagnostic complete.")
        return 0
    finally:
        microphone.close()


if __name__ == "__main__":
    raise SystemExit(main())
