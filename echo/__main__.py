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
