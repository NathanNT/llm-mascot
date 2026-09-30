"""Manual check (needs a Windows desktop and moves the mouse for a couple of seconds).

Clicking the mascot, its buttons and its panels must never take the keyboard focus away from the application being
dictated into, and the text must be typed after the existing draft instead of replacing it.
"""
import ctypes
import json
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"

import layered
import rover
from windows import foreground_window

user32 = ctypes.windll.user32
rover.read_quotas = lambda *a: []

def bring_to_front(hwnd):
    """Windows only lets the foreground process change focus; a bare Alt tap lifts that restriction for tests."""
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)

report = work / "target.json"
target = subprocess.Popen([sys.executable, str(Path(__file__).with_name("_target_window.py")), str(report)])
try:
    deadline = time.monotonic() + 10
    while not report.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    info = json.loads(report.read_text(encoding="utf-8"))
    hwnd = info["hwnd"]
    bring_to_front(hwnd)
    time.sleep(0.4)
    assert foreground_window() == hwnd, "the fake chat window could not be brought to the front"

    app = rover.RoverApp()
    app.handle_hover = lambda now: None
    app.recorder.start = lambda language: True            # no microphone needed
    app.root.update()

    def pump(seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.root.update()
            time.sleep(0.005)

    def rect(widget):
        box = wintypes.RECT()
        user32.GetWindowRect(layered.toplevel_hwnd(widget), ctypes.byref(box))
        return box.left, box.top, box.right, box.bottom

    def click(x, y):
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        pump(0.35)

    app.set_revealed(True)
    app.open_dictation()
    app.finish_animations()
    pump(0.5)

    # 1. click the mascot itself
    left, top, right, bottom = rect(app.root)
    ix0, iy0, ix1, iy1 = app.idle_box
    click(left + (ix0 + ix1) / 2, top + (iy0 + iy1) / 2)
    assert foreground_window() == hwnd, "clicking the mascot stole the focus"
    assert app.target_hwnd == hwnd, "the mascot did not remember the field to type into"
    # 2. click the microphone button / the panel mic (whichever is on screen)
    if app.btn_bottom.win.winfo_ismapped():
        x0, y0, x1, y1 = rect(app.btn_bottom.win)
        click((x0 + x1) / 2, (y0 + y1) / 2)
        assert foreground_window() == hwnd, "clicking the microphone button stole the focus"
    x0, y0, x1, y1 = rect(app.dictation.win)
    click(x0 + 60, y0 + 60)
    assert foreground_window() == hwnd, "clicking inside the dictation panel stole the focus"

    # 3. the rewritten text is typed after the draft, line breaks do not submit anything
    app.busy = True
    app.complete_rewrite("First line.\nSecond line.")
    pump(0.6)
    text = json.loads(report.read_text(encoding="utf-8"))["text"]
    assert text == "My draft prompt that must survive. First line.\nSecond line.", repr(text)

    # 4. the clipboard alternative
    app.prefs["insert"] = "copy"
    app.complete_rewrite("Only on the clipboard")
    pump(0.3)
    assert app.root.clipboard_get() == "Only on the clipboard"
    assert json.loads(report.read_text(encoding="utf-8"))["text"] == text, "copy mode must not type anything"
    print({"result": "PASS", "focus": "never stolen", "draft": "kept", "clipboard": "works"})
    app.quit()
finally:
    Path(str(report) + ".stop").write_text("stop")
    time.sleep(0.4)
    target.terminate()
