from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from echo.audio import Microphone
from echo.config import load_env, model_dir
from vosk import KaldiRecognizer, Model


def main() -> int:
    load_env()
    path = model_dir()
    if not (path / "am").is_dir():
        print(f"Vosk model not found: {path}")
        return 1

    print("Loading Vosk model...")
    model = Model(str(path))
    microphone = Microphone(sample_rate=16000, frame_length=1600)
    print(f"Using microphone device: {microphone.device}")
    recognizer = KaldiRecognizer(model, 16000)
    recognizer.SetWords(False)
    last_partial = ""

    try:
        microphone.start()
        print("\nSpeak naturally for 12 seconds. Try saying: 'Echo next'.\n")
        import time

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            frame = microphone.read(timeout=0.5)
            if frame is None:
                continue
            if recognizer.AcceptWaveform(frame):
                text = json.loads(recognizer.Result()).get("text", "").strip()
                if text:
                    print(f"HEARD: {text}")
                last_partial = ""
            else:
                partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
                if partial and partial != last_partial:
                    print(f"HEARING: {partial}")
                    last_partial = partial

        final_text = json.loads(recognizer.FinalResult()).get("text", "").strip()
        if final_text:
            print(f"FINAL: {final_text}")
        print("\nDiagnostic complete.")
        return 0
    finally:
        microphone.close()


if __name__ == "__main__":
    raise SystemExit(main())
