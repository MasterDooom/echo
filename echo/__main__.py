from __future__ import annotations

import argparse
import logging
import queue
import sys
import time

from . import config


def _diagnose_command() -> int:
    """Record one short utterance in RAM and print its local transcript/intent."""
    from .audio import Microphone
    from .commands import parse_command
    from .stt import CommandRecognizer

    microphone = Microphone(sample_rate=16000, frame_length=160)
    frames: list[bytes] = []
    speech_started = False
    silence_frames = 0
    start = time.monotonic()
    deadline = start + 8.0

    print("Speak one complete command now (for example: 'play Blinding Lights by The Weeknd').")
    print("Pause Spotify for this diagnostic. Audio stays in RAM and is not saved.")
    try:
        microphone.start()
        print(f"Using microphone device: {microphone.device}")
        while time.monotonic() < deadline:
            pair = microphone.read_pair(timeout=0.2)
            if pair is None:
                continue
            _processed, raw = pair
            frames.append(raw)

            import numpy as np

            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples))) / 32768.0
            voice = microphone.speech_probability >= 0.20 or rms >= 0.005
            if voice:
                speech_started = True
                silence_frames = 0
            elif speech_started:
                silence_frames += 1
            if speech_started and len(frames) >= 100 and silence_frames >= 70:
                break
    finally:
        microphone.close()

    if not speech_started:
        print("No speech activity detected. Check the selected microphone and Windows microphone permissions.")
        return 2

    recognizer = CommandRecognizer()
    transcript = recognizer.transcribe_frames(frames)
    command = parse_command(transcript)

    print(f"\nTranscript: {transcript!r}")
    print(f"Intent: {command!r}")
    print(f"Audio/model diagnostics: {recognizer.last_diagnostics}")
    return 0 if transcript else 3


def _diagnose_wake() -> int:
    """Test wake candidates and the second-stage local verifier without Spotify."""
    import json
    from collections import deque

    from vosk import KaldiRecognizer, Model

    from .audio import Microphone
    from .model import ensure_model
    from .stt import CommandRecognizer
    from .wake import (
        OpenWakeWordDetector,
        WakeWordStabilizer,
        wake_candidate_detected,
        wake_word_detected,
    )

    sample_rate = 16000
    frame_length = 160
    required_hits = 4
    postroll_frames = 45
    threshold = 0.20
    stabilizer = WakeWordStabilizer(required_hits)
    open_wake = OpenWakeWordDetector()
    wake = None

    if not open_wake.enabled:
        model = Model(str(ensure_model()))
        wake = KaldiRecognizer(
            model,
            sample_rate,
            json.dumps(["echo", "ayko", "aiko", "hey echo", "[unk]"]),
        )
        wake.SetWords(False)
        backend = "Vosk candidate + local Whisper verification"
    else:
        backend = f"openWakeWord ({open_wake.model_path}) + local Whisper verification"

    recognizer = CommandRecognizer()
    recognizer.warm()
    microphone = Microphone(sample_rate=sample_rate, frame_length=frame_length)
    history: deque[bytes] = deque(maxlen=100)
    vad_history: deque[bool] = deque(maxlen=8)
    candidates = 0
    accepted = 0
    rejected = 0
    deadline = time.monotonic() + 60.0

    print(f"Wake diagnostic · backend: {backend}")
    print("Speak normally for a few seconds, then say: 'Echo, play Starboy'.")
    print("No Spotify commands will run. Audio stays in RAM. Press Ctrl+C to stop.")
    try:
        microphone.start()
        print(f"Microphone device: {microphone.device}")
        while time.monotonic() < deadline:
            pair = microphone.read_pair(timeout=0.20)
            if pair is None:
                continue
            processed, original = pair
            history.append(original)

            import numpy as np

            samples = np.frombuffer(processed, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples))) / 32768.0
            vad = float(microphone.speech_probability)
            speech_active = vad >= threshold or (vad >= 0.08 and rms >= 0.006)
            vad_history.append(speech_active)

            if open_wake.enabled:
                scored = open_wake.process(processed)
                if scored is None:
                    continue
                raw_candidate, score = scored
                chunk_speech_active = sum(vad_history) >= 3
                candidate = raw_candidate and chunk_speech_active
                stabilizer_speech_active = chunk_speech_active
            else:
                if wake is None:
                    continue
                if wake.AcceptWaveform(processed):
                    text = json.loads(wake.Result()).get("text", "")
                else:
                    text = json.loads(wake.PartialResult()).get("partial", "")
                candidate = wake_candidate_detected(text) and speech_active
                score = 1.0 if candidate else 0.0
                stabilizer_speech_active = speech_active

            if not stabilizer.update(candidate, stabilizer_speech_active):
                continue

            candidates += 1
            seed = list(history)
            tail: list[bytes] = []
            for _ in range(postroll_frames):
                next_pair = microphone.read_pair(timeout=0.15)
                if next_pair is not None:
                    tail.append(next_pair[1])

            transcript = recognizer.transcribe_frames(
                seed + tail,
                context_bias=False,
            )
            verified = (
                wake_word_detected(transcript)
                and float(recognizer.last_diagnostics.get("avg_logprob", -10.0)) >= -1.0
                and float(recognizer.last_diagnostics.get("no_speech_probability", 1.0)) <= 0.65
            )
            if verified:
                accepted += 1
                print(f"WAKE ACCEPTED  score={score:.3f}  transcript={transcript!r}")
            else:
                rejected += 1
                print(f"FALSE CANDIDATE score={score:.3f}  transcript={transcript!r}")

            stabilizer.reset()
            history.clear()
            vad_history.clear()
            if wake is not None:
                wake.Reset()
            open_wake.reset()
            microphone.clear()

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        microphone.close()

    print(
        f"Summary: {candidates} candidates, {accepted} confirmed, "
        f"{rejected} rejected during this session."
    )
    return 0


def main() -> int:
    config.load_env()
    parser = argparse.ArgumentParser(
        description="Echo — a lightweight local voice controller for Spotify"
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--spotify-login", action="store_true", help="connect or update Spotify permissions")
    actions.add_argument("--download-model", action="store_true", help="download local speech models")
    actions.add_argument("--refresh-playlists", action="store_true", help="refresh the local Spotify playlist cache")
    actions.add_argument("--test-speech", action="store_true", help="test local microphone transcription without Spotify")
    actions.add_argument("--test-wake", action="store_true", help="test local wake candidates and verification without Spotify")
    args = parser.parse_args()

    try:
        if args.spotify_login:
            from .spotify import SpotifyClient

            SpotifyClient.login()
            return 0

        if args.download_model:
            from .model import ensure_model
            from .stt import CommandRecognizer

            print(f"Wake-word model ready: {ensure_model()}")
            recognizer = CommandRecognizer()
            print(f"Downloading/loading local command model ({recognizer.model_name})...")
            recognizer.warm()
            print("Command model ready. Speech recognition stays on this PC.")
            return 0

        if args.test_speech:
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
            return _diagnose_command()

        if args.test_wake:
            logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
            return _diagnose_wake()

        if args.refresh_playlists:
            from .spotify import SpotifyClient

            spotify = SpotifyClient()
            spotify._access_token()
            spotify.require_scopes()
            playlists = spotify.list_playlists(refresh=True)
            print(f"Cached {len(playlists)} Spotify playlists:")
            for playlist in playlists:
                print(f"  - {playlist.get('name', 'Untitled')}")
            return 0

        from .assistant import EchoAssistant
        from .overlay import EchoOverlay

        logging.basicConfig(
            filename=config.app_data_dir() / "echo.log",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
        events: queue.Queue = queue.Queue()
        assistant = EchoAssistant(events)
        overlay = EchoOverlay(events, assistant.toggle_paused, assistant.stop)
        assistant.start()
        try:
            overlay.run()
        finally:
            assistant.stop()
            assistant.join(timeout=3)
        return 0
    except Exception as exc:
        print(f"Echo: {exc}", file=sys.stderr)
        logging.exception("Echo command failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
