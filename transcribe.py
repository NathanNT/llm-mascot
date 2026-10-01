"""Speech-to-text through an OpenAI-compatible web service (OpenAI, Groq, your own), with a local fallback.

The local model stays the default: nothing leaves the PC. Choosing a service sends the recorded audio to it.
"""

from __future__ import annotations

import io
import os
import re
import wave
from urllib.parse import urlparse

import numpy as np
import requests

import secrets_store
from i18n import tr

SAMPLE_RATE = 16000
ENGINES: dict[str, dict] = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini-transcribe",
               "env": "OPENAI_API_KEY", "models": ("gpt-4o-mini-transcribe", "gpt-transcribe", "gpt-4o-transcribe", "whisper-1")},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "whisper-large-v3-turbo",
             "env": "GROQ_API_KEY", "models": ("whisper-large-v3-turbo", "whisper-large-v3")},
    "custom": {"label": "Custom", "base_url": "", "model": "", "env": "TRANSCRIPTION_API_KEY", "models": ()},
}
_SAFE_MODEL = re.compile(r"^[\w.\-:/]{1,80}$")


class TranscribeError(RuntimeError):
    def __init__(self, message: str, recoverable: bool = False, empty: bool = False):
        super().__init__(message)
        self.recoverable = recoverable          # worth falling back to the local model
        self.empty = empty                      # the service understood nothing in this audio (silence)


def is_remote(config: dict) -> bool:
    return config.get("engine", "local") in ENGINES


def engine_label(config: dict) -> str:
    return ENGINES.get(config.get("engine", "local"), {}).get("label", "")


def safe_base_url(url: str) -> str:
    """https only (plain http just for this PC), no credentials in the address; '' when unusable."""
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if parsed.scheme == "http" and not local:
        return ""
    return url


def resolve(config: dict) -> dict:
    """The effective engine, model, address and key (stored key first, then the engine's environment variable)."""
    engine = config.get("engine", "local")
    preset = ENGINES.get(engine, ENGINES["custom"])
    model = (config.get("model") or "").strip() or preset["model"]
    base_url = safe_base_url(config.get("base_url") or "") if engine == "custom" else preset["base_url"]
    key = secrets_store.unprotect(config.get("api_key", "")) or os.environ.get(preset["env"], "")
    return {"engine": engine, "model": model if _SAFE_MODEL.match(model) else "", "base_url": base_url, "key": key.strip(),
            "env": preset["env"]}


def key_source(config: dict) -> str:
    """'stored', 'environment' or '' – where the API key comes from (never the key itself)."""
    if secrets_store.unprotect(config.get("api_key", "")):
        return "stored"
    return "environment" if os.environ.get(ENGINES.get(config.get("engine"), ENGINES["custom"])["env"], "").strip() else ""


def wav_bytes(audio: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


def _explain(response: requests.Response, settings: dict) -> TranscribeError:
    code = response.status_code
    if code in (401, 403):
        return TranscribeError(tr("The API key was rejected"))
    if code == 404:
        return TranscribeError(tr("Model or endpoint not found ({model})").format(model=settings["model"]))
    if code == 413:
        return TranscribeError(tr("The recording is too long for this service"))
    if code == 429:
        return TranscribeError(tr("Rate limit reached or no credit left"), recoverable=True)
    if code >= 500:
        return TranscribeError(tr("The service is busy, try again"), recoverable=True)
    try:
        detail = str(response.json().get("error", {}).get("message", ""))[:120].replace(settings["key"], "…") if settings["key"] else ""
    except (ValueError, AttributeError):
        detail = ""
    return TranscribeError(tr("Transcription service error ({code}) {detail}").format(code=code, detail=detail).strip())


def transcribe_remote(audio: np.ndarray, language: str, config: dict) -> str:
    settings = resolve(config)
    if not settings["base_url"] or not settings["model"]:
        raise TranscribeError(tr("Set the service address and model in Advanced settings"))
    if not settings["key"]:
        raise TranscribeError(tr("No API key for {service}").format(service=engine_label(config)))
    try:
        response = requests.post(
            settings["base_url"] + "/audio/transcriptions",
            headers={"Authorization": "Bearer " + settings["key"]},
            files={"file": ("dictation.wav", wav_bytes(audio), "audio/wav")},
            data={"model": settings["model"], "language": language, "response_format": "json", "temperature": "0",
                  **({"prompt": config["prompt"]} if config.get("prompt") else {})},
            timeout=(8, 60 + len(audio) / SAMPLE_RATE / 4),          # a long recording takes the service longer
        )
    except requests.Timeout as exc:
        raise TranscribeError(tr("The service took too long to answer"), recoverable=True) from exc
    except requests.RequestException as exc:
        raise TranscribeError(tr("Cannot reach {host}").format(host=urlparse(settings["base_url"]).hostname), recoverable=True) from exc
    if not response.ok:
        raise _explain(response, settings)
    try:
        text = str(response.json().get("text", "")).strip()
    except ValueError as exc:
        raise TranscribeError(tr("Unreadable answer from the service")) from exc
    if not text:
        raise TranscribeError(tr("No speech recognised"), empty=True)
    return text


REMOTE_PIECE_SECONDS = 600.0     # services refuse uploads above ~25 MB: ten minutes of 16 kHz mono is about 19 MB


def transcribe_remote_long(audio: np.ndarray, language: str, config: dict) -> str:
    """A long recording is sent to the service in pieces cut at its quietest moments; silent pieces are skipped."""
    pieces = split_audio(audio, REMOTE_PIECE_SECONDS)
    if len(pieces) == 1:
        return transcribe_remote(audio, language, config)
    texts = []
    for piece in pieces:
        if float(np.max(np.abs(piece))) < 0.003:
            continue
        try:
            texts.append(transcribe_remote(piece, language, config))
        except TranscribeError as exc:
            if not exc.empty:
                raise
    if not texts:
        raise TranscribeError(tr("No speech recognised"), empty=True)
    return " ".join(texts)


def is_gpu(config: dict) -> bool:
    return config.get("engine", "local") == "gpu"


def run(audio: np.ndarray, language: str, config: dict, local, fallback: bool) -> str:
    """Transcribe with the chosen engine; if the GPU server or a service fails, use the local model when it is ready."""
    if is_gpu(config):
        import accel                               # imported here: accel itself needs this module
        try:
            return accel.transcribe_gpu(audio, language, config.get("whisper_model", "base"), config.get("prompt", ""))
        except accel.AccelError as exc:
            if fallback:
                return local(audio, language)
            raise TranscribeError(str(exc)) from exc
    if not is_remote(config):
        return local(audio, language)
    try:
        return transcribe_remote_long(audio, language, config)
    except TranscribeError as exc:
        if exc.recoverable and fallback:
            return local(audio, language)
        raise


def test_connection(config: dict) -> tuple[bool, str]:
    """Free check: list the service's models with the key."""
    settings = resolve(config)
    if not settings["base_url"]:
        return False, tr("Set the service address and model in Advanced settings")
    if not settings["key"]:
        return False, tr("No API key for {service}").format(service=engine_label(config))
    try:
        response = requests.get(settings["base_url"] + "/models", headers={"Authorization": "Bearer " + settings["key"]}, timeout=(6, 15))
    except requests.RequestException:
        return False, tr("Cannot reach {host}").format(host=urlparse(settings["base_url"]).hostname)
    if response.status_code in (401, 403):
        return False, tr("The API key was rejected")
    if not response.ok:
        return False, tr("Transcription service error ({code}) {detail}").format(code=response.status_code, detail="").strip()
    return True, tr("Connected")


MIN_PIECE_SECONDS = 5.0          # a piece is sent to the GPU as soon as a pause follows at least this much speech
PAUSE_SECONDS = 0.4


def find_pause(audio: np.ndarray, rate: int = 16000) -> int | None:
    """Sample index in the middle of the last pause (PAUSE_SECONDS of quiet) that leaves at least MIN_PIECE_SECONDS before it."""
    frame = rate // 20
    count = len(audio) // frame
    need = int(PAUSE_SECONDS * 20)
    first = int(MIN_PIECE_SECONDS * 20)
    if count < first + need:
        return None
    level = np.abs(audio[:count * frame]).reshape(count, frame).max(axis=1)
    quiet = level < max(0.008, 0.1 * float(np.percentile(level, 95)))
    for start in range(count - need, first - 1, -1):
        if quiet[start:start + need].all():
            return (start + need // 2) * frame
    return None


PIECE_LIMIT_SECONDS = 13.0       # the GPU server listens to 15 s windows (-ac 768): about five times quicker than the default 30 s


def quietest_cut(audio: np.ndarray, limit_seconds: float = PIECE_LIMIT_SECONDS, rate: int = 16000) -> int:
    """Where to cut a recording that is too long for one window: the quietest 50 ms in its last four seconds."""
    frame = rate // 20
    end = min(len(audio), int(limit_seconds * rate)) // frame
    begin = max(1, end - 4 * 20)
    level = np.abs(audio[:end * frame]).reshape(end, frame).max(axis=1)
    return (begin + int(np.argmin(level[begin:end]))) * frame


def split_audio(audio: np.ndarray, limit_seconds: float = PIECE_LIMIT_SECONDS, rate: int = 16000) -> list[np.ndarray]:
    pieces = []
    while len(audio) > limit_seconds * rate:
        cut = quietest_cut(audio, limit_seconds, rate)
        pieces.append(audio[:cut])
        audio = audio[cut:]
    return pieces + [audio]


class PieceCutter:
    """Audio arrives a moment at a time while you talk; this hands back the pieces that are ready to be transcribed
    (everything before a pause, or before the quietest moment when there is no pause yet), and keeps only what is not cut yet."""

    def __init__(self) -> None:
        self.pending = np.zeros(0, dtype=np.float32)
        self.consumed = 0                         # samples already handed back

    def feed(self, fresh: list[np.ndarray]) -> list[np.ndarray]:
        if fresh:
            self.pending = np.concatenate([self.pending, *fresh])
        ready = []
        window = int(PIECE_LIMIT_SECONDS * SAMPLE_RATE)
        while True:
            cut = find_pause(self.pending[:window])                  # even if the app stalled and a lot arrived at once, no piece exceeds the window
            if not cut and len(self.pending) > (PIECE_LIMIT_SECONDS - 1) * SAMPLE_RATE:
                cut = quietest_cut(self.pending)
            if not cut:
                return ready
            ready.append(self.pending[:cut])
            self.pending = self.pending[cut:]
            self.consumed += cut
