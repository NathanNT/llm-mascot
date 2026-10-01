"""Manual check (needs a Windows desktop): the whole square around the mascot catches the mouse, not only its opaque pixels."""
import ctypes
import json
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"
prefs.SETTINGS_FILE.write_text(json.dumps({"mascot": "rover"}))

import layered
import numpy as np
import rover

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.handle_hover = lambda now: None
app.root.update()
user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND


def window_at(x, y):
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    return user32.GetAncestor(hwnd, 2) if hwnd else 0


mine = layered.toplevel_hwnd(app.root)
alpha = np.asarray(app.mset.clips["idle"].frames[0].getchannel("A"))
left, top, right, bottom = app.idle_box
empty = [(x, y) for y in range(top, bottom) for x in range(left, right)
         if 0 <= y - rover.PAD < alpha.shape[0] and 0 <= x - rover.PAD < alpha.shape[1] and alpha[y - rover.PAD, x - rover.PAD] == 0]
assert empty, "the default mascot has transparent pixels inside its box"
caught = [window_at(app.root.winfo_rootx() + x, app.root.winfo_rooty() + y) == mine for x, y in empty[:: max(1, len(empty) // 40)]]
print(f"{sum(caught)}/{len(caught)} transparent pixels inside the box catch the mouse")
assert all(caught)
outside = window_at(app.root.winfo_rootx() + left - 30, app.root.winfo_rooty() + top - 30)
assert outside != mine, "clicks far outside the box still go to the window behind"
print("PASS")
app.quit()
