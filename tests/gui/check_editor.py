"""Manual check (needs a Windows desktop): the mascot editor changes an event's animation and moves the icons, and both stick."""
import json
import os
import shutil
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
mascot = os.environ.get("ROVER_TEST_MASCOT", "pack:Clippy" if (ROOT / "mascots" / "Clippy").is_dir() else "rover")
if mascot.startswith("pack:"):
    shutil.copytree(ROOT / "mascots" / mascot[5:], prefs.MASCOT_DIR / mascot[5:])
prefs.SETTINGS_FILE.write_text(json.dumps({"mascot": mascot, "theme": os.environ.get("ROVER_TEST_THEME", "dark"), "ui_language": os.environ.get("ROVER_TEST_LANG", "en")}))

import mascot_setup
import rover

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.handle_hover = lambda now: None
app.root.update()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.03)


app.open_mascot_editor()
editor = app.mascot_editor
pump(0.5)
clips = sorted(app.mset.clips, key=str.lower)
print(len(clips), "animations:", clips[:6])
# choose an animation for "Mouse over" (second row of the event list)
editor.event_list.selection_clear(0, "end")
editor.event_list.selection_set(1)
editor.pick_event()
pump(0.3)
target = next(name for name in clips if name != app.mset.automatic("hover") and name != "idle")
row = editor.clip_names.index(target)
editor.clip_list.selection_clear(0, "end")
editor.clip_list.selection_set(row)
editor.pick_clip()
pump(0.3)
assert editor.animations == {"hover": target}, editor.animations
assert editor.event_list.get(1).endswith(target), editor.event_list.get(1)

# drag the microphone icon 60 px to the right and 25 px down
canvas = editor.canvas
x0, y0 = canvas.coords(editor.items["bottom"])
event = type("E", (), {})()
canvas.event_generate("<Motion>", x=int(x0) + 10, y=int(y0) + 10)
canvas.update()
editor.grab = ("bottom", int(x0) + 10, int(y0) + 10, list(editor.layout["bottom"]))
event.x, event.y = int(x0) + 70, int(y0) + 35
editor.drag(event)
editor.drop(event)
x1, y1 = canvas.coords(editor.items["bottom"])
assert (round(x1 - x0), round(y1 - y0)) == (60, 25), (x1 - x0, y1 - y0)
pump(0.3)
from PIL import ImageGrab
x, y = editor.top.winfo_rootx(), editor.top.winfo_rooty()
ImageGrab.grab((x, y, x + editor.top.winfo_width(), y + editor.top.winfo_height())).save(Path(os.environ.get("ROVER_TEST_SHOT") or ROOT / "design" / "editor.png"))

before = mascot_setup.offset(app.prefs, mascot, "bottom")
editor.save()
pump(0.3)
saved = json.loads(prefs.SETTINGS_FILE.read_text(encoding="utf-8"))
assert saved["animations"] == {mascot: {"hover": target}} and saved["layout"] == {mascot: {"bottom": [60, 25]}}, saved
assert app.mset.find("hover") == target
assert mascot_setup.offset(app.prefs, mascot, "bottom") == (60, 25) and before == (0, 0)
assert not editor.alive()
# the real microphone button really moved
default_x = app.root.winfo_x() + app.anchor_x - rover.BUTTON // 2
actual = app.btn_bottom.base[0]                      # where the button was placed (its window is hidden until you hover)
print("microphone button x:", actual, "default:", default_x)
assert abs((actual - default_x) - 60) <= 1
print("PASS")
app.quit()
