"""Downloads the speech-recognition model now so the first dictation starts instantly."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from faster_whisper import WhisperModel   # noqa: E402

import settings   # noqa: E402

name = settings.load()["whisper_model"]
print(f"Downloading the '{name}' Whisper model into {ROOT / 'models'} …")
WhisperModel(name, device="cpu", compute_type="int8", download_root=str(ROOT / "models"))
print(json.dumps({"model": name, "status": "ready"}))
