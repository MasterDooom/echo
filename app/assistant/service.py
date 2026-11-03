"""Voice-assistant orchestration."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.assistant.commands import Action, parse_command
from app.audio.listener import VoiceListener
from app.config import Settings
from app.spotify.client import SpotifyController

LOGGER = logging.getLogger(__name__)


class AssistantService:
    def __init__(
        self,
        settings: Settings,
        set_status: Callable[[str, str], None],
        should_stop: threading.Event,
    ) -> None:
        self.settings = settings
        self.set_status = set_status
        self.should_stop = should_stop
        self.spotify: SpotifyController | None = None
        self.listener: VoiceListener | None = None
        self.speaker = None

    def _speak(self, text: str) -> None:
        if not self.settings.tts_enabled:
            return
        try:
            if self.speaker is None:
                import pyttsx3

                self.speaker = pyttsx3.init()
                self.speaker.setProperty("rate", 185)
                self.speaker.setProperty("volume", 0.85)
            self.speaker.say(text)
            self.speaker.runAndWait()
        except Exception:
            LOGGER.exception("Text-to-speech failed")

    def _execute(self, transcript: str) -> None:
        command = parse_command(transcript)
        if command.action is Action.UNKNOWN:
            self.set_status("Didn't catch that", "idle")
            return

        assert self.spotify is not None
        self.set_status("Working", "working")
        try:
            if command.action is Action.PLAY:
                title, artists = self.spotify.play(command.query)
                response = f"Playing {title} by {artists}" if artists else f"Playing {title}"
            elif command.action is Action.NEXT:
                self.spotify.next()
                response = "Next track"
            elif command.action is Action.PREVIOUS:
                self.spotify.previous()
                response = "Previous track"
            elif command.action is Action.PAUSE:
                self.spotify.pause()
                response = "Paused"
            elif command.action is Action.RESUME:
                self.spotify.resume()
                response = "Resumed"
            elif command.action is Action.VOLUME_UP:
                response = f"Volume {self.spotify.change_volume(10)} percent"
            elif command.action is Action.VOLUME_DOWN:
                response = f"Volume {self.spotify.change_volume(-10)} percent"
            elif command.action is Action.MUTE:
                self.spotify.mute()
                response = "Muted"
            elif command.action is Action.WHAT_IS_PLAYING:
                track = self.spotify.currently_playing()
                item = track.get("item") if track else None
                if not item:
                    response = "Nothing is playing"
                else:
                    artists = ", ".join(a["name"] for a in item.get("artists", []))
                    response = f"Playing {item.get('name')} by {artists}"
            else:
                response = "I don't know that command"

            self.set_status(response[:18], "idle")
            self._speak(response)
        except Exception as error:
            LOGGER.warning("Command failed: %s", error)
            response = SpotifyController.user_message(error)
            self.set_status("Spotify unavailable", "error")
            self._speak(response)

    def run(self) -> None:
        problems = self.settings.validate()
        if problems:
            LOGGER.error("Setup incomplete: %s", "; ".join(problems))
            self.set_status("Setup needed", "error")
            return

        try:
            self.set_status("Connecting", "working")
            self.spotify = SpotifyController(
                self.settings.spotify_client_id,
                self.settings.spotify_client_secret,
                self.settings.spotify_redirect_uri,
            )
            self.listener = VoiceListener(
                vosk_model_path=self.settings.vosk_model_path,
                command_timeout=self.settings.command_timeout,
            )
            self.listener.start()
            self.set_status("Ready", "idle")

            while not self.should_stop.is_set():
                if not self.listener.wait_for_wake_word(self.should_stop.is_set):
                    break

                self.set_status("Listening", "listening")
                transcript = self.listener.transcribe_command()
                command = parse_command(transcript)
                # Support both "Echo next" and "Echo" followed by a short command.
                if command.action is Action.UNKNOWN:
                    self.set_status("Your command", "listening")
                    transcript = self.listener.transcribe_command(timeout=4.0)
                if transcript:
                    self._execute(transcript)
                self.set_status("Ready", "idle")
        except Exception:
            LOGGER.exception("Echo stopped unexpectedly")
            self.set_status("Check setup", "error")
        finally:
            if self.listener is not None:
                self.listener.close()
            if self.speaker is not None:
                try:
                    self.speaker.stop()
                except Exception:
                    LOGGER.debug("Could not stop speech engine", exc_info=True)
