"""Renders the README images from the real application on a clean wallpaper (no private desktop, demo data only).

    python tools/make_screenshots.py --lang en --theme dark --out docs/img/en
"""

from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--lang", default="en")
parser.add_argument("--theme", default="dark")
parser.add_argument("--out", default=str(ROOT / "docs" / "img" / "en"))
parser.add_argument("--gif", action="store_true", help="also record the animated demo")
parser.add_argument("--plain", action="store_true", help="use only the bundled original mascot")
args = parser.parse_args()

import settings as prefs   # noqa: E402

workdir = Path(tempfile.mkdtemp())
prefs.SETTINGS_FILE = workdir / "settings.json"
prefs.MASCOT_DIR = workdir / "mascots"
prefs.codex_pets = lambda: []          # never show third-party artwork in public images
prefs.MASCOT_DIR.mkdir(parents=True)
ORDER = ["Clippy", "Merlin", "Genie", "Rocky", "Peedy", "F1", "Genius", "Rover"]
EXCLUDE = {"Links"}           # not shown in the documentation images
DISPLAY = {"Rover": "Rover XP"}
# Classic assistants are only present if installed with tools/get_agents.py; nothing third-party is ever committed.
PACKS = [] if args.plain else sorted((p for p in (ROOT / "mascots").glob("*") if (p / "agent.js").is_file() and (p / "map.png").is_file() and p.name not in EXCLUDE),
                                     key=lambda p: ORDER.index(p.name) if p.name in ORDER else 99)
for pack_folder in PACKS:
    shutil.copytree(pack_folder, prefs.MASCOT_DIR / pack_folder.name)
hero_mascot = "pack:Clippy" if any(p.name == "Clippy" for p in PACKS) else "rover"
clippy = ROOT / "mascots" / "Clippy"
prefs.SETTINGS_FILE.write_text(json.dumps({"ui_language": args.lang, "language": args.lang, "theme": args.theme, "size": 144,
                                           "mascot": hero_mascot}))

from PIL import Image, ImageDraw, ImageFont, ImageGrab   # noqa: E402

import core       # noqa: E402
import layered    # noqa: E402
import rover      # noqa: E402
from i18n import tr   # noqa: E402

rover.ROVER = ROOT / "assets" / "mascot-default.png"
out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)

NOW = time.time()


def demo_cards():
    return [
        {"name": "Codex Pro", "plan": "Pro", "status": "ok", "extra_credits": "1250",
         "windows": [{"label": core.window_label(300), "used": 38, "reset": NOW + 3 * 3600},
                     {"label": core.window_label(10080), "used": 61, "reset": NOW + 4 * 86400}]},
        {"name": "Codex Secondary", "plan": tr("Secondary"), "status": "ok",
         "windows": [{"label": core.window_label(10080), "used": 22, "reset": NOW + 5 * 86400}]},
        {"name": "Claude", "status": "", "url": core.CLAUDE_USAGE_URL},
    ]


rover.read_quotas = lambda *a: demo_cards()

left, top, right, bottom = rover.monitor_work_area(0, 0)       # primary monitor
WIDTH, HEIGHT = 600, min(bottom - top - 10, 990)
box_x, box_y = left + 160, top + 5
backdrop = subprocess.Popen([sys.executable, str(ROOT / "tools" / "_backdrop.py"), str(box_x), str(box_y), str(WIDTH), str(HEIGHT),
                             "1" if args.theme == "dark" else "0"])
time.sleep(1.2)
app = rover.RoverApp()
app.handle_hover = lambda now: None
virtual_left, virtual_top = rover.desktop_bounds()[:2]


def pump(seconds: float = 0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        time.sleep(0.004)


def grab(region=None) -> Image.Image:
    x0, y0, x1, y1 = region or (box_x, box_y, box_x + WIDTH, box_y + HEIGHT)
    full = ImageGrab.grab(all_screens=True)
    return full.crop((x0 - virtual_left, y0 - virtual_top, x1 - virtual_left, y1 - virtual_top))


def window_rect(widget):
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(layered.toplevel_hwnd(widget), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


try:
    app.quota_time = __import__("datetime").datetime.now()
    app.cards = demo_cards()
    app.notice = ""
    # put Rover in the middle of the wallpaper
    rover.place_window(app.root, box_x + WIDTH // 2 - 30 - app.anchor_x, box_y + 500 - app.idle_box[1])
    app.paint_mascot()
    app.position_all()
    pump(0.6)
    if app.mset.find("hover") and hero_mascot != "rover":
        app.react("hover")

    def show_all():
        app.set_revealed(True)
        app.open_conso()
        app.open_dictation()
        app.finish_animations()
        pump(0.5)

    # ---- hero: everything open
    show_all()
    grab().save(out / "hero.png")

    # ---- dictation states
    states = []
    fake_thread = type("T", (), {"is_alive": lambda self: True})()
    scenarios = [
        ("ready", lambda: None),
        ("recording", lambda: (setattr(app.recorder, "_thread", fake_thread), app.recorder._stop.clear(), setattr(app, "rec_started", time.monotonic() - 7),
                               app.wave.extend([0.05 * ((i * 7) % 11) + 0.1 for i in range(30)]))),
        ("processing", lambda: (app.recorder._stop.set(), setattr(app, "busy", True), setattr(app, "step", "codex"))),
        ("text", lambda: (setattr(app, "busy", False), setattr(app, "pending_text", "Hi! Could you confirm the time of Thursday's meeting?"
                                                               if args.lang == "en" else "Bonjour, pouvez-vous me confirmer l’horaire de la réunion de jeudi ?"))),
        ("error", lambda: (setattr(app, "pending_text", ""), setattr(app, "error_msg", tr("Codex unavailable: {detail}").format(detail=tr("command interrupted"))),
                           setattr(app, "last_transcript", "hello"), setattr(app, "error_until", time.monotonic() + 999))),
    ]
    for name, apply in scenarios:
        apply()
        app.notice = ""
        app.update_status_visuals()
        app.render_dictation()
        app.position_all()
        app.finish_animations()
        pump(0.35)
        left_, top_, right_, bottom_ = window_rect(app.dictation.win)
        states.append(grab((left_ - 6, top_ - 6, right_ + 6, bottom_ + 6)))
    sheet = Image.new("RGB", (sum(s.width for s in states) + 16 * (len(states) - 1), max(s.height for s in states)), (20, 20, 24) if args.theme == "dark" else (232, 222, 204))
    x_cursor = 0
    for shot in states:
        sheet.paste(shot, (x_cursor, 0))
        x_cursor += shot.width + 16
    sheet.save(out / "states.png")
    app.error_msg = ""
    app.pending_text = ""
    app.recorder._stop.set()
    app.busy = False
    app.update_status_visuals()

    # ---- settings window
    app.conso.hide()
    app.dictation.hide()
    app.set_revealed(False)
    app.finish_animations()
    app.open_settings()
    pump(0.8)
    grab(window_rect(app.settings_window.top)).save(out / "settings.png")
    app.settings_window.toggle_page()
    advanced_page = app.settings_window.peer
    advanced_page.top.update()
    pump(0.6)
    grab(window_rect(advanced_page.top)).save(out / "advanced.png")
    app.prefs["transcription"]["engine"] = "openai"
    advanced_page.build()
    advanced_page.top.update()
    pump(0.6)
    grab(window_rect(advanced_page.top)).save(out / "advanced-service.png")
    app.prefs["transcription"]["engine"] = "gpu"
    advanced_page.build()
    advanced_page.top.update()
    pump(0.8)
    grab(window_rect(advanced_page.top)).save(out / "advanced-gpu.png")
    app.prefs["transcription"]["engine"] = "local"
    advanced_page.close()
    pump(0.3)

    # ---- the classic characters at work (processing animation)
    strip = []
    for pack_folder in PACKS:
        if pack_folder.name not in ("Clippy", "Merlin", "Genie", "Peedy", "F1"):
            continue
        app.update_pref(mascot="pack:" + pack_folder.name)
        app.set_revealed(False)
        app.finish_animations()
        app.busy = True
        app.step = "codex"
        app.update_status_visuals()
        app.open_dictation()
        app.finish_animations()
        pump(1.1)
        mx0, my0, mx1, my1 = window_rect(app.root)
        dx0, dy0, dx1, dy1 = window_rect(app.dictation.win)
        strip.append(grab((dx0 - 10, my0 - 18, dx1 + 10, dy1 + 10)))
        app.busy = False
        app.dictation.hide()
        app.update_status_visuals()
    if strip:
        strip_sheet = Image.new("RGB", (sum(s_.width for s_ in strip) + 14 * (len(strip) - 1), max(s_.height for s_ in strip)), (20, 20, 24))
        x_cursor = 0
        for shot in strip:
            strip_sheet.paste(shot, (x_cursor, 0))
            x_cursor += shot.width + 14
        strip_sheet.save(out / "characters.png")
    app.update_pref(mascot=hero_mascot)
    app.set_revealed(False)
    app.finish_animations()
    pump(0.4)

    # ---- animated demo
    if args.gif:
        frames: list[Image.Image] = []
        region = (box_x, box_y, box_x + WIDTH, box_y + HEIGHT)      # same frame as hero.png, so both display at the same size

        def record(seconds: float, each=None, fps: float = 12):
            end = time.monotonic() + seconds
            step = 1 / fps
            while time.monotonic() < end:
                started = time.monotonic()
                if each:
                    each()
                pump(0.02)
                frames.append(grab(region).convert("RGB"))
                while time.monotonic() - started < step:
                    app.root.update()
                    time.sleep(0.004)

        record(0.9)
        app.set_revealed(True)
        record(1.3)
        app.open_conso()
        record(1.9)
        app.close_conso()
        record(0.4)
        app.open_dictation()
        record(0.7)
        setattr(app.recorder, "_thread", fake_thread)
        app.recorder._stop.clear()
        app.rec_started = time.monotonic()
        app.update_status_visuals()
        import math
        tick = {"n": 0}

        def speak():
            tick["n"] += 1
            app.recorder.level = abs(math.sin(tick["n"] / 2.3)) * 0.18 + 0.02

        record(2.2, speak)
        app.recorder._stop.set()
        app.busy = True
        app.step = "whisper"
        app.update_status_visuals()
        record(1.0)
        app.step = "codex"
        app.update_status_visuals()
        record(1.2)
        app.busy = False
        app.pending_text = "Hi! Could you confirm the time of Thursday's meeting?" if args.lang == "en" else "Bonjour, pouvez-vous me confirmer l’horaire de la réunion de jeudi ?"
        app.update_status_visuals()
        app.react("success")
        record(1.9)
        app.pending_text = ""
        app.set_status(tr("Prompt inserted · nothing was sent"))
        record(1.5)
        app.notice_until = 0
        app.close_dictation()
        app.set_revealed(False)
        record(1.2)
        width = 360
        resized = [f.resize((width, round(f.height * width / f.width)), Image.Resampling.LANCZOS) for f in frames]
        sample = Image.new("RGB", (width, resized[0].height * 12))
        for slot, frame in enumerate(resized[:: max(1, len(resized) // 12)][:12]):
            sample.paste(frame, (0, slot * resized[0].height))
        palette = sample.quantize(colors=220, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in resized]
        quantized[0].save(out / "demo.gif", save_all=True, append_images=quantized[1:], duration=83, loop=0, optimize=True)
        print("gif frames:", len(frames), "size KB:", (out / "demo.gif").stat().st_size // 1024)
finally:
    app.quit()
    backdrop.terminate()

# ---- mascot gallery (original artwork only)
def font(size, bold=False):
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"),):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + name, size)
        except OSError:
            pass
    return ImageFont.load_default()


dark = args.theme == "dark"
bg, fg, sub = ((22, 23, 28), (242, 242, 240), (163, 163, 168)) if dark else ((250, 246, 238), (43, 38, 32), (103, 94, 83))
import math       # noqa: E402

import mascots as mascot_lib   # noqa: E402


def animation_count(folder: Path) -> int:
    text = (folder / "agent.js").read_text(encoding="utf-8")
    return len(json.loads(text[text.index("{"): text.rindex("}") + 1])["animations"])


entries = []
for pack_folder in PACKS:
    preview = mascot_lib.pack_preview(pack_folder, (192, 208))
    if preview is not None:
        entries.append((DISPLAY.get(pack_folder.name, pack_folder.name), preview, f"{animation_count(pack_folder)} animations"))
if not entries:
    entries.append(("Rover", Image.open(ROOT / "assets" / "mascot-default.png").convert("RGBA").crop((0, 0, 192, 208)), "9 animations"))
columns = min(5, len(entries))
rows = math.ceil(len(entries) / columns)
gallery = Image.new("RGB", (columns * 176 + 32, rows * 250 + 24), bg)
draw = ImageDraw.Draw(gallery)
for index, (label, sprite, caption) in enumerate(entries):
    col, row = index % columns, index // columns
    left_ = 32 + col * 176
    top_ = 20 + row * 250
    cell = sprite.resize((144, 156), Image.Resampling.LANCZOS)
    gallery.paste(cell, (left_ - 8, top_), cell)
    draw.text((left_ + 64, top_ + 178), label, font=font(17, True), fill=fg, anchor="mm")
    draw.text((left_ + 64, top_ + 202), caption, font=font(13), fill=sub, anchor="mm")
gallery.save(out / "mascots.png")
print("images written to", out)
