"""French/English speech recognition for Rover: a local faster-whisper model by default, or an OpenAI-compatible service."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

import transcribe
import vocab
from i18n import tr
from modelstore import MODEL_DIR, model_downloaded      # noqa: F401  (re-exported for the benchmark)

MAX_SECONDS = 30 * 60              # half an hour; the audio is kept as small chunks, so a long recording costs nothing while it runs


def local_transcribe(model: WhisperModel, audio: np.ndarray, language: str, prompt: str = "") -> str:
    """Greedy decoding without timestamps: several times faster than the default beam search, plenty for dictation."""
    segments, _ = model.transcribe(audio, language=language, beam_size=1, vad_filter=True,
                                   condition_on_previous_text=False, without_timestamps=True,
                                   initial_prompt=prompt or None)
    return " ".join(segment.text.strip() for segment in segments).strip()


class Recorder:
    """Records from the default microphone, then transcribes. `on_status` receives 'listening' or 'transcribing'."""

    def __init__(self, on_status: Callable[[str], None], on_result: Callable[[str, str], None],
                 on_error: Callable[[str], None], model_name: str = "base",
                 get_config: Callable[[], dict] | None = None, on_partial: Callable[[str], None] | None = None,
                 get_live: Callable[[], bool] | None = None, get_vocabulary: Callable[[], str] | None = None):
        self.on_status = on_status
        self.on_result = on_result
        self.on_error = on_error
        self.model_name = model_name
        self.get_config = get_config or (lambda: {})
        self.on_partial = on_partial                         # receives the words recognised so far, while you are still talking
        self.get_live = get_live or (lambda: False)
        self.get_vocabulary = get_vocabulary or (lambda: "")
        self._prompt = ""
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
        """Load the model in the background so the first dictation answers at once (never downloads anything)."""
        config = self.get_config()
        if transcribe.is_gpu(config):
            import accel

            def start_server():
                try:
                    if accel.ready(self.model_name):
                        accel.server.ensure(self.model_name)
                        accel.server.transcribe(np.zeros(8000, dtype=np.float32), "en", self.model_name)   # compiles the GPU kernels now, not on your first sentence
                except accel.AccelError:
                    pass                                   # the first dictation reports it, or falls back to the CPU

            threading.Thread(target=start_server, daemon=True).start()
            return
        if transcribe.is_remote(config) or not model_downloaded(self.model_name):
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
        return local_transcribe(self._load_model(), audio, language, self._prompt)

    def _piece(self, audio: np.ndarray, language: str, config: dict, fallback: bool | None = None) -> str:
        if len(audio) < 4000 or float(np.max(np.abs(audio))) < 0.003:
            return ""
        return transcribe.run(audio, language, config, self._local,
                              fallback=model_downloaded(self.model_name) if fallback is None else fallback)

    def _preview(self, tail: np.ndarray, language: str, config: dict, finished: list[str], state: dict) -> None:
        """Recognise the words spoken since the last finished piece, only to show them: a slow CPU model never takes over for this."""
        try:
            text = self._piece(tail, language, config, fallback=False)
            if text and not state["stopped"] and self.on_partial:
                self.on_partial(" ".join([*finished, text]).strip())
        except Exception:
            pass                                             # a failed preview is simply not shown
        finally:
            state["busy"] = False

    def _run(self, language: str) -> None:
        chunks: list[np.ndarray] = []
        self._prompt = vocab.build_prompt(self.get_vocabulary(), "", language)           # your words, so English terms are written as such
        config = {**self.get_config(), "whisper_model": self.model_name, "prompt": self._prompt}
        streaming = transcribe.is_gpu(config)           # only the GPU is fast enough to work while you talk
        pieces: list | None = [] if streaming else None
        pool = ThreadPoolExecutor(max_workers=1) if streaming else None
        live = bool(streaming and self.on_partial and self.get_live())
        preview_pool = ThreadPoolExecutor(max_workers=1) if live else None
        preview = {"busy": False, "stopped": False}
        last_preview = 0.0
        cutter = transcribe.PieceCutter()
        taken = 0                                       # chunks already given to the cutter

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
                    if pieces is not None:
                        fresh = chunks[taken:]
                        taken += len(fresh)
                        for piece in cutter.feed(fresh):          # everything before a pause is transcribed now, while you keep talking
                            pieces.append(pool.submit(self._piece, piece, language, config))
                        now = time.monotonic()
                        if live and not preview["busy"] and now - last_preview > 1.0:
                            tail = cutter.pending
                            if len(tail) > 12000 and float(np.max(np.abs(tail[-16000:]))) > 0.01:          # voice in the last second
                                preview["busy"], last_preview = True, now
                                finished = [f.result() for f in pieces if f.done() and not f.cancelled() and f.exception() is None]
                                preview_pool.submit(self._preview, tail, language, config, [t for t in finished if t], preview)

            preview["stopped"] = True
            if not chunks:
                raise RuntimeError(tr("No sound received from the microphone"))
            audio = np.concatenate(chunks)
            if len(audio) < 8000 or float(np.max(np.abs(audio))) < 0.003:
                raise RuntimeError(tr("No voice detected"))

            self.on_status("transcribing")
            config = {**self.get_config(), "whisper_model": self.model_name, "prompt": self._prompt}
            text = ""
            if pieces is not None:
                try:
                    tail = audio[cutter.consumed:]
                    pieces.append(pool.submit(self._piece, tail, language, config))
                    text = " ".join(t for t in (f.result() for f in pieces) if t).strip()
                except Exception:
                    text = ""                          # any trouble with the pieces: start over on the whole recording
            if not text:
                text = transcribe.run(audio, language, config, self._local, fallback=model_downloaded(self.model_name))
            if not text:
                raise RuntimeError(tr("No speech recognised"))
            self.on_result(text, language)
        except Exception as exc:
            self.on_error(str(exc))
        finally:
            preview["stopped"] = True
            if pool:
                pool.shutdown(wait=False)
            if preview_pool:
                preview_pool.shutdown(wait=False)
