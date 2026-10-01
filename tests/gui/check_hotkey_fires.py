"""Manual check (needs a Windows desktop): the dictation shortcut really reaches the app while Tk's main loop runs (Ctrl+G and the default)."""
import ctypes
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"
prefs.SETTINGS_FILE.write_text(json.dumps({"hotkey": "ctrl+alt+f9"}))

import hotkeys
import rover

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
fired = []
app.toggle_voice = lambda from_hotkey=False: fired.append(from_hotkey)
assert app.hotkey_ok
user32 = ctypes.windll.user32
VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}


def press(spec):
    mods, key = hotkeys.parse(spec)
    names = [name for name, flag in (("ctrl", hotkeys.MOD_CONTROL), ("alt", hotkeys.MOD_ALT), ("shift", hotkeys.MOD_SHIFT)) if mods & flag]
    for name in names:
        user32.keybd_event(VK[name], 0, 0, 0)
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, 2, 0)
    for name in reversed(names):
        user32.keybd_event(VK[name], 0, 2, 0)


def run_until(condition, seconds=2.0):
    import time
    end = time.time() + seconds
    while time.time() < end and not condition():
        app.root.update()
        time.sleep(0.02)


# the shortcut from the settings fires toggle_voice
press("ctrl+alt+f9")
run_until(lambda: fired)
assert fired == [True], fired
# and after changing it, the new one fires and the old one no longer does
app.prefs["hotkey"] = "ctrl+alt+f10"
assert app.apply_hotkey()
fired.clear()
press("ctrl+alt+f9")
run_until(lambda: fired, 0.6)
assert fired == []
press("ctrl+alt+f10")
run_until(lambda: fired)
assert fired == [True], fired
print("PASS")
app.quit()
