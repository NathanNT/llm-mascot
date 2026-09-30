"""French/English speech recognition for Rover: a local faster-whisper model by default, or an OpenAI-compatible service."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

import transcribe
from i18n import tr

MODEL_DIR = Path(__file__).resolve().parent / "models"
MAX_SECONDS = 75
HF_NAMES = {"turbo": "large-v3-turbo"}      # the name faster-whisper uses on Hugging Face


def model_downloaded(name: str) -> bool:
    """True when this Whisper model is already on disk (so it loads instantly and needs no internet)."""
    return any(MODEL_DIR.glob(f"models--*--faster-whisper-{HF_NAMES.get(name, name)}"))


def local_transcribe(model: WhisperModel, audio: np.ndarray, language: str) -> str:
    """Greedy decoding without timestamps: several times faster than the default beam search, plenty for dictation."""
    segments, _ = model.transcribe(audio, language=language, beam_size=1, vad_filter=True,
                                   condition_on_previous_text=False, without_timestamps=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


class Recorder:
    """Records from the default microphone, then transcribes. `on_status` receives 'listening' or 'transcribing'."""

    def __init__(self, on_status: Callable[[str], None], on_result: Callable[[str, str], None],
                 on_error: Callable[[str], None], model_name: str = "base",
                 get_config: Callable[[], dict] | None = None):
        self.on_status = on_status
        self.on_result = on_result
        self.on_error = on_error
        self.model_name = model_name
        self.get_config = get_config or (lambda: {})
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._model: WhisperModel | None = None
        self._model_lock = threading.Lock()
        self.level = 0.0  # live microphone peak (0..1) for the recording meter

    def set_model(self, name: str) -> None:
        """Use another Whisper model from the next dictation on (it is downloaded the first time it is needed)."""
        if name != self.model_name:
            with self._model_lock:
                self.model_name = name
                self._model = None
            self.warm_up()

    def warm_up(self) -> None:
        """Load the local model in the background so the first dictation answers at once (never downloads anything)."""
        if transcribe.is_remote(self.get_config()) or not model_downloaded(self.model_name):
            return
        threading.Thread(target=self._load_model, daemon=True).start()

    def _load_model(self) -> WhisperModel:
        with self._model_lock:
            if self._model is None:
                try:
                    self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
                except Exception as exc:     # no network on first use, disk full, unknown model name…
                    raise RuntimeError(tr("Whisper model unavailable: {detail}").format(detail=str(exc)[:120])) from exc
            return self._model

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

    def _local(self, audio: np.ndarray, language: str) -> str:
        return local_transcribe(self._load_model(), audio, language)

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
            text = transcribe.run(audio, language, self.get_config(), self._local, fallback=model_downloaded(self.model_name))
            if not text:
                raise RuntimeError(tr("No speech recognised"))
            self.on_result(text, language)
        except Exception as exc:
            self.on_error(str(exc))
