from __future__ import annotations

import argparse
import logging
import queue
import sys

from . import config


def main() -> int:
    config.load_env()
    parser = argparse.ArgumentParser(
        description="Echo — a lightweight local voice controller for Spotify"
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--spotify-login", action="store_true", help="connect or update Spotify permissions")
    actions.add_argument("--download-model", action="store_true", help="download local speech models")
    actions.add_argument("--refresh-playlists", action="store_true", help="refresh the local Spotify playlist cache")
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
            print("Downloading/loading the local command-recognition model...")
            CommandRecognizer().warm()
            print("Command model ready. Both speech models are stored locally.")
            return 0

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
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
