from __future__ import annotations

import logging
import os
import queue

import sounddevice as sd

log = logging.getLogger(__name__)


class Microphone:
    """Low-latency microphone capture with local WebRTC noise suppression."""

    def __init__(self, sample_rate: int = 16000, frame_length: int = 160) -> None:
        self.sample_rate = sample_rate
        self.frame_length = frame_length
        self.device = self._resolve_device()
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=512)
        self.stream = None
        self._processor = None
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

        directsound_default = next(
            (index for index, host, is_default in candidates
             if host == "Windows DirectSound" and is_default),
            None,
        )
        if directsound_default is not None:
            return directsound_default

        return next(
            (index for index, _host, is_default in candidates if is_default),
            None,
        )

    def start(self) -> None:
        import numpy as np
        from pywebrtc_audio import AudioProcessor

        self._numpy = np
        self._processor = AudioProcessor(
            sample_rate=self.sample_rate,
            num_channels=1,
            echo_cancellation=False,
            noise_suppression=True,
            auto_gain_control=True,
            ns_level=2,
            agc_max_gain_db=18.0,
        )

        def callback(indata, _frames, _time_info, status):
            if status:
                log.debug("Microphone stream status: %s", status)
            raw = bytes(indata)
            try:
                self.frames.put_nowait(raw)
            except queue.Full:
                # Keep the freshest audio instead of building multi-second lag.
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
        try:
            self.stream.start()
            self.clear()
        except Exception:
            self.close()
            raise

    def read(self, timeout: float = 0.25) -> bytes | None:
        try:
            raw = self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

        np = self._numpy
        near = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
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
        stream = self.stream
        self.stream = None
        if stream is None:
            return

        first_error: Exception | None = None
        try:
            stream.stop()
        except Exception as exc:
            first_error = exc
            log.warning("Microphone stop failed; still attempting to close the device: %s", exc)

        try:
            stream.close()
        except Exception as exc:
            first_error = first_error or exc
            log.exception("Microphone close failed")

        if first_error is not None:
            raise first_error
