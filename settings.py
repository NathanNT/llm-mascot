"""Persistent user preferences for Rover (mascot, size, theme, AI shortcuts)."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

import mascot_setup
import vocab
from hotkeys import parse as windows_free_parse
import styles
from i18n import system_language

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.json"
MASCOT_DIR = APP_DIR / "mascots"
BLOUB_URL = "https://bloub.vercel.app/"

MAX_APPS = 8
SIZE_MIN, SIZE_MAX, SIZE_STEP = 48, 192, 8
WHISPER_MODELS = (   # (name, download size, character) – the speech model runs on your PC
    ("tiny", "≈75 MB", "Very fast, often wrong: not recommended"),
    ("base", "≈145 MB", "Fast, good for clear speech"),
    ("small", "≈480 MB", "Balanced, noticeably better"),
    ("turbo", "≈800 MB", "Near-best accuracy, still quick"),
    ("medium", "≈1.5 GB", "Accurate, slower on a CPU"),
    ("large-v3", "≈3 GB", "Best accuracy, heavy"),
)
SHRINK_CHOICES = (0, 5, 15, 30, 60)         # minutes
HIDE_CHOICES = (0, 15, 30, 60, 120)
REWRITE_PROVIDERS = ("auto", "codex", "claude", "off")
SIZE_PRESETS = (("Small", 64), ("Medium", 96), ("Large", 128), ("Extra large", 160))
IMAGE_SUFFIXES = (".png", ".gif", ".webp")

DEFAULT_APPS = [
    {"name": "Claude", "url": "https://claude.ai", "kind": "claude"},
    {"name": "ChatGPT", "url": "https://chatgpt.com", "kind": "openai"},
]

DEFAULTS: dict = {
    "mascot": "rover",      # "rover", "dot", "file:<image>", "pack:<folder>" or "pet:<Codex pet>"
    "size": 144,
    "side": "right",        # preferred side of the AI shortcut rail
    "theme": "dark",
    "ui_language": "auto",  # "auto" follows Windows; "fr" or "en"
    "language": "auto",     # dictation language: "auto" follows Windows; "fr" or "en"
    "apps": DEFAULT_APPS,
    "whisper_model": "base",
    "insert": "type",       # "type" types at the caret, "copy" only puts the text on the clipboard
    "rewrite_provider": "auto",   # "auto", "codex", "claude" or "off" (insert the raw transcript)
    "rewrite_style": "clear",     # a preset id from styles.py, or "custom"
    "rewrite_prompt": "",         # your own editing instruction, used when the style is "custom"
    "animations": {},               # per mascot: the animation chosen for each event (mascot_setup.py)
    "layout": {},                   # per mascot: where the gauge, microphone and shortcut rail sit
    "keep_context": False,          # send the last few dictations along with the next one, for the rewrite
    "live_transcript": True,        # show the words as you speak (GPU only)
    "hotkey": "ctrl+alt+r",         # start / stop the dictation from anywhere
    "idle_shrink": 0,               # minutes without use before the mascot shrinks (0 = never)
    "idle_hide": 0,                 # minutes without use before it hides in the notification area (0 = never)
    "my_words": "",                      # your own words: project and product names…, first in the speech prompt
    "vocabulary": vocab.DEFAULT_TERMS,   # words the speech model should expect (English terms in French sentences…)
    "startup": None,              # start with Windows; None = not decided in this session
    "quotas_url": "",       # optional local JSON service; see README
    "codex": {"home": "", "model": "", "reasoning": "low", "auth_store": "", "tier": ""},     # tier "priority" = Codex Fast mode
    "claude": {"model": ""},
    "transcription": {"engine": "local", "model": "", "base_url": "", "api_key": ""},   # api_key is stored encrypted (DPAPI)
}


def load() -> dict:
    data = json.loads(json.dumps(DEFAULTS))
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _finish(data)
    if not isinstance(stored, dict):
        return _finish(data)
    for key in ("mascot", "side", "theme", "language"):
        if isinstance(stored.get(key), str):
            data[key] = stored[key]
    if isinstance(stored.get("size"), int):
        data["size"] = clamp_size(stored["size"])
    if isinstance(stored.get("apps"), list):
        apps = [a for a in stored["apps"]
                if isinstance(a, dict) and isinstance(a.get("name"), str)
                and (isinstance(a.get("url"), str) or isinstance(a.get("path"), str))][:MAX_APPS]
        data["apps"] = apps
    if data["theme"] not in ("dark", "light", "xp"):
        data["theme"] = "dark"
    if data["side"] not in ("left", "right"):
        data["side"] = "right"
    if isinstance(stored.get("codex"), dict):
        data["codex"].update({k: v for k, v in stored["codex"].items() if k in data["codex"] and isinstance(v, str)})
        if stored["codex"].get("rewrite") == "off" and "rewrite_provider" not in stored:
            data["rewrite_provider"] = "off"              # older files switched the rewrite off here
    if isinstance(stored.get("claude"), dict) and isinstance(stored["claude"].get("model"), str):
        data["claude"]["model"] = stored["claude"]["model"]
    if isinstance(stored.get("transcription"), dict):
        incoming = stored["transcription"]
        if incoming.get("engine") in ("local", "gpu", "openai", "groq", "custom"):
            data["transcription"]["engine"] = incoming["engine"]
        if isinstance(incoming.get("model"), str) and re.fullmatch(r"[\w.\-:/]{0,80}", incoming["model"]):
            data["transcription"]["model"] = incoming["model"]
        if isinstance(incoming.get("base_url"), str):
            data["transcription"]["base_url"] = incoming["base_url"][:200]
        if isinstance(incoming.get("api_key"), str) and incoming["api_key"].startswith("dpapi:"):
            data["transcription"]["api_key"] = incoming["api_key"]      # never accept a clear-text key from the file
    if stored.get("rewrite_provider") in REWRITE_PROVIDERS:
        data["rewrite_provider"] = stored["rewrite_provider"]
    for key in ("rewrite_style", "rewrite_prompt"):
        if isinstance(stored.get(key), str):
            data[key] = stored[key][:4000]
    data["rewrite_style"] = styles.RETIRED.get(data["rewrite_style"], data["rewrite_style"])
    if isinstance(stored.get("my_words"), str):
        data["my_words"] = stored["my_words"][:1500]
    if isinstance(stored.get("vocabulary"), str):
        data["vocabulary"] = stored["vocabulary"][:1500]
    for key, allowed in (("idle_shrink", SHRINK_CHOICES), ("idle_hide", HIDE_CHOICES)):
        if stored.get(key) in allowed and not isinstance(stored.get(key), bool):
            data[key] = stored[key]
    for key in ("keep_context", "live_transcript"):
        if isinstance(stored.get(key), bool):
            data[key] = stored[key]
    if isinstance(stored.get("hotkey"), str) and windows_free_parse(stored["hotkey"]):
        data["hotkey"] = stored["hotkey"].lower().replace(" ", "")
    data["animations"] = mascot_setup.sanitize_animations(stored.get("animations"))
    data["layout"] = mascot_setup.sanitize_layout(stored.get("layout"))
    if isinstance(stored.get("startup"), bool):
        data["startup"] = stored["startup"]

    for key in ("quotas_url", "whisper_model"):
        if isinstance(stored.get(key), str):
            data[key] = stored[key]
    if stored.get("insert") in ("type", "copy"):
        data["insert"] = stored["insert"]
    if isinstance(stored.get("ui_language"), str):
        data["ui_language"] = stored["ui_language"]
    if data["ui_language"] not in ("auto", "fr", "en"):
        data["ui_language"] = "auto"
    if data["language"] not in ("auto", "fr", "en"):
        data["language"] = "auto"
    return _finish(data)


def _finish(data: dict) -> dict:
    if data["whisper_model"] not in [name for name, _, _ in WHISPER_MODELS]:
        data["whisper_model"] = "base"
    if data["language"] == "auto":
        data["language"] = system_language()
    return data


def save(data: dict) -> None:
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for attempt in range(6):                 # an antivirus or a sync tool can hold the file for a moment
        try:
            os.replace(temporary, SETTINGS_FILE)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.1)


def codex_pets() -> list[tuple[str, Path]]:
    """Sprite atlases of the pets that ship with the Codex extension installed on this PC (not redistributed)."""
    roots = [Path.home() / ".vscode" / "extensions", Path.home() / ".cursor" / "extensions"]
    roots += list((Path(os.environ.get("LOCALAPPDATA", "")) / "VSCode-Codex").glob("*/extensions")) if os.environ.get("LOCALAPPDATA") else []
    found: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("openai.chatgpt-*/webview/assets/*-spritesheet-*.webp"):
            name = path.name.split("-spritesheet-")[0].replace("-", " ").title()
            found.setdefault(name, path)
    return sorted(found.items())


def pet_path(value: str) -> Path | None:
    """Resolve 'pet:<name>' to an atlas that really belongs to an installed Codex extension."""
    if not value.startswith("pet:"):
        return None
    wanted = value[4:]
    return next((path for name, path in codex_pets() if name == wanted), None)


def clamp_size(value: int) -> int:
    value = max(SIZE_MIN, min(SIZE_MAX, int(value)))
    return SIZE_MIN + round((value - SIZE_MIN) / SIZE_STEP) * SIZE_STEP


def list_mascots() -> list[Path]:
    if not MASCOT_DIR.is_dir():
        return []
    return sorted(p for p in MASCOT_DIR.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def import_mascot(source: str) -> str:
    """Copy an image into mascots/ and return its settings value."""
    MASCOT_DIR.mkdir(exist_ok=True)
    source_path = Path(source)
    if source_path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Format non pris en charge (PNG, GIF ou WebP)")
    target = MASCOT_DIR / source_path.name
    if source_path.resolve() != target.resolve():
        shutil.copyfile(source_path, target)
    return f"file:{target.name}"


def list_packs() -> list[Path]:
    if not MASCOT_DIR.is_dir():
        return []
    return sorted(p for p in MASCOT_DIR.iterdir() if p.is_dir() and (p / "agent.js").is_file() and (p / "map.png").is_file())


def import_pack(source: str) -> str:
    """Copy a clippy.js-style character folder (agent.js + map.png) into mascots/."""
    folder = Path(source)
    if not (folder / "agent.js").is_file() or not (folder / "map.png").is_file():
        raise ValueError("Le dossier doit contenir agent.js et map.png")
    target = MASCOT_DIR / folder.name
    MASCOT_DIR.mkdir(exist_ok=True)
    if folder.resolve() != target.resolve():
        target.mkdir(exist_ok=True)
        for name in ("agent.js", "map.png"):
            shutil.copyfile(folder / name, target / name)
    return f"pack:{target.name}"


def pack_path(value: str) -> Path | None:
    if not value.startswith("pack:"):
        return None
    candidate = MASCOT_DIR / Path(value[5:]).name
    return candidate if (candidate / "agent.js").is_file() and (candidate / "map.png").is_file() else None


def mascot_path(value: str) -> Path | None:
    if not value.startswith("file:"):
        return None
    candidate = MASCOT_DIR / Path(value[5:]).name  # never leave mascots/
    return candidate if candidate.is_file() else None
