from __future__ import annotations

import os
import queue

import sounddevice as sd


class Microphone:
    """One shared microphone stream; callbacks never wait on the assistant."""

    def __init__(self, sample_rate: int = 16000, frame_length: int = 800) -> None:
        self.sample_rate = sample_rate
        self.frame_length = frame_length
        self.device = self._resolve_device()
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=128)
        self.stream = None

    @staticmethod
    def _resolve_device():
        requested = os.environ.get("ECHO_INPUT_DEVICE", "").strip()
        devices = sd.query_devices()
        if requested:
            if requested.isdigit():
                return int(requested)
            match = next(
                (i for i, item in enumerate(devices)
                 if requested.lower() in item["name"].lower() and item["max_input_channels"] > 0),
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
        directsound = next(
            (index for index, host, _ in candidates if host == "Windows DirectSound"),
            None,
        )
        if directsound is not None:
            return directsound
        return next((index for index, _host, is_default in candidates if is_default), None)

    def start(self) -> None:
        def callback(indata, _frames, _time_info, status):
            if status:
                return
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

    def read(self, timeout: float = 0.25) -> bytes | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
