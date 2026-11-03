"""Echo desktop entry point."""

from __future__ import annotations

import logging
import threading

from app.assistant.service import AssistantService
from app.config import ROOT_DIR, Settings
from app.ui.overlay import StatusOverlay


def main() -> None:
    settings = Settings.load()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(ROOT_DIR / "echo.log", encoding="utf-8")],
    )

    stop_event = threading.Event()
    overlay = StatusOverlay(on_quit=stop_event.set)
    service = AssistantService(settings, overlay.set_status, stop_event)
    worker = threading.Thread(target=service.run, name="echo-assistant", daemon=True)
    worker.start()
    overlay.run()
    stop_event.set()
    worker.join(timeout=3)


if __name__ == "__main__":
    main()
