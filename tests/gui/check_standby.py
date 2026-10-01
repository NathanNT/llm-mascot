"""Manual check (needs a Windows desktop): after a while unused the mascot shrinks, then hides in the notification area; coming back restores it."""
import ctypes
import json
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"
prefs.SETTINGS_FILE.write_text(json.dumps({"idle_shrink": 5, "idle_hide": 15, "ui_language": "en"}))

import layered
import rover
import tray

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.root.update()
assert app.tray.showing() and app.tray.present(), "the icon is there from the start"
user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND


hold_away = {"on": True}                      # keep the pointer in the corner, whatever the person at the keyboard does


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        if hold_away["on"]:
            user32.SetCursorPos(2, 2)
        app.root.update()
        app.handle_hover(time.monotonic())
        app.poll_tray()
        time.sleep(0.03)


def window_at(x, y):
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    return user32.GetAncestor(hwnd, 2) if hwnd else 0


def away():
    user32.SetCursorPos(2, 2)
    pump(0.6)


def point_on_mascot():
    left, top, right, bottom = app.visible_box()
    return int(app.root.winfo_rootx() + (left + right) / 2), int(app.root.winfo_rooty() + (top + bottom) / 2)


away()
assert app.scale == 1.0 and not app.hidden
# --- shrink after 5 minutes without use
app.last_activity = time.monotonic() - 5 * 60 - 5
pump(1.8)
assert app.scale_target == rover.SHRUNK and abs(app.scale - rover.SHRUNK) < 0.02, (app.scale, app.scale_target)
left, top, right, bottom = app.visible_box()
full = app.idle_box
assert (right - left) < 0.5 * (full[2] - full[0]) and abs(bottom - full[3]) < 1, "it shrinks towards the point under its feet"
mine = layered.toplevel_hwnd(app.root)
x, y = point_on_mascot()
assert window_at(x, y) == mine, "the small mascot can be grabbed"
corner = (app.root.winfo_rootx() + full[0] + 4, app.root.winfo_rooty() + full[1] + 4)
assert window_at(*corner) != mine, "where it used to stand is click-through again"
# --- coming back (the pointer reaches it) restores the normal size
hold_away["on"] = False
user32.SetCursorPos(x, y)
pump(1.5)
hold_away["on"] = True
assert app.scale == 1.0 and app.scale_target == 1.0, (app.scale, app.scale_target)
# --- hide in the notification area after 15 minutes
away()
app.last_activity = time.monotonic() - 15 * 60 - 5
pump(0.6)
assert app.hidden and not app.root.winfo_ismapped()
assert app.tray.showing() and app.tray.present(), "the icon is in the notification area"
# --- a click on the icon brings it back, small first and then growing
user32.PostMessageW(app.tray.hwnd, tray.WM_TRAY, 1, tray.WM_LBUTTONUP)
pump(1.8)
assert not app.hidden and app.root.winfo_ismapped() and app.scale == 1.0
assert app.tray.showing() and app.tray.present(), "the icon stays: it is always in the notification area"
# --- the icon's menu hides and shows it, and the icon comes back by itself when Windows loses it (Explorer restarted)
app.tray.events.put("hide")
pump(0.6)
assert app.hidden, "the menu's Hide tucks it away"
app.tray.events.put("show")
pump(1.8)
assert not app.hidden and app.scale == 1.0
tray.shell32.Shell_NotifyIconW(tray.NIM_DELETE, ctypes.byref(app.tray._data()))
assert not app.tray.present(), "Windows lost the icon"
app.tray.ensure()
pump(0.6)
assert app.tray.present(), "it is put back"
# --- the dictation shortcut also brings it back
app.hide_to_tray()
assert app.hidden
app.toggle_voice = rover.RoverApp.toggle_voice.__get__(app)
calls = []
app.recorder.start = lambda language: calls.append(language) or True
away_hidden = app.hidden
app.last_external_hwnd = user32.GetShellWindow()           # a window to dictate into
app.toggle_voice(from_hotkey=True)
pump(0.5)
assert away_hidden and not app.hidden and calls, "the shortcut restores the mascot and starts dictating"
print("PASS")
app.recorder.stop()
app.quit()
