"""Manual check (needs a Windows desktop): resting on the Claude logo lists the installed ways to start a chat; a click launches one."""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"
import json
import os
prefs.SETTINGS_FILE.write_text(json.dumps({"theme": os.environ.get("ROVER_TEST_THEME", "dark")}))

import launchers
import rover

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.update_pref(ui_language="en")
launched = []
launchers.launch = launched.append
app.detect_launchers()
app.root.update()
assert len(app.launch_options["claude"]) >= 2, app.launch_options

app.set_revealed(True)
app.finish_animations()
app.root.update()
rail = app.rail.win
claude_index = next(i for i, a in enumerate(app.prefs["apps"]) if a["kind"] == "claude")


def point_at_claude():
    if not rail.winfo_ismapped() or app.rail.p < 1:          # like a person: first onto the mascot, which reveals the rail
        left, top, right, bottom = app.idle_box
        ctypes.windll.user32.SetCursorPos(app.root.winfo_rootx() + (left + right) // 2, app.root.winfo_rooty() + (top + bottom) // 2)
        return
    low, high = app.rail_ranges[claude_index]
    ctypes.windll.user32.SetCursorPos(rail.winfo_rootx() + rail.winfo_width() // 2, rail.winfo_rooty() + (low + high) // 2)


aim = False


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        if aim:
            point_at_claude()                # the rail may still be sliding into place
        app.handle_hover(time.time())
        time.sleep(0.03)


app.set_revealed(True)
ctypes.windll.user32.SetCursorPos(5, 5)
pump(0.6)
app.set_revealed(True)
aim = True
pump(0.05)
assert app.flyout_index is None, "the menu must wait a moment, not flash on a fly-by"
pump(1.2)
assert app.flyout_index == claude_index and app.flyout.win.winfo_ismapped()
choices = app.flyout_choices
print([c["label"] for c in choices], [bool(c["badge"]) for c in choices], [p is not None for p in (app.badge_icons.get(c["badge"]) for c in choices)])
assert len(app.flyout_ranges) == len(app.launch_options["claude"]) - 1 >= 2      # the desktop app is the logo itself
from PIL import ImageGrab
win = app.flyout.win
ImageGrab.grab((min(win.winfo_rootx(), rail.winfo_rootx()) - 10, rail.winfo_rooty() - 10, max(win.winfo_rootx() + win.winfo_width(), rail.winfo_rootx() + rail.winfo_width()) + 10, rail.winfo_rooty() + rail.winfo_height() + 10)).save(Path(__file__).resolve().parents[2] / "design" / "flyout.png")
aim = False
low, high = app.flyout_ranges[-1]
ctypes.windll.user32.SetCursorPos(win.winfo_rootx() + (low + high) // 2, win.winfo_rooty() + win.winfo_height() // 2)
pump(0.4)
assert app.flyout_index == claude_index, "moving onto the menu keeps it open"
app.flyout.label.event_generate("<Button-1>", x=(low + high) // 2, y=20)
app.root.update()
assert launched and launched[0]["type"] == "url" and app.flyout_index is None
ctypes.windll.user32.SetCursorPos(5, 5)
pump(0.6)
print("PASS")
app.quit()
