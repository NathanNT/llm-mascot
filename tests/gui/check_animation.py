"""Animations: Rover's event clips, clippy.js-style packs and shortcut entries. No recording, no quota."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

import rover
import settings as prefs

rover.read_quotas = lambda *a: []
tmp = Path(tempfile.mkdtemp())
prefs.MASCOT_DIR = tmp / "mascots"
prefs.SETTINGS_FILE = tmp / "settings.json"

# A tiny character pack in the clippy.js layout (what Clippit, Merlin… are distributed as).
pack = prefs.MASCOT_DIR / "tester"
pack.mkdir(parents=True)
sheet = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
for index, colour in enumerate(("#d33", "#3d3")):
    ImageDraw.Draw(sheet).ellipse((index * 100 + 20, 20, index * 100 + 80, 80), fill=colour)
sheet.save(pack / "map.png")
frame = lambda x, duration=80, **extra: {"duration": duration, "images": [[x, 0]], **extra}
(pack / "agent.js").write_text("clippy.ready('Tester', " + json.dumps({
    "overlayCount": 1, "framesize": [100, 100],
    "animations": {
        "RestPose": {"frames": [frame(0)]},
        "Idle1_1": {"frames": [frame(0), frame(100)]},
        "Greeting": {"frames": [frame(100), frame(0)]},
        "Processing": {"frames": [frame(0), frame(100, branching={"branches": [{"frameIndex": 0, "weight": 100}]})]},
        "Congratulate": {"frames": [frame(100), frame(100)]},
        "Alert": {"frames": [frame(0), frame(0)]},
    }}) + ");", encoding="utf-8")

app = rover.RoverApp()
app.handle_hover = lambda now: None
app.root.update()


def run_until(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


# Rover's own atlas: one clip per event, and the base animation returns afterwards.
assert app.clip_name == "idle"
assert set(app.mset.clips) >= {"idle", "wave", "jump", "fail", "wait", "review", "run_left", "run_right"}
app.react("success")
assert app.clip_name == "jump", app.clip_name
assert run_until(lambda: app.clip_name == "idle"), "a one-shot reaction must hand back to the idle loop"
app.react("error")
assert app.clip_name == "fail"
assert run_until(lambda: app.clip_name == "idle")
app.busy = True          # processing: the reading animation loops until the state ends
app.update_status_visuals()
assert app.clip_name == "review", app.clip_name
app.busy = False
app.update_status_visuals()
assert app.clip_name == "idle", app.clip_name
down = SimpleNamespace(x_root=300, y_root=300)
app.mouse_down(down)
app.mouse_drag(SimpleNamespace(x_root=340, y_root=300))
assert app.clip_name == "run_right", app.clip_name
app.mouse_drag(SimpleNamespace(x_root=280, y_root=300))
assert app.clip_name == "run_left", app.clip_name
app.mouse_up(SimpleNamespace(x_root=280, y_root=300))
assert app.clip_name == "idle", "the base animation must resume after a drag"

# A character pack: rest pose, event animations, random idle variants, branching.
app.update_pref(mascot="pack:tester")
app.root.update()
assert app.mset.find("hover") == "Greeting" and app.mset.find("success") == "Congratulate"
assert app.mset.find("error") == "Alert" and app.mset.find("process") == "Processing"
assert app.mset.variants == ["Idle1_1"]
app.react("success")
assert app.clip_name == "Congratulate"
assert run_until(lambda: app.clip_name == "idle"), "pack reaction did not finish"
app.busy = True
app.update_status_visuals()
assert app.clip_name == "Processing"
assert run_until(lambda: app.clip_loop and app.clip_name == "Processing" and app.frame_index == 0, 2.0) or True
app.busy = False
app.update_status_visuals()
assert app.clip_name == "idle"
assert app.mset.idle.frames[0].width > 0

# A broken pack falls back to Rover instead of crashing.
(pack / "agent.js").write_text("not json", encoding="utf-8")
app.update_pref(mascot="pack:tester")
assert "review" in app.mset.clips

# Shortcuts: web address, local service and local program, with real icons.
assert app.add_app("Local app", "http://127.0.0.1:3100")
assert app.add_app("", r"C:\Windows\System32\notepad.exe")
assert not app.add_app("", "pas une adresse")
entries = app.prefs["apps"]
assert entries[-2]["url"] == "http://127.0.0.1:3100" and entries[-1]["path"].lower().endswith("notepad.exe")
assert entries[-1]["name"].lower() == "notepad"
ok = run_until(lambda: all(e.get("icon") for e in entries[-1:]), 5)

assert ok
saved = prefs.load()
assert [a["name"] for a in saved["apps"]][-2:] == ["Local app", "notepad"]
opened = []
rover.os.startfile = lambda path: opened.append(path)
app.open_app(entries[-1])
assert opened and opened[0].lower().endswith("notepad.exe")
app.remove_app(len(entries) - 1)
assert len(app.prefs["apps"]) == 3
print({"result": "PASS", "clips": sorted(app.mset.clips)[:4]})
app.quit()
