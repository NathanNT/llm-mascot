"""Manual check (needs a Windows desktop): in the XP theme the title-bar close buttons show a hand and really close their window."""
import ctypes
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import settings as prefs

work = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = work / "settings.json"
prefs.MASCOT_DIR = work / "mascots"
prefs.SETTINGS_FILE.write_text(json.dumps({"theme": "xp", "ui_language": "en"}))

import rover

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.root.update()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        app.handle_hover(time.monotonic())
        time.sleep(0.03)


# a bubble's ×: hand cursor over it, arrow elsewhere, click closes it and it stays closed while the pointer is still there
app.set_revealed(True)
app.open_conso()
pump(0.5)
bubble = app.conso


class Event:
    def __init__(self, x, y):
        self.x, self.y = x, y


close_x, close_y = rover.BUBBLE_W - 3 - 14, 3 + 14
assert bubble._on_close_button(Event(close_x, close_y)) and not bubble._on_close_button(Event(40, 14))
bubble._title_hover(Event(close_x, close_y))
assert str(bubble.canvas.cget("cursor")) == "hand2"
bubble._title_hover(Event(40, 14))
assert str(bubble.canvas.cget("cursor")) == "arrow"
ctypes.windll.user32.SetCursorPos(bubble.win.winfo_rootx() + close_x, bubble.win.winfo_rooty() + close_y)
bubble._title_click(Event(close_x, close_y))
pump(0.6)
assert not app.conso.visible(), "the × closes the usage bubble"
pump(0.6)
assert not app.conso.visible(), "and it does not pop straight back open under the pointer"

# the settings window: hand over its close button, click closes it
app.open_settings()
pump(0.4)
window = app.settings_window
bar = next(w for w in window.top.winfo_children() if w.winfo_class() == "Canvas")
width = bar.winfo_width()
bar.event_generate("<Motion>", x=width - 10, y=12)
bar.update()
assert str(bar.cget("cursor")) == "hand2", bar.cget("cursor")
bar.event_generate("<Motion>", x=50, y=12)
bar.update()
assert str(bar.cget("cursor")) == "arrow"
bar.event_generate("<ButtonPress-1>", x=width - 10, y=12)
pump(0.3)
assert not window.visible
print("PASS")
app.quit()
