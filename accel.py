"""GPU speech recognition: whisper.cpp with the Vulkan backend (AMD, NVIDIA and Intel), run as a small local server.

Two things are downloaded, only when you press "Set up GPU acceleration":
  * the runtime: whisper-server.exe, built from public source by this project's GitHub Actions and checked against the
    SHA-256 in runtime_manifest.json before it is unpacked or run;
  * one GGML model file from the whisper.cpp repository on Hugging Face, checked against Hugging Face's own SHA-256.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import socket
import subprocess
import threading
import time
import zlib
import zipfile
from ctypes import wintypes
from pathlib import Path
from typing import Callable

import numpy as np
import requests

import transcribe
from i18n import tr

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE / "runtime" / "whispercpp"
GGML_DIR = HERE / "models" / "ggml"
MANIFEST_FILE = HERE / "runtime_manifest.json"
HF_REPO = "ggerganov/whisper.cpp"
# app model name -> (GGML file on Hugging Face, size in MB). Bigger models use the 5-bit versions: same accuracy for speech, half the size.
GGML_MODELS: dict[str, tuple[str, int]] = {
    "tiny": ("ggml-tiny.bin", 78), "base": ("ggml-base.bin", 148), "small": ("ggml-small.bin", 488),
    "turbo": ("ggml-large-v3-turbo-q5_0.bin", 574), "medium": ("ggml-medium-q5_0.bin", 539),
    "large-v3": ("ggml-large-v3-q5_0.bin", 1081),
}


class AccelError(RuntimeError):
    pass


def manifest() -> dict:
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8")).get("whispercpp", {})
    except (OSError, ValueError):
        return {}


def runtime_exe() -> Path:
    return RUNTIME_DIR / "whisper-server.exe"


def runtime_installed() -> bool:
    """The server is unpacked and is the exact build the manifest names."""
    marker = RUNTIME_DIR / "INSTALLED.json"
    try:
        installed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    expected = manifest().get("sha256", "")
    return runtime_exe().is_file() and bool(expected) and installed.get("sha256") == expected


def runtime_published() -> bool:
    return bool(manifest().get("sha256") and manifest().get("url"))


def model_path(name: str) -> Path:
    return GGML_DIR / GGML_MODELS.get(name, ("missing", 0))[0]


def model_installed(name: str) -> bool:
    return name in GGML_MODELS and model_path(name).is_file()


def download_size_mb(name: str) -> int:
    return GGML_MODELS[name][1]


# ------------------------------------------------------------------------------------------------ downloading

def _download(url: str, destination: Path, sha256: str, progress: Callable[[int, int], None] | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with requests.get(url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(partial, "wb") as handle:
                for chunk in response.iter_content(1 << 20):
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise AccelError(tr("Download failed: {detail}").format(detail=str(exc)[:100])) from exc
    if sha256 and digest.hexdigest().lower() != sha256.lower():
        partial.unlink(missing_ok=True)
        raise AccelError(tr("The downloaded file does not match its checksum; it was discarded"))
    partial.replace(destination)


def install_runtime(progress: Callable[[int, int], None] | None = None) -> None:
    info = manifest()
    if not runtime_published():
        raise AccelError(tr("The GPU runtime is not published yet"))
    archive = RUNTIME_DIR.parent / "whisper-vulkan-win-x64.zip"
    _download(info["url"], archive, info["sha256"], progress)
    stop()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (RUNTIME_DIR / member.filename).resolve()
            if RUNTIME_DIR.resolve() not in target.parents and target != RUNTIME_DIR.resolve():
                raise AccelError(tr("Unsafe file name in the archive"))
        bundle.extractall(RUNTIME_DIR)
    archive.unlink(missing_ok=True)
    (RUNTIME_DIR / "INSTALLED.json").write_text(json.dumps({"sha256": info["sha256"], "version": info.get("version", "")}), encoding="utf-8")


def install_model(name: str, progress: Callable[[int, int], None] | None = None) -> None:
    if name not in GGML_MODELS:
        raise AccelError(tr("Unknown model"))
    filename = GGML_MODELS[name][0]
    try:
        listing = requests.get(f"https://huggingface.co/api/models/{HF_REPO}/tree/main", timeout=20).json()
        entry = next(item for item in listing if item.get("path") == filename)
        digest = (entry.get("lfs") or {}).get("oid", "")
    except (requests.RequestException, ValueError, StopIteration) as exc:
        raise AccelError(tr("Download failed: {detail}").format(detail=str(exc)[:100])) from exc
    _download(f"https://huggingface.co/{HF_REPO}/resolve/main/{filename}", model_path(name), digest, progress)


# ------------------------------------------------------------------------------------------------ the server

def _kill_with_us(process: subprocess.Popen) -> None:
    """Put the server in a job that Windows closes when this program ends, however it ends: no orphan process."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("a", "b", "c", "d", "e", "f")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    job = kernel32.CreateJobObjectW(None, None)
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
    kernel32.AssignProcessToJobObject(job, int(process._handle))       # the job handle stays open until we exit
    _JOBS.append(job)


_JOBS: list = []


_help_text: str | None = None


def fast_flags() -> list[str]:
    """Greedy decoding is quicker than the default beam search; only flags this build knows are used."""
    global _help_text
    if _help_text is None:
        try:
            completed = subprocess.run([str(runtime_exe()), "--help"], capture_output=True, text=True, errors="replace", timeout=15,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _help_text = completed.stdout + completed.stderr
        except (OSError, subprocess.SubprocessError):
            _help_text = ""
    flags: list[str] = []
    if "--beam-size" in _help_text:
        flags += ["--beam-size", "1"]
    if "--best-of" in _help_text:
        flags += ["--best-of", "1"]
    if "--audio-ctx" in _help_text:
        flags += ["--audio-ctx", "768"]     # half the audio window: short dictations are ~5x quicker, recordings are cut into pieces that fit
    return flags                      # the temperature fallback stays on: it is what rescues small models from repetition loops


class Server:
    """whisper-server.exe kept running with one model loaded on the GPU, so each dictation only pays for inference."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.port = 0
        self.model = ""
        self.lock = threading.Lock()

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def ensure(self, model: str, timeout: float = 120.0) -> None:
        with self.lock:
            if self.running() and self.model == model:
                return
            self._stop_locked()
            if not runtime_installed():
                raise AccelError(tr("GPU runtime not installed"))
            if not model_installed(model):
                raise AccelError(tr("GPU model not installed: {model}").format(model=model))
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                self.port = probe.getsockname()[1]
            self.process = subprocess.Popen(
                [str(runtime_exe()), "-m", str(model_path(model)), "--host", "127.0.0.1", "--port", str(self.port), "-t", "4", *fast_flags()],
                cwd=str(RUNTIME_DIR), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                _kill_with_us(self.process)
            except OSError:
                pass
            self.model = model
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AccelError(tr("The GPU speech server stopped at start-up (is the graphics driver up to date?)"))
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                        return
                except OSError:
                    time.sleep(0.15)
            self._stop_locked()
            raise AccelError(tr("The GPU speech server did not start in time"))

    def transcribe(self, audio: np.ndarray, language: str, model: str) -> str:
        self.ensure(model)
        try:
            response = requests.post(f"http://127.0.0.1:{self.port}/inference",
                                     files={"file": ("dictation.wav", transcribe.wav_bytes(audio), "audio/wav")},
                                     data={"response_format": "json", "language": language, "temperature": "0.0"}, timeout=(5, 90))
            response.raise_for_status()
            return str(response.json().get("text", "")).strip()
        except (requests.RequestException, ValueError) as exc:
            raise AccelError(tr("The GPU speech server did not answer: {detail}").format(detail=str(exc)[:100])) from exc

    def _stop_locked(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                self.process.kill()
        self.process, self.model = None, ""

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()


server = Server()


def stop() -> None:
    server.stop()


def looks_degenerate(text: str, seconds: float) -> bool:
    """A speech model stuck in a loop repeats itself: far too many words, or text that compresses like a repeated phrase."""
    if len(text.split()) > max(12, 7 * seconds):
        return True
    raw = text.encode("utf-8")
    return len(raw) > 80 and len(raw) / len(zlib.compress(raw)) > 2.4


def transcribe_gpu(audio: np.ndarray, language: str, model: str) -> str:
    text = " ".join(part for part in (server.transcribe(piece, language, model) for piece in transcribe.split_audio(audio)) if part).strip()
    if not text:
        raise AccelError(tr("No speech recognised"))
    if looks_degenerate(text, len(audio) / transcribe.SAMPLE_RATE):
        raise AccelError(tr("The GPU model looped on this recording"))
    return text


def ready(model: str) -> bool:
    return runtime_installed() and model_installed(model)
