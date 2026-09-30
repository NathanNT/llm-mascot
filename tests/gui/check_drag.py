"""Exercise real Tk window movement without recording or spending quota."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from types import SimpleNamespace
import rover
from windows import monitor_work_area, desktop_bounds, place_window

rover.read_quotas = lambda *a: []
app = rover.RoverApp()
app.root.title("Rover drag verification")
app.handle_hover = lambda now: None          # the test drives the bubbles explicitly
calls = []
app.toggle_voice = lambda: calls.append("dictation")

left, top, right, bottom = monitor_work_area(*app.root.winfo_pointerxy())
start_x = (left + right) // 2
start_y = (top + bottom) // 2
place_window(app.root, start_x, start_y)
app.root.update()
app.set_revealed(True)
app.open_conso()
app.open_dictation()
app.finish_animations()
app.root.update()


import ctypes
from ctypes import wintypes
import layered


def real_position(widget):
    """Position the OS reports (layered windows are moved without Tk hearing about it)."""
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(layered.toplevel_hwnd(widget), ctypes.byref(rect))
    return rect.left, rect.top


def positions():
    windows = {"pet": app.root, "top": app.btn_top.win, "bottom": app.btn_bottom.win,
               "conso": app.conso.win, "dictation": app.dictation.win, "rail": app.rail.win}
    return {name: real_position(w) for name, w in windows.items()}


before = positions()
down = SimpleNamespace(x_root=start_x + 75, y_root=start_y + 84)
app.mouse_down(down)
app.mouse_drag(SimpleNamespace(x_root=down.x_root + 40, y_root=down.y_root - 30))
app.root.update()
during = positions()
dx, dy = during["pet"][0] - before["pet"][0], during["pet"][1] - before["pet"][1]
assert (dx, dy) == (40, -30), (dx, dy)
windows_by_name = {"top": app.btn_top.win, "bottom": app.btn_bottom.win, "conso": app.conso.win,
                   "dictation": app.dictation.win, "rail": app.rail.win}
for name in ("top", "bottom", "conso", "dictation", "rail"):
    if not windows_by_name[name].winfo_ismapped():
        continue  # a button hidden behind its open bubble
    moved = (during[name][0] - before[name][0], during[name][1] - before[name][1])
    assert moved == (dx, dy), f"{name} did not follow the mascot during the drag: {moved}"
assert app.conso.visible() and app.dictation.visible(), "A bubble disappeared while dragging"
assert not calls, "Dragging triggered dictation"
app.mouse_drag(down)
app.root.update()
app.mouse_up(down)
assert not calls, "A drag returning to its origin became a click"
app.mouse_down(down)
app.mouse_up(down)
assert calls == ["dictation"], "A normal click must still work"

# Left-hand monitors have negative coordinates; everything must stay in the monitor's usable area.
virtual_left, virtual_top, _, _ = desktop_bounds()
place_window(app.root, virtual_left + 20, virtual_top + 300)
app.root.update()
app.position_all()
app.root.update()
l, t, r, b = monitor_work_area(app.root.winfo_x() + 75, app.root.winfo_y() + 84)
for name, popup in (("conso", app.conso), ("dictation", app.dictation), ("rail", app.rail)):
    px, py = real_position(popup.win)
    width, height = popup.size
    assert l <= px <= r - width, f"{name} left the monitor horizontally"
    assert t <= py <= b - height, f"{name} left the monitor vertically"

# Hover behaviour: the round buttons stand in for their bubble until the pointer reaches them.
app.conso.hide()
app.dictation.hide()
app.set_revealed(False)
app.finish_animations()
app.root.update()
assert not any(w.win.winfo_ismapped() for w in app.animated), "Nothing may show until Rover is hovered"
app.set_revealed(True)
app.finish_animations()
app.root.update()
assert app.btn_top.win.winfo_ismapped() and app.btn_bottom.win.winfo_ismapped() and app.rail.win.winfo_ismapped()
assert not app.conso.visible() and not app.dictation.visible(), "Bubbles must start collapsed"
app.open_conso()
app.root.update()
assert app.conso.visible(), "Hovering the gauge button must open the consumption bubble"

# Personalisation: size and mascot changes rebuild the mascot window around its centre.
centre = (app.root.winfo_x() + app.mascot_w / 2, app.root.winfo_y() + app.mascot_h / 2)
app.update_pref(size=96)
app.root.update()
assert app.frames[0].width <= 96 and app.frames[0].width > 60, app.frames[0].width
new_centre = (app.root.winfo_x() + app.mascot_w / 2, app.root.winfo_y() + app.mascot_h / 2)
assert abs(new_centre[0] - centre[0]) <= 1 and abs(new_centre[1] - centre[1]) <= 1
app.update_pref(mascot="dot", theme="light", side="left")
app.root.update()
assert app.prefs["mascot"] == "dot" and app.mascot_w < 96
print({"result": "PASS", "drag_delta": (dx, dy), "followed": "buttons, bubbles and rail",
       "monitor_work_area": (l, t, r, b)})
app.quit()
