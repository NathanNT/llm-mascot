"""The dictation shortcut: parsing, checking and showing combinations such as "ctrl+alt+r" (no Windows calls, so it is testable)."""

from __future__ import annotations

import re

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
ORDER = ("ctrl", "alt", "shift", "win")
DEFAULT = "ctrl+alt+r"
_FUNCTION = re.compile(r"f([1-9]|1\d|2[0-4])")


def _split(text: str) -> tuple[set[str], str] | None:
    parts = [part for part in text.lower().replace(" ", "").split("+") if part]
    if len(parts) < 1:
        return None
    key, mods = parts[-1], set(parts[:-1])
    if not mods <= set(MODIFIERS) or key in MODIFIERS:
        return None
    return mods, key


def parse(text: str) -> tuple[int, int] | None:
    """(modifier flags, virtual-key code) for a usable shortcut, else None.
    A shortcut needs Ctrl, Alt or Win (Shift alone would clash with typing), unless it is a function key."""
    split = _split(text) if isinstance(text, str) else None
    if split is None:
        return None
    mods, key = split
    if _FUNCTION.fullmatch(key):
        vk = 0x70 + int(key[1:]) - 1
    elif re.fullmatch(r"[a-z0-9]", key):
        vk = ord(key.upper())
    elif key == "space":
        vk = 0x20
    else:
        return None
    if not (mods & {"ctrl", "alt", "win"}) and not _FUNCTION.fullmatch(key):
        return None
    return sum(MODIFIERS[mod] for mod in mods), vk


def normalize(text: str) -> str | None:
    """The canonical spelling ("ctrl+alt+r"), or None when the shortcut cannot be used."""
    if parse(text) is None:
        return None
    mods, key = _split(text)
    return "+".join([mod for mod in ORDER if mod in mods] + [key])


def label(text: str) -> str:
    """How it is shown: Ctrl+Alt+R."""
    canonical = normalize(text) or DEFAULT
    return "+".join(part.capitalize() if len(part) > 1 else part.upper() for part in canonical.split("+"))


def parts(text: str) -> list[str]:
    return label(text).split("+")


def from_keypress(pressed: set[str], keysym: str) -> str | None:
    """The shortcut being typed: held modifiers plus the key that was just pressed, or None for a modifier alone / an unusable key."""
    key = keysym.lower()
    if key in ("control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r", "super_l", "super_r", "win_l", "win_r"):
        return None
    if key == "space":
        pass
    elif not (re.fullmatch(r"[a-z0-9]", key) or _FUNCTION.fullmatch(key)):
        return None
    return normalize("+".join([mod for mod in ORDER if mod in pressed] + [key]))
