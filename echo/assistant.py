from __future__ import annotations

import json
import logging
import re
import queue
import threading
import time
from collections import deque
from typing import Any

from vosk import KaldiRecognizer, Model

from .audio import Microphone
from .commands import parse_command
from .model import ensure_model
from .spotify import SpotifyClient
from .stt import CommandRecognizer
from .wake import (
    OpenWakeWordDetector,
    WakeWordStabilizer,
    wake_candidate_detected,
    wake_word_detected,
)

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
FRAME_LENGTH = 160
PRE_ROLL_FRAMES = 100  # 1 second; protects the first syllables after "Echo".
WAKE_GRAMMAR = json.dumps(["echo", "ayko", "aiko", "hey echo", "[unk]"])

COMMAND_WINDOW_SECONDS = 10.0
MIN_CAPTURE_SECONDS = 0.55
NO_COMMAND_WAIT_SECONDS = 4.0
VAD_THRESHOLD = 0.20
WAKE_RMS_FALLBACK = 0.006
WAKE_VERIFY_POSTROLL_FRAMES = 45  # 450 ms after a candidate, before Spotify is touched.
WAKE_REJECT_COOLDOWN_SECONDS = 1.25
WAKE_REQUIRED_HITS = 4
END_SILENCE_FRAMES = 90  # 900 ms, so a natural pause does not clip the final word.


class EchoAssistant(threading.Thread):
    def __init__(self, events: queue.Queue, *, daemon: bool = True) -> None:
        super().__init__(name="EchoAssistant", daemon=daemon)
        self.events = events
        self.shutdown = threading.Event()
        # Privacy mode closes the PortAudio input stream and releases the device.
        self.paused = threading.Event()
        self.microphone: Microphone | None = None

    def emit(self, kind: str, value: str) -> None:
        self.events.put((kind, value))

    def stop(self) -> None:
        self.shutdown.set()

    def toggle_paused(self) -> None:
        if self.shutdown.is_set() or not self.is_alive():
            self.emit("status", "error")
            self.emit("message", "Echo is not running. Fix setup, then restart it.")
            return
        if self.paused.is_set():
            self.paused.clear()
            self.emit("message", "Privacy mode off · reopening microphone")
        else:
            self.paused.set()
            self.emit("status", "privacy")
            self.emit("message", "Privacy mode on · releasing microphone")

    @staticmethod
    def _wake_candidate(recognizer: KaldiRecognizer, raw: bytes) -> bool:
        """Vosk is only a cheap candidate generator; it cannot wake Spotify alone."""
        if recognizer.AcceptWaveform(raw):
            text = json.loads(recognizer.Result()).get("text", "")
        else:
            text = json.loads(recognizer.PartialResult()).get("partial", "")
        return wake_candidate_detected(text)

    def _speech_active(self, processed_audio: bytes) -> tuple[bool, float, float]:
        """Return VAD gate, VAD score and RMS for the current 10 ms frame."""
        assert self.microphone is not None
        samples = self.microphone._numpy.frombuffer(
            processed_audio, dtype=self.microphone._numpy.int16
        ).astype(self.microphone._numpy.float32)
        rms = float(self.microphone._numpy.sqrt(
            self.microphone._numpy.mean(samples * samples)
        )) / 32768.0
        probability = float(self.microphone.speech_probability)
        # Energy is only a quiet-speech fallback when WebRTC also sees some
        # speech. Energy alone would let music/noise open the wake gate.
        active = probability >= VAD_THRESHOLD or (
            probability >= 0.08 and rms >= WAKE_RMS_FALLBACK
        )
        return active, probability, rms

    def _verify_wake(
        self,
        recognizer: CommandRecognizer,
        seed_frames: list[bytes],
    ) -> tuple[bool, list[bytes], str]:
        """Verify a candidate with unbiased local Whisper before pausing Spotify."""
        assert self.microphone is not None
        frames = list(seed_frames)
        for _ in range(WAKE_VERIFY_POSTROLL_FRAMES):
            if self.shutdown.is_set() or self.paused.is_set():
                return False, frames, ""
            pair = self.microphone.read_pair(timeout=0.15)
            if pair is not None:
                _processed, original = pair
                frames.append(original)

        transcript = recognizer.transcribe_frames(frames, context_bias=False)
        diagnostics = recognizer.last_diagnostics
        avg_logprob = float(diagnostics.get("avg_logprob", -10.0))
        no_speech = float(diagnostics.get("no_speech_probability", 1.0))
        # Wake verification must not depend on successful command parsing.
        # A real wake followed by a misunderstood command still deserves a
        # listening window; command failure must never become a false wake.
        verified = (
            wake_word_detected(transcript)
            and avg_logprob >= -1.0
            and no_speech <= 0.65
        )
        log.info(
            "Wake verification: accepted=%s transcript=%r avg_logprob=%.3f "
            "no_speech=%.3f frames=%d",
            verified,
            transcript,
            avg_logprob,
            no_speech,
            len(frames),
        )
        return verified, frames, transcript

    def _restore_playback(self, spotify: SpotifyClient, snapshot: dict[str, Any]) -> None:
        if not snapshot or not snapshot.get("was_playing"):
            return
        try:
            spotify.resume_after_command(snapshot)
        except Exception:
            log.exception("Could not restore Spotify after listening")
            self.emit(
                "message",
                "Echo couldn't resume Spotify automatically. Check the Spotify device.",
            )

    def _open_microphone(self) -> Microphone:
        microphone = Microphone(SAMPLE_RATE, FRAME_LENGTH)
        microphone.start()
        return microphone

    def _release_microphone(self) -> None:
        """Close and forget the input stream so Windows releases the device."""
        microphone = self.microphone
        self.microphone = None
        if microphone is not None:
            microphone.close()

    def run(self) -> None:
        try:
            self.emit("status", "loading")

            wake_stabilizer = WakeWordStabilizer(WAKE_REQUIRED_HITS)
            open_wake = OpenWakeWordDetector()
            wake: KaldiRecognizer | None = None
            if not open_wake.enabled:
                # Transitional local fallback: strict Vosk candidate detection
                # is followed by independent Whisper verification. Once a
                # custom Echo ONNX model is configured, Vosk is not loaded.
                wake_model_path = ensure_model(
                    lambda message: self.emit("message", message)
                )
                wake_model = Model(str(wake_model_path))
                wake = KaldiRecognizer(wake_model, SAMPLE_RATE, WAKE_GRAMMAR)
                wake.SetWords(False)

            spotify = SpotifyClient()
            spotify._access_token()
            spotify.require_scopes()

            command_recognizer = CommandRecognizer()
            # Warm once so the first wake verification does not stall while
            # the user is already speaking. The same local model handles STT.
            command_recognizer.warm()

            self.microphone = self._open_microphone()
            self.emit("status", "listening")
            if open_wake.enabled:
                self.emit("message", "Ready · local Echo wake model active")
                log.info("Wake backend: openWakeWord (%s)", open_wake.model_path)
            else:
                self.emit("message", "Ready · local wake verification active")
                log.warning(
                    "No ECHO_WAKE_MODEL_PATH configured; using Vosk candidate "
                    "detection with VAD and independent Whisper verification"
                )

            history: deque[bytes] = deque(maxlen=PRE_ROLL_FRAMES)
            rejected_until = 0.0
            while not self.shutdown.is_set():
                if self.paused.is_set():
                    self._release_microphone()
                    history.clear()
                    if wake is not None:
                        wake.Reset()
                    wake_stabilizer.reset()
                    open_wake.reset()
                    self.emit("status", "privacy")
                    while self.paused.is_set() and not self.shutdown.is_set():
                        time.sleep(0.10)
                    if self.shutdown.is_set():
                        break
                    self.microphone = self._open_microphone()
                    history.clear()
                    if wake is not None:
                        wake.Reset()
                    wake_stabilizer.reset()
                    open_wake.reset()
                    self.emit("status", "listening")
                    self.emit("message", "Microphone active · local wake detection")
                    continue

                if self.microphone is None:
                    self.microphone = self._open_microphone()

                pair = self.microphone.read_pair(timeout=0.20)
                if pair is None:
                    continue
                wake_audio, original_audio = pair
                history.append(original_audio)

                # Always feed the streaming models, but require speech activity
                # and repeated detections before considering a wake candidate.
                speech_active, vad_score, frame_rms = self._speech_active(wake_audio)
                if open_wake.enabled:
                    scored = open_wake.process(wake_audio)
                    if scored is None:
                        continue
                    raw_candidate, wake_score = scored
                    candidate = raw_candidate and speech_active
                    if raw_candidate:
                        log.debug(
                            "Wake model score=%.3f vad=%.3f rms=%.4f",
                            wake_score,
                            vad_score,
                            frame_rms,
                        )
                else:
                    assert wake is not None
                    candidate = self._wake_candidate(wake, wake_audio) and speech_active
                    wake_score = 1.0 if candidate else 0.0

                if time.monotonic() < rejected_until:
                    wake_stabilizer.reset()
                    continue
                if not wake_stabilizer.update(candidate, speech_active):
                    continue

                # This is only a candidate. Do not pause Spotify or change
                # playback state until a separate local ASR pass confirms it.
                log.info(
                    "Wake candidate detected (score=%.3f, vad=%.3f, rms=%.4f)",
                    wake_score,
                    vad_score,
                    frame_rms,
                )
                wake_stabilizer.reset()
                seed = list(history)
                verified, verification_frames, verification_text = self._verify_wake(
                    command_recognizer, seed
                )
                history.clear()
                if wake is not None:
                    wake.Reset()
                open_wake.reset()

                if not verified:
                    log.info("Rejected false wake candidate: %r", verification_text)
                    rejected_until = time.monotonic() + WAKE_REJECT_COOLDOWN_SECONDS
                    if self.microphone is not None:
                        self.microphone.clear()
                    continue

                normalized_wake_text = re.sub(
                    r"[^a-z0-9]+", " ", verification_text.lower()
                ).strip()
                bare_wake = normalized_wake_text in {
                    "echo", "hey echo", "ayko", "aiko", "hey ayko", "hey aiko"
                }

                self.emit("status", "awake")
                playback_snapshot: dict[str, Any] = {
                    "was_playing": False,
                    "device_id": None,
                }
                restore_playback = True
                command = None

                try:
                    try:
                        # Only a verified wake phrase is allowed to interrupt
                        # Spotify. The active device is retained for restore.
                        playback_snapshot = spotify.pause_for_command()
                        if playback_snapshot.get("was_playing"):
                            self.emit("message", "Echo heard · listening")
                    except Exception as exc:
                        log.warning("Could not pause Spotify before command capture: %s", exc)
                        self.emit("message", f"Spotify pause failed: {exc}")

                    command_started_with_wake = not bare_wake
                    # The first second of the wake candidate may contain
                    # Spotify audio. Keep only the post-candidate tail for
                    # command transcription; it also preserves the first
                    # syllable if the user speaks straight through.
                    command_seed = verification_frames[-WAKE_VERIFY_POSTROLL_FRAMES:]
                    frames = self._capture_command(
                        command_seed,
                        command_already_started=command_started_with_wake,
                    )
                    if not frames:
                        self.emit("message", "I heard Echo, but no command followed.")
                    else:
                        self.emit("status", "working")
                        transcript = command_recognizer.transcribe_frames(frames)
                        log.info(
                            "Recognized command transcript: %r diagnostics=%s",
                            transcript,
                            command_recognizer.last_diagnostics,
                        )
                        diagnostics = command_recognizer.last_diagnostics
                        avg_logprob = float(diagnostics.get("avg_logprob", -10.0))
                        no_speech = float(diagnostics.get("no_speech_probability", 1.0))
                        confidence_ok = bool(transcript) and avg_logprob >= -1.0 and no_speech <= 0.65
                        command = parse_command(transcript) if confidence_ok else None
                        if not confidence_ok:
                            self.emit(
                                "message",
                                "I didn't catch that clearly · Spotify will resume. Please repeat.",
                            )
                        elif command is None:
                            detail = f"I heard: {transcript[:90]}" if transcript else "I didn't catch the command"
                            self.emit("message", f"{detail} · Spotify will resume")
                        elif command.kind == "pause":
                            # Spotify was paused before capture; this is an
                            # intentional pause, so do not restore playback.
                            restore_playback = False
                            self.emit("message", "Paused")
                        else:
                            log.info(
                                "Parsed command: %s query=%r artist=%r mode=%r target=%r",
                                command.kind,
                                command.query,
                                command.artist,
                                command.mode,
                                command.target_type,
                            )
                            result = spotify.execute(
                                command,
                                device_id=playback_snapshot.get("device_id"),
                            )
                            self.emit("message", f"{result} · heard: {transcript[:45]}")
                            if command.kind in {"play", "resume"}:
                                # These commands intentionally establish a new
                                # playback state; do not resume the old track.
                                restore_playback = False
                except Exception as exc:
                    log.exception("Command failed")
                    self.emit("message", str(exc))
                finally:
                    # Single restoration point: invalid intents, empty audio,
                    # transcription errors and Spotify exceptions all resume.
                    if restore_playback:
                        self._restore_playback(spotify, playback_snapshot)

                if not self.paused.is_set():
                    self.emit("status", "listening")

        except Exception as exc:
            log.exception("Echo stopped")
            self.emit("status", "error")
            self.emit("message", str(exc))
        finally:
            try:
                self._release_microphone()
            except Exception:
                log.exception("Could not close microphone")

    def _capture_command(
        self,
        pre_roll: list[bytes],
        *,
        command_already_started: bool = False,
    ) -> list[bytes]:
        """Capture a command after verified wake detection and optional Spotify pause."""
        assert self.microphone is not None
        frames = list(pre_roll)
        # A bare "Echo" starts the Alexa-style command window but is not itself
        # the command. A continuous "Echo, play ..." utterance is already in
        # progress and must not be clipped.
        speech_started = command_already_started
        trailing_silence = 0
        started_at = time.monotonic()
        deadline = started_at + COMMAND_WINDOW_SECONDS


        while (
            not self.shutdown.is_set()
            and not self.paused.is_set()
            and time.monotonic() < deadline
        ):
            pair = self.microphone.read_pair(timeout=0.15)
            if pair is None:
                continue
            _processed_audio, original_audio = pair
            frames.append(original_audio)

            probability = float(self.microphone.speech_probability)
            samples = self.microphone._numpy.frombuffer(
                original_audio, dtype=self.microphone._numpy.int16
            ).astype(self.microphone._numpy.float32)
            rms = float(self.microphone._numpy.sqrt(
                self.microphone._numpy.mean(samples * samples)
            )) / 32768.0
            speech_detected = probability >= VAD_THRESHOLD or (
                probability >= 0.08 and rms >= WAKE_RMS_FALLBACK
            )
            if speech_detected:
                speech_started = True
                trailing_silence = 0
            elif speech_started:
                trailing_silence += 1

            elapsed = time.monotonic() - started_at
            if (
                speech_started
                and elapsed >= MIN_CAPTURE_SECONDS
                and trailing_silence >= END_SILENCE_FRAMES
            ):
                break
            if not speech_started and elapsed >= NO_COMMAND_WAIT_SECONDS:
                return []

        return frames if speech_started and not self.paused.is_set() else []
