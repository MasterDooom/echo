from __future__ import annotations

import logging
import os
import queue

import sounddevice as sd

log = logging.getLogger(__name__)


class Microphone:
    """Mic capture with optional WASAPI loopback reference + WebRTC AEC3.

    The mic is captured by sounddevice. The Windows default render endpoint is
    captured through SoundCard/WASAPI loopback. SoundCard's COM-backed objects
    are created and used on the assistant thread.
    """

    def __init__(self, sample_rate: int = 16000, frame_length: int = 160) -> None:
        self.sample_rate = sample_rate
        self.frame_length = frame_length
        self.device = self._resolve_device()
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=128)
        self.stream = None
        self._loopback_recorder = None
        self._processor = None
        self.aec_enabled = False
        self.loopback_name: str | None = None
        self.warning: str | None = None
        self.speech_probability = 0.0

    @staticmethod
    def _resolve_device():
        requested = os.environ.get("ECHO_INPUT_DEVICE", "").strip()
        devices = sd.query_devices()
        if requested:
            if requested.isdigit():
                return int(requested)
            match = next(
                (i for i, item in enumerate(devices)
                 if requested.lower() in item["name"].lower()
                 and item["max_input_channels"] > 0),
                None,
            )
            if match is not None:
                return match

        candidates = []
        for index, item in enumerate(devices):
            if item["max_input_channels"] <= 0:
                continue
            host = sd.query_hostapis(item["hostapi"])["name"]
            is_default = index == sd.default.device[0]
            candidates.append((index, host, is_default))

        preferred = next(
            (index for index, host, is_default in candidates
             if host == "Windows DirectSound" and is_default),
            None,
        )
        if preferred is not None:
            return preferred
        return next(
            (index for index, _host, is_default in candidates if is_default),
            None,
        )

    def _open_audio_processor(self) -> None:
        import numpy as np
        from pywebrtc_audio import AudioProcessor

        self._numpy = np
        self._processor = AudioProcessor(
            sample_rate=self.sample_rate,
            num_channels=1,
            echo_cancellation=self.aec_enabled,
            noise_suppression=True,
            auto_gain_control=True,
            ns_level=2,
            agc_max_gain_db=18.0,
            stream_delay_ms=50,
        )

    def _open_loopback(self) -> None:
        """Open the current Windows output as the AEC far-end reference."""
        import soundcard as sc

        speaker = sc.default_speaker()
        loopback = sc.get_microphone(speaker.id, include_loopback=True)
        if not loopback.isloopback:
            raise RuntimeError("SoundCard did not return a WASAPI loopback input")
        if int(loopback.channels) < 2:
            raise RuntimeError("The Windows loopback endpoint did not expose stereo channels")

        # SoundCard documents a Windows issue with mono WASAPI recordings;
        # use the first stereo pair and downmix it for the mono AEC reference.
        recorder = loopback.recorder(
            samplerate=self.sample_rate,
            channels=2,
            blocksize=self.frame_length * 2,
        )
        recorder.__enter__()
        self._loopback_recorder = recorder
        self.loopback_name = speaker.name
        self.aec_enabled = True

    def start(self) -> None:
        def callback(indata, _frames, _time_info, status):
            if status:
                log.debug("Microphone stream status: %s", status)
            raw = bytes(indata)
            try:
                self.frames.put_nowait(raw)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                    self.frames.put_nowait(raw)
                except queue.Empty:
                    pass

        self.stream = sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.frame_length,
            channels=1,
            device=self.device,
            dtype="int16",
            callback=callback,
        )
        self.stream.start()

        try:
            self._open_loopback()
        except Exception as exc:
            self.aec_enabled = False
            self.warning = (
                "Speaker echo cancellation unavailable. Echo can still work, "
                "but use headphones while Spotify is playing."
            )
            log.warning("Could not open speaker loopback for echo cancellation: %s", exc)

        try:
            self._open_audio_processor()
            self.clear()  # Drop audio buffered while the two streams were starting.
        except Exception:
            self.close()
            raise

    def _disable_aec(self, reason: Exception) -> None:
        log.warning("Disabling AEC after loopback failure: %s", reason)
        if self._loopback_recorder is not None:
            try:
                self._loopback_recorder.__exit__(None, None, None)
            except Exception:
                log.debug("Could not close failed loopback recorder", exc_info=True)
            self._loopback_recorder = None
        self.aec_enabled = False
        self.warning = (
            "Speaker echo cancellation stopped. Use headphones while Spotify is playing."
        )
        self._open_audio_processor()

    def read(self, timeout: float = 0.25) -> bytes | None:
        try:
            raw = self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

        np = self._numpy
        near = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if self._loopback_recorder is not None:
            try:
                far_channels = self._loopback_recorder.record(self.frame_length)
                far = np.asarray(far_channels, dtype=np.float32)
                if far.ndim == 2:
                    far = far.mean(axis=1, dtype=np.float32)
                else:
                    far = far.reshape(-1)

                if len(far) < len(near):
                    far = np.pad(far, (0, len(near) - len(far)))
                elif len(far) > len(near):
                    far = far[:len(near)]
                clean = self._processor.process(near, far)
            except Exception as exc:
                self._disable_aec(exc)
                clean = self._processor.process(near)
        else:
            clean = self._processor.process(near)

        self.speech_probability = float(self._processor.speech_probability)
        pcm = (np.clip(clean, -1.0, 1.0) * 32767.0).astype(np.int16)
        return pcm.tobytes()

    def clear(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None

        if self._loopback_recorder is not None:
            try:
                self._loopback_recorder.__exit__(None, None, None)
            except Exception:
                log.debug("Could not close loopback recorder", exc_info=True)
            finally:
                self._loopback_recorder = None
