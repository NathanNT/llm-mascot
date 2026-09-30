"""On-device French/English speech recognition for Rover (faster-whisper, nothing leaves the PC)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from i18n import tr

MODEL_DIR = Path(__file__).resolve().parent / "models"
MAX_SECONDS = 75


class Recorder:
    """Records from the default microphone, then transcribes. `on_status` receives 'listening' or 'transcribing'."""

    def __init__(self, on_status: Callable[[str], None], on_result: Callable[[str, str], None],
                 on_error: Callable[[str], None], model_name: str = "base"):
        self.on_status = on_status
        self.on_result = on_result
        self.on_error = on_error
        self.model_name = model_name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model: WhisperModel | None = None
        self.level = 0.0  # live microphone peak (0..1) for the recording meter

    def set_model(self, name: str) -> None:
        """Use another Whisper model from the next dictation on (it is downloaded the first time it is needed)."""
        if name != self.model_name:
            self.model_name = name
            self._model = None

    @property
    def recording(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self, language: str) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(language,), daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self, language: str) -> None:
        chunks: list[np.ndarray] = []

        def callback(indata, frames, time_info, status):
            chunks.append(indata[:, 0].copy())
            self.level = float(np.max(np.abs(indata)))

        try:
            self.on_status("listening")
            try:
                stream = sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=callback)
                stream.start()
            except (sd.PortAudioError, OSError) as exc:
                raise RuntimeError(tr("Microphone unavailable: {detail}").format(detail=str(exc)[:120])) from exc
            with stream:
                started = time.monotonic()
                while not self._stop.wait(0.1) and time.monotonic() - started < MAX_SECONDS:
                    pass

            if not chunks:
                raise RuntimeError(tr("No sound received from the microphone"))
            audio = np.concatenate(chunks)
            if len(audio) < 8000 or float(np.max(np.abs(audio))) < 0.003:
                raise RuntimeError(tr("No voice detected"))

            self.on_status("transcribing")
            if self._model is None:
                try:
                    self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
                except Exception as exc:     # no network on first use, disk full, unknown model name…
                    raise RuntimeError(tr("Whisper model unavailable: {detail}").format(detail=str(exc)[:120])) from exc
            segments, _ = self._model.transcribe(audio, language=language, beam_size=5, vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            if not text:
                raise RuntimeError(tr("No speech recognised"))
            self.on_result(text, language)
        except Exception as exc:
            self.on_error(str(exc))
