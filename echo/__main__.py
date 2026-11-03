from __future__ import annotations

import argparse
import logging
import queue
import sys

from . import config


def main() -> int:
    config.load_env()
    parser = argparse.ArgumentParser(description="Echo — a tiny voice controller for Spotify")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--spotify-login", action="store_true", help="connect your Spotify account")
    actions.add_argument("--download-model", action="store_true", help="download the offline speech model")
    args = parser.parse_args()

    try:
        if args.spotify_login:
            from .spotify import SpotifyClient

            SpotifyClient.login()
            return 0
        if args.download_model:
            from .model import ensure_model

            print(f"Speech model ready: {ensure_model()}")
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
