"""The speech models that run on the CPU (faster-whisper / CTranslate2): where they live, whether they are complete, and a
download with progress, so a model can be fetched from a button instead of silently at the first dictation."""

from __future__ import annotations

import fnmatch
import threading
from pathlib import Path
from typing import Callable

from i18n import tr

MODEL_DIR = Path(__file__).resolve().parent / "models"
HF_NAMES = {"turbo": "large-v3-turbo"}      # the name faster-whisper uses on Hugging Face
FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


class ModelError(RuntimeError):
    pass


def repo_for(name: str) -> str:
    from faster_whisper.utils import _MODELS
    repo = _MODELS.get(HF_NAMES.get(name, name))
    if repo is None:
        raise ModelError(tr("Unknown model"))
    return repo


def cache_folder(name: str, root: Path | None = None) -> Path:
    return (root or MODEL_DIR) / ("models--" + repo_for(name).replace("/", "--"))


def model_downloaded(name: str, root: Path | None = None) -> bool:
    """True when this Whisper model is complete on disk (a half-finished download does not count)."""
    try:
        folder = cache_folder(name, root)
    except ModelError:
        return False
    return any(folder.glob("snapshots/*/model.bin"))


def expected_bytes(name: str) -> int:
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo_for(name), files_metadata=True)
    return sum(item.size or 0 for item in info.siblings if any(fnmatch.fnmatch(item.rfilename, pattern) for pattern in FILES))


def _bytes_on_disk(folder: Path) -> int:
    total = 0
    try:
        for item in (folder / "blobs").iterdir():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def download_model(name: str, progress: Callable[[int, int], None] | None = None, root: Path | None = None) -> None:
    """Download one model into the models folder; `progress(done, total)` is called about four times a second."""
    from faster_whisper.utils import disabled_tqdm
    from huggingface_hub import snapshot_download
    folder = cache_folder(name, root)
    try:
        total = expected_bytes(name)
    except Exception as exc:
        raise ModelError(tr("Download failed: {detail}").format(detail=str(exc)[:100])) from exc
    stop = threading.Event()

    def watch():
        while not stop.wait(0.25):
            if progress:
                progress(min(_bytes_on_disk(folder), total), total)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        snapshot_download(repo_for(name), allow_patterns=FILES, cache_dir=str(root or MODEL_DIR), tqdm_class=disabled_tqdm)
    except Exception as exc:
        raise ModelError(tr("Download failed: {detail}").format(detail=str(exc)[:100])) from exc
    finally:
        stop.set()
    if not model_downloaded(name, root):
        raise ModelError(tr("The downloaded file does not match its checksum; it was discarded"))
    if progress:
        progress(total, total)
