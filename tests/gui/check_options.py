"""Manual check (needs a Windows desktop): changing the dictation shortcut, and the live words in the microphone bubble."""
import ctypes
import json
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"
prefs.SETTINGS_FILE.write_text(json.dumps({"theme": "xp", "ui_language": "en", "transcription": {"engine": "gpu"}}))

import hotkeys
import rover
import windows

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.handle_hover = lambda now: None
app.root.update()
user32 = ctypes.windll.user32


def taken(spec):
    """True when something (the app, or another program) already owns this shortcut."""
    mods, vk = hotkeys.parse(spec)
    if user32.RegisterHotKey(None, 99, mods | windows.MOD_NOREPEAT, vk):
        user32.UnregisterHotKey(None, 99)
        return False
    return True


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.03)


assert app.hotkey_ok and taken("ctrl+alt+r") and not taken("ctrl+alt+f8")
app.open_settings()
window = app.settings_window
window.toggle_page()
window = window.peer
pump(0.3)


def widgets(root=None):
    for child in (root or window.top).winfo_children():
        yield child
        yield from widgets(child)


def press(text):
    next(w for w in widgets() if isinstance(w, tk.Button) and w.cget("text") == text).invoke()
    pump(0.2)


def focus_label():
    return window.top.focus_get()


def label_texts():
    return [w.cget("text") for w in widgets() if isinstance(w, tk.Label)]


assert "Ctrl+Alt+R" in label_texts()
# --- choose a new shortcut by pressing it
press("Change…")
assert not taken("ctrl+alt+r"), "while listening, the old shortcut is released so that it can be typed"
rover.pressed_modifiers = lambda: {"ctrl", "alt"}
focus_label().event_generate("<KeyPress>", keysym="F8")
pump(0.3)
assert app.prefs["hotkey"] == "ctrl+alt+f8" and "Ctrl+Alt+F8" in label_texts(), label_texts()
assert taken("ctrl+alt+f8") and not taken("ctrl+alt+r"), "the new shortcut is claimed, the old one freed"
# --- a shortcut that another program owns is refused and the old one stays
assert user32.RegisterHotKey(None, 98, windows.MOD_CONTROL | windows.MOD_ALT, 0x76)
press("Change…")
focus_label().event_generate("<KeyPress>", keysym="F7")
pump(0.3)
user32.UnregisterHotKey(None, 98)
assert app.prefs["hotkey"] == "ctrl+alt+f8" and app.hotkey_ok
assert any("already used" in t for t in label_texts()), label_texts()
# --- Escape cancels, and Default restores
press("Change…")
focus_label().event_generate("<KeyPress>", keysym="Escape")
pump(0.2)
assert app.prefs["hotkey"] == "ctrl+alt+f8" and taken("ctrl+alt+f8")
press("Default")
assert app.prefs["hotkey"] == "ctrl+alt+r" and taken("ctrl+alt+r") and not taken("ctrl+alt+f8")
# --- the menu and the bubble show the shortcut
app.prefs["hotkey"] = "ctrl+shift+f9"
assert app.apply_hotkey() and taken("ctrl+shift+f9")
assert "Ctrl+Shift+F9" in app.menu.entrycget(0, "label")
assert hotkeys.parts(app.prefs["hotkey"]) == ["Ctrl", "Shift", "F9"]

# --- live words in the microphone bubble
app.dict_state = lambda: "recording"
app.rec_started = time.monotonic()
app.render_dictation()
pump(0.2)
live = app.live_widgets.get("live")
assert live is not None, "a live label is reserved while recording on the GPU"
app.show_partial("alors voici ce que je dis en ce moment")
assert live.cget("text") == "alors voici ce que je dis en ce moment"
app.show_partial("x" * 400)
assert live.cget("text").startswith("…") and len(live.cget("text")) == 211
app.prefs["live_transcript"] = False
app.render_dictation()
assert app.live_widgets.get("live") is None
# --- "My words": typing updates the setting, and a project folder can teach its names
from tkinter import filedialog

project = Path(tempfile.mkdtemp()) / "paperclip-orchestrator"
(project / "src").mkdir(parents=True)
(project / "package.json").write_text(json.dumps({"name": "paperclip-orchestrator", "dependencies": {"zod": "3"}}))
(project / "src" / "dispatcher.ts").write_text("")
pump(0.3)
boxes = [w for w in widgets() if isinstance(w, tk.Text)]
my_words = next(w for w in boxes if int(w.cget("height")) == 2)
my_words.delete("1.0", "end")
my_words.insert("1.0", "Paperclip")
my_words.focus_force()
my_words.event_generate("<KeyRelease>", keysym="a")
assert app.prefs["my_words"] == "Paperclip", app.prefs["my_words"]
filedialog.askdirectory = lambda **kwargs: str(project)
known = {w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel)}
press("Learn from a folder…")
dialogs = [w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel) and w not in known]
assert dialogs, "the dialog with the names found is open"


def descendants(root):
    for child in root.winfo_children():
        yield child
        yield from descendants(child)


found = next(w for w in descendants(dialogs[0]) if isinstance(w, tk.Text)).get("1.0", "end")
assert "paperclip-orchestrator" in found and "dispatcher" in found and "zod" in found, found
next(w for w in descendants(dialogs[0]) if isinstance(w, tk.Button) and w.cget("text") == "Add to my words").invoke()
pump(0.3)
assert app.prefs["my_words"].startswith("Paperclip") and "dispatcher" in app.prefs["my_words"] and "zod" in app.prefs["my_words"], app.prefs["my_words"]
assert my_words.get("1.0", "end-1c") == app.prefs["my_words"]
print("PASS")
app.quit()
