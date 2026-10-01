"""Manual check (needs a Windows desktop): a CPU model that is not on the PC gets a Download button with a progress bar."""
import json
import os
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"
prefs.SETTINGS_FILE.write_text(json.dumps({"theme": os.environ.get("ROVER_TEST_THEME", "xp"), "ui_language": "en", "whisper_model": "large-v3",
                                           "transcription": {"engine": "local"}}))

import modelstore
import rover

modelstore.model_downloaded = lambda name, root=None: False          # pretend nothing is on this PC
rover.modelstore.model_downloaded = modelstore.model_downloaded
seen = {}


def fake_download(name, progress=None, root=None):
    for step in range(1, 11):
        progress(step * 300_000_000, 3_000_000_000)
        app.root.update()
        if step == 4:
            time.sleep(0.2)
            app.root.update()
            seen["text"] = next(w.cget("text") for w in widgets() if isinstance(w, tk.Label) and "Downloading the model" in str(w.cget("text")))
            from PIL import ImageGrab
            x, y = window.top.winfo_rootx(), window.top.winfo_rooty()
            ImageGrab.grab((x, y, x + window.top.winfo_width(), y + window.top.winfo_height())).save(
                Path(__file__).resolve().parents[2] / "design" / "download.png")
    raise modelstore.ModelError("stopped for the test")


class InlineThread:                      # no Tk main loop in this script: run the "background" download right away
    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


rover.modelstore.download_model = fake_download
rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.root.update()
app.open_settings()
window = app.settings_window
window.toggle_page()
window = window.peer


def widgets(root=None):
    for child in (root or window.top).winfo_children():
        yield child
        yield from widgets(child)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.03)


pump(0.5)
labels = [w.cget("text") for w in widgets() if isinstance(w, tk.Label)]
assert any("large-v3" in t and "to download" in t for t in labels), labels
button = next(w for w in widgets() if isinstance(w, tk.Button) and w.cget("text") == "Download the model")
rover.threading.Thread = InlineThread
button.invoke()
rover.threading.Thread = __import__("threading").Thread
print(seen["text"])
assert "MB" in seen["text"] and "%" in seen["text"]
pump(0.4)
print([w.cget("text") for w in widgets() if isinstance(w, tk.Label) and ("MB" in str(w.cget("text")) or "stopped" in str(w.cget("text")))])
assert any("stopped for the test" in str(w.cget("text")) for w in widgets() if isinstance(w, tk.Label))
print("PASS")
app.quit()
