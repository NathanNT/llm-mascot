"""Rover: a floating Windows dictation and quota companion."""

from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import i18n
from i18n import tr
from PIL import Image, ImageDraw, ImageSequence, ImageTk

import appicons
import accel
import gpu
import layered
import secrets_store
import transcribe
import startup
import styles
import mascots
import settings as prefs
from core import (CLAUDE_USAGE_URL, claude_available, codex_available, format_credits, format_reset, provider_label,
                  read_quotas, rewrite_enabled, rewrite_text)
from ui_kit import (CLAUDE_COLOR, FONT_MONO, FONT_REGULAR, FONT_SEMIBOLD, SS, THEMES, TRANSPARENT, circle_button,
                    harden_edges, icon, icon_pil, letter_icon, panel_image, rail_image, rounded_box, rgb)
from voice import Recorder
from windows import (acquire_single_instance, consume_hotkey, foreground_window, insert_text, is_own_window,
                     register_hotkey, unregister_hotkey, desktop_bounds, monitor_work_area, place_window)

HERE = Path(__file__).resolve().parent
# A sprite atlas dropped at assets/rover.png replaces the bundled default mascot.
ROVER = HERE / "assets" / "rover.png" if (HERE / "assets" / "rover.png").is_file() else HERE / "assets" / "mascot-default.png"
SPRITE_W, SPRITE_H, SPRITE_FRAMES = 192, 208, 6
PAD = 8                 # transparent margin around the mascot
BUBBLE_W = 340          # outer width of the two bubbles
BUBBLE_PAD = 17         # panel border (1) + inner padding (16)
INNER_W = BUBBLE_W - 2 * BUBBLE_PAD
BUTTON = 44
GAP = 8
TAIL = 7
CLOSE_DELAY = 0.15
REVEAL_GRACE = 0.25


def photo(image: Image.Image) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(image)


def make_popup(master, per_pixel: bool = False) -> tk.Toplevel:
    popup = tk.Toplevel(master)
    popup.withdraw()
    popup.overrideredirect(True)
    popup.attributes("-topmost", True)
    popup.configure(bg=TRANSPARENT)
    if not per_pixel:
        popup.wm_attributes("-transparentcolor", TRANSPARENT)
    return popup


class Animated:
    """Fade-and-slide open/close animation shared by every popup around the mascot."""

    DURATION = 0.13

    def init_anim(self, kick, slide=(0, 0)):
        self.kick = kick
        self.slide = slide
        self.p = 0.0
        self.target = 0
        self.base = (0, 0)
        self._alpha = None

    per_pixel = False
    bitmap = None

    def _move(self):
        eased = 1 - (1 - self.p) ** 3
        x = self.base[0] + self.slide[0] * (1 - eased)
        y = self.base[1] + self.slide[1] * (1 - eased)
        if self.per_pixel:
            if self.bitmap is not None and self.win.winfo_ismapped():
                hwnd = layered.enable(self.win, no_activate=True)
                layered.update(hwnd, self.bitmap, round(x), round(y), round(255 * eased))
            return
        alpha = max(0.02, round(eased, 2))
        if alpha != self._alpha:
            self._alpha = alpha
            self.win.attributes("-alpha", alpha)
        place_window(self.win, round(x), round(y), *self.size)

    def place(self, x: int, y: int) -> None:
        self.base = (x, y)
        self._move()

    def appear(self) -> None:
        if self.target == 1:
            return
        if not self.win.winfo_ismapped():
            self.p = 0.0
            if not self.per_pixel:
                self._move()
            self.win.deiconify()
            self.win.lift()
            self.win.update_idletasks()
            if self.per_pixel:
                self._move()
            else:
                layered.no_activate(self.win)
        self.target = 1
        self.kick()

    show = appear

    def vanish(self) -> None:
        if self.target == 0:
            return
        self.target = 0
        self.kick()

    def hide(self) -> None:
        self.target = 0
        self.p = 0.0
        self.win.withdraw()

    def visible(self) -> bool:
        """True while the popup is open or opening (not while it fades out)."""
        return self.target == 1

    def step(self, dt: float) -> bool:
        """Advance the animation; returns True while it still has to move."""
        if self.p == self.target:
            if self.target == 0 and self.win.winfo_ismapped():
                self.win.withdraw()
            return False
        delta = dt / self.DURATION
        self.p = min(1.0, self.p + delta) if self.target else max(0.0, self.p - delta)
        self._move()
        if self.p == 0.0 and self.target == 0:
            self.win.withdraw()
        return self.p != self.target


class ImageWindow(Animated):
    """A borderless window that shows one anti-aliased image (round buttons, shortcut rail)."""

    per_pixel = True

    def __init__(self, master, kick, cursor: str = "hand2", slide=(0, 0)):
        self.init_anim(kick, slide)
        self.win = make_popup(master, per_pixel=True)
        self.win.configure(cursor=cursor)
        self.label = tk.Label(self.win, bg=TRANSPARENT, bd=0, highlightthickness=0, cursor=cursor)
        self.label.pack()
        self.image = None
        self.size = (1, 1)

    def set_image(self, image: Image.Image) -> None:
        self.image = photo(image)
        self.label.configure(image=self.image)
        self.size = image.size
        if self.bitmap is not None:
            self.bitmap.close()
        self.bitmap = layered.Bitmap(image)
        self._move()


class Bubble(Animated):
    """Rounded, anti-aliased panel with a tail, filled with regular Tk widgets."""

    def __init__(self, master, tail: str, kick, slide=(0, 0)):
        self.init_anim(kick, slide)
        self.win = make_popup(master)
        self.canvas = tk.Canvas(self.win, bg=TRANSPARENT, bd=0, highlightthickness=0)
        self.canvas.pack()
        self.body: tk.Frame | None = None
        self.anchor_tail = tail               # "down" (bubble above Rover) or "up" (below)
        self.tail: str | None = tail
        self.anchored = True
        self.size = (BUBBLE_W, 60)
        self.tail_x = BUBBLE_W // 2
        self._image = None
        self._theme: dict | None = None
        self._body_height = 0

    def rebuild(self, theme: dict, fill) -> None:
        self._theme = theme
        if self.body is not None:
            self.body.destroy()
        self.body = tk.Frame(self.canvas, bg=theme["panel"])
        fill(self.body)
        self.body.update_idletasks()
        self._body_height = self.body.winfo_reqheight()
        self.redraw()

    def set_mode(self, anchored: bool) -> None:
        """Anchored bubbles point at the mascot; side bubbles (no room above/below) have no tail."""
        if anchored != self.anchored:
            self.anchored = anchored
            self.tail = self.anchor_tail if anchored else None
            if self.body is not None:
                self.redraw()

    def set_tail(self, x: int) -> None:
        if abs(x - self.tail_x) > 2:
            self.tail_x = x
            if self.body is not None:
                self.redraw()

    def redraw(self) -> None:
        theme = self._theme
        height = self._body_height + 2 * BUBBLE_PAD
        image, top = panel_image(BUBBLE_W, height, theme["panel"], theme["border"], tail=self.tail, tail_x=self.tail_x,
                                 tail_size=TAIL)
        self._image = photo(harden_edges(image, theme["border"]))
        self.canvas.delete("all")
        self.canvas.configure(width=image.width, height=image.height)
        self.canvas.create_image(0, 0, anchor="nw", image=self._image)
        self.canvas.create_window(BUBBLE_PAD, top + BUBBLE_PAD, window=self.body, anchor="nw", width=INNER_W)
        self.size = image.size


def pointer_inside(window) -> bool:
    if not window.winfo_ismapped():
        return False
    px, py = window.winfo_pointerxy()
    return (window.winfo_rootx() <= px < window.winfo_rootx() + window.winfo_width() and
            window.winfo_rooty() <= py < window.winfo_rooty() + window.winfo_height())


def card(parent, theme: dict, width: int, build, pad: int = 12, min_height: int = 0, outer: str | None = None):
    """Rounded card (anti-aliased) around widgets created by `build(inner_frame)`."""
    outer = outer or theme["panel"]
    holder = tk.Canvas(parent, width=width, height=10, bg=outer, bd=0, highlightthickness=0)
    inner = tk.Frame(holder, bg=theme["card"])
    build(inner)
    inner.update_idletasks()
    height = max(min_height, inner.winfo_reqheight() + 2 * pad)
    holder.configure(height=height)
    holder._background = photo(rounded_box(width, height, theme["card"], theme["border"], 12, outer=outer))
    holder.create_image(0, 0, anchor="nw", image=holder._background)
    holder.create_window(pad, height // 2, window=inner, anchor="w", width=width - 2 * pad)
    return holder


def button(parent, theme: dict, text: str, command, primary: bool = False) -> tk.Button:
    if primary:
        return tk.Button(parent, text=text, command=command, bg=theme["accent"], fg=theme["on_accent"],
                         activebackground=theme["accent"], activeforeground=theme["on_accent"], relief="flat", bd=0,
                         padx=12, pady=6, font=(FONT_SEMIBOLD, 9), cursor="hand2")
    return tk.Button(parent, text=text, command=command, bg=theme["card"], fg=theme["text"],
                     activebackground=theme["border"], activeforeground=theme["text"], relief="flat", bd=0,
                     highlightthickness=1, highlightbackground=theme["border"], padx=12, pady=5,
                     font=(FONT_SEMIBOLD, 9), cursor="hand2")


def domain_name(url: str) -> str:
    host = urlparse(url).netloc or url
    host = host.removeprefix("www.")
    return host.split(".")[0].capitalize() if host else "App"


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    if any(ch.isspace() for ch in value):
        return ""
    parsed = urlparse(value)
    host = parsed.hostname or ""
    plausible = host == "localhost" or "." in host or ":" in host
    return value if parsed.scheme in ("http", "https") and plausible else ""


class RoverApp:
    def __init__(self):
        self.prefs = prefs.load()
        self.saved_prefs = prefs.load()
        i18n.set_language(self.prefs["ui_language"])
        self.root = tk.Tk()
        self.root.title("LLM Mascot")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT)
        self.root.report_callback_exception = self.log_exception
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        self.language = self.prefs["language"]
        self.last_external_hwnd = 0
        self.target_hwnd = 0
        self.busy = False
        self.pending_text = ""
        self.last_output = ""
        self.notice = ""
        self.notice_until = 0.0
        self.step = "whisper"
        self.error_msg = ""
        self.error_until = 0.0
        self.last_transcript = ""
        self.last_language = self.language
        self.cards: list[dict] | None = None
        self.quota_loading = False
        self.quota_time: datetime | None = None
        self.last_quota_refresh = 0.0
        self.drag_origin: tuple[int, int, int, int] | None = None
        self.dragged = False
        self.rec_started = 0.0
        self.wave = deque([0.0] * 30, maxlen=30)
        self.spin = 0
        self.conso_last_over = 0.0
        self.dict_last_over = 0.0
        self.settings_window: SettingsWindow | None = None
        self.rail_ranges: list[tuple[int, int]] = []
        self.live_widgets: dict = {}

        self.frames: list[Image.Image] = []
        self.mset: mascots.MascotSet | None = None
        self.clip_name, self.frame_index, self.clip_loop, self.frames_played = "idle", 0, True, 0
        self.next_frame_at = 0.0
        self.next_variant = time.monotonic() + random.uniform(9, 22)
        self.base_ev = "idle"
        self.drag_event: str | None = None
        self.last_drag_x = 0
        self.mascot_bitmaps: dict = {}
        self.badge_color = "#5ccf98"
        self.canvas = tk.Canvas(self.root, width=10, height=10, bg=TRANSPARENT, bd=0, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.mouse_down)
        self.canvas.bind("<B1-Motion>", self.mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.mouse_up)
        self.canvas.bind("<Button-3>", self.show_menu)

        self.revealed = False
        self.hover_last = 0.0
        self.anim_running = False
        self.last_frame = 0.0
        self.btn_top = ImageWindow(self.root, self.kick_animation, slide=(0, 10))
        self.btn_bottom = ImageWindow(self.root, self.kick_animation, slide=(0, -10))
        self.conso = Bubble(self.root, "down", self.kick_animation, slide=(0, 14))
        self.dictation = Bubble(self.root, "up", self.kick_animation, slide=(0, -14))
        self.rail = ImageWindow(self.root, self.kick_animation, cursor="hand2", slide=(-12, 0))
        self.animated = (self.btn_top, self.btn_bottom, self.conso, self.dictation, self.rail)
        self.btn_top.label.bind("<Button-1>", lambda event: self.refresh_quotas_async(force=True))
        self.btn_bottom.label.bind("<Button-1>", lambda event: self.toggle_voice())
        self.rail.label.bind("<Button-1>", self.rail_click)
        self.rail.label.bind("<Button-3>", self.rail_menu)

        self.build_menu()

        self.recorder = Recorder(self.thread_status, self.transcription_ready, self.thread_error,
                                 model_name=self.prefs["whisper_model"], get_config=lambda: self.prefs["transcription"])
        self.recorder.warm_up()
        self.apply_look(initial=True)
        self.hotkey_ok = register_hotkey()
        if not self.hotkey_ok:
            self.set_status(tr("Ctrl+Alt+R shortcut unavailable; click the mascot"))
        self.loop(self.poll_hotkey, 50)
        self.loop(self.track_focus, 120)
        self.loop(self.animate, 30)
        self.loop(self.tick, 50)
        self.refresh_quotas_async()

    # ------------------------------------------------------------------ look & layout

    @property
    def theme(self) -> dict:
        return THEMES[self.prefs["theme"]]

    def load_mascot(self) -> mascots.MascotSet:
        """The selected mascot, scaled to the selected size and cropped to its visible pixels."""
        size = self.prefs["size"]
        box_h = round(size * SPRITE_H / SPRITE_W)
        choice = self.prefs["mascot"]
        mascot = None
        try:
            if choice == "dot":
                mascot = mascots.dot_set(max(20, size // 3), rgb(self.theme["accent"]))
            elif choice.startswith("pack:") and prefs.pack_path(choice):
                mascot = mascots.pack_set(prefs.pack_path(choice), size, box_h)
            elif choice.startswith("pet:") and prefs.pet_path(choice):
                mascot = mascots.rover_set(prefs.pet_path(choice), size, box_h)
            elif prefs.mascot_path(choice):
                mascot = mascots.file_set(prefs.mascot_path(choice), size, box_h)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.log_exception(type(exc), exc, exc.__traceback__)
            mascot = None
        if mascot is None:
            mascot = mascots.rover_set(ROVER, size, box_h)
        mascot.crop_to_content()
        return mascot

    def apply_look(self, initial: bool = False) -> None:
        """(Re)build every visual element from the current preferences."""
        old_center = None
        if not initial:
            old_center = (self.root.winfo_x() + self.root.winfo_width() / 2, self.root.winfo_y() + self.root.winfo_height() / 2)
        self.mset = self.load_mascot()
        self.frames = self.mset.idle.frames
        for bitmap in self.mascot_bitmaps.values():
            bitmap.close()
        self.mascot_bitmaps = {}
        content_w, content_h = self.mset.size
        self.mascot_w, self.mascot_h = content_w + 2 * PAD, content_h + 2 * PAD
        self.idle_box = tuple(value + PAD for value in self.mset.idle_box)
        self.anchor_x = PAD + self.visual_center(self.frames)
        self.clip_name, self.frame_index = "idle", 0
        self.canvas.configure(width=self.mascot_w, height=self.mascot_h)
        self.root.update()
        if initial:
            left, top, right, bottom = monitor_work_area(60, self.root.winfo_screenheight() // 2)
            x = left + 80
            y = top + round((bottom - top) * 0.42)
        else:
            x = round(old_center[0] - self.mascot_w / 2)
            y = round(old_center[1] - self.mascot_h / 2)
        x, y = self.clamp_position(x, y)
        place_window(self.root, x, y, self.mascot_w, self.mascot_h)
        self.play_base()
        self.build_rail()
        self.update_status_visuals(rebuild_bubbles=True)
        self.position_all()

    @staticmethod
    def visual_center(frames: list[Image.Image]) -> int:
        """Horizontal centre of mass of the mascot (its body, not a tail or an ear sticking out)."""
        alpha = np.asarray(frames[0].getchannel("A"), dtype=np.float64)
        total = alpha.sum()
        if total <= 0:
            return frames[0].width // 2
        centroid = float((alpha.sum(axis=0) * np.arange(alpha.shape[1])).sum() / total)
        return round((centroid + frames[0].width / 2) / 2)  # halfway between mass centre and box centre

    def mascot_bitmap(self, clip_name: str, index: int) -> layered.Bitmap:
        key = (clip_name, index, self.badge_color)
        bitmap = self.mascot_bitmaps.get(key)
        if bitmap is None:
            if len(self.mascot_bitmaps) > 400:
                for old in self.mascot_bitmaps.values():
                    old.close()
                self.mascot_bitmaps = {}
            image = Image.new("RGBA", (self.mascot_w, self.mascot_h), (0, 0, 0, 0))
            image.alpha_composite(self.mset.clips[clip_name].frames[index], (PAD, PAD))
            big = Image.new("RGBA", (16 * SS, 16 * SS), (0, 0, 0, 0))
            draw = ImageDraw.Draw(big)
            draw.ellipse((0, 0, 16 * SS - 1, 16 * SS - 1), fill=rgb(self.theme["desk"]))
            draw.ellipse((2 * SS, 2 * SS, 14 * SS - 1, 14 * SS - 1), fill=rgb(self.badge_color))
            image.alpha_composite(big.resize((16, 16), Image.Resampling.LANCZOS), (self.mascot_w - PAD - 12, PAD - 4))
            bitmap = self.mascot_bitmaps[key] = layered.Bitmap(image)
        return bitmap

    def paint_mascot(self) -> None:
        if self.mset is None:
            return
        hwnd = layered.enable(self.root, no_activate=True)
        layered.update(hwnd, self.mascot_bitmap(self.clip_name, self.frame_index))

    # ------------------------------------------------------------------ mascot animation

    def base_event(self) -> str:
        """The looping animation that matches what Rover is doing right now."""
        return {"recording": "record", "processing": "process"}.get(self.dict_state(), "idle")

    def play_clip(self, name: str, loop: bool) -> None:
        clip = self.mset.clips[name]
        self.clip_name, self.frame_index, self.clip_loop, self.frames_played = name, 0, loop, 0
        self.next_frame_at = time.monotonic() + clip.delays[0] / 1000
        self.paint_mascot()

    def play_event(self, event: str, loop: bool = False) -> bool:
        name = self.mset.find(event) if self.mset else None
        if name is None:
            return False
        self.play_clip(name, loop)
        return True

    def play_base(self) -> None:
        self.base_ev = self.base_event()
        if not self.play_event(self.base_ev, loop=True):
            self.play_clip("idle", True)

    def react(self, event: str) -> None:
        """One-shot reaction to an event (wave, jump, failure…); the base animation resumes afterwards."""
        if self.drag_origin and self.dragged:
            return
        self.play_event(event, loop=False)

    def advance_frame(self, now: float) -> None:
        clip = self.mset.clips[self.clip_name]
        if len(clip.frames) == 1 and self.clip_loop:
            self.next_frame_at = now + 1.0
            return
        following = clip.next_index(self.frame_index)
        self.frames_played += 1
        if following >= len(clip.frames) or self.frames_played > 600:
            if not self.clip_loop:
                self.play_base()
                return
            following, self.frames_played = 0, 0
        self.frame_index = following
        self.next_frame_at = now + clip.delays[following] / 1000
        self.paint_mascot()

    def log_exception(self, exc_type, exc, tb) -> None:
        import traceback
        with open(HERE / "rover.log", "a", encoding="utf-8") as log:
            log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\n" + "".join(traceback.format_exception(exc_type, exc, tb)) + "\n")

    def build_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label=tr("Dictate / stop · Ctrl+Alt+R"), command=self.toggle_voice)
        self.menu.add_command(label=tr("Copy last text"), command=self.copy_text)
        self.menu.add_command(label=tr("Compare speech models…"), command=self.open_benchmark)
        self.menu.add_command(label=tr("Customize…"), command=self.open_settings)
        self.menu.add_command(label=tr("Open Bloub"), command=lambda: webbrowser.open(prefs.BLOUB_URL))
        self.menu.add_command(label=tr("Open Claude usage"), command=lambda: webbrowser.open(CLAUDE_USAGE_URL))
        self.menu.add_separator()
        self.menu.add_command(label=tr("Quit"), command=self.quit)

    def clamp_position(self, x: int, y: int) -> tuple[int, int]:
        left, top, right, bottom = desktop_bounds()
        return max(left, min(right - self.mascot_w, x)), max(top, min(bottom - self.mascot_h, y))

    def build_rail(self) -> None:
        theme = self.theme
        glyphs, letters = [], []
        for app in self.prefs["apps"]:
            kind = app.get("kind")
            if kind == "claude":
                glyphs.append(icon("claude", CLAUDE_COLOR, 24))
            elif kind == "openai":
                glyphs.append(icon("openai", theme["text"], 24))
            else:
                glyphs.append(self.custom_glyph(app))
            letters.append(app["name"][:1])
        glyphs.append(icon("sliders", theme["sec"], 22))
        letters.append("s")
        glyphs.append(icon("plus", theme["sec"], 20))
        letters.append("+")
        image, self.rail_ranges = rail_image(theme, glyphs, letters)
        self.rail.set_image(image)

    def custom_glyph(self, app: dict) -> Image.Image:
        """Icon of a user shortcut: its real icon on a light disc (readable in both themes), else its initial."""
        picture = appicons.load_icon(app["icon"]) if app.get("icon") else None
        if picture is None:
            return letter_icon(app["name"][:1], self.theme["text"], 24)
        size = 28 * SS
        disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(disc).ellipse((0, 0, size - 1, size - 1), fill=(244, 243, 240, 255))
        inner = 20 * SS
        disc.alpha_composite(picture.resize((inner, inner), Image.Resampling.LANCZOS), ((size - inner) // 2, (size - inner) // 2))
        return disc

    def position_all(self) -> None:
        """Place the round buttons, bubbles and shortcut rail around the mascot."""
        wx, wy = self.root.winfo_x(), self.root.winfo_y()
        cx = wx + self.anchor_x
        left, top, right, bottom = monitor_work_area(cx, wy + self.mascot_h // 2)

        def clamp(value, low, high):
            return max(low, min(high, value))

        top_y = clamp(wy + self.idle_box[1] - GAP - BUTTON, top, bottom - BUTTON)
        bottom_y = clamp(wy + self.idle_box[3] + GAP, top, bottom - BUTTON)
        button_x = clamp(cx - BUTTON // 2, left, right - BUTTON)
        self.btn_top.place(button_x, top_y)
        self.btn_bottom.place(button_x, bottom_y)

        rail_w, rail_h = self.rail.size
        prefer_right = self.prefs["side"] == "right"
        center_y = wy + (self.idle_box[1] + self.idle_box[3]) // 2

        def side_x(width):
            """Column beside the mascot, on the side without the shortcut rail when possible."""
            beyond_left = wx - GAP - width
            beyond_right = wx + self.mascot_w + GAP
            if prefer_right:
                candidates = (beyond_left, beyond_right + rail_w + GAP)
            else:
                candidates = (beyond_right, beyond_left - rail_w - GAP)
            for candidate in candidates:
                if left <= candidate and candidate + width <= right:
                    return candidate
            return clamp(candidates[0], left, right - width)

        if self.conso.body is not None:
            width, height = self.conso.size
            full = height + (TAIL if not self.conso.tail else 0)
            fits = top_y + BUTTON + TAIL - full >= top
            self.conso.set_mode(fits)
            width, height = self.conso.size
            if fits:
                x = clamp(cx - width // 2, left, right - width)
                self.conso.set_tail(cx - x)
                width, height = self.conso.size
                y = clamp(top_y + BUTTON + TAIL - height, top, bottom - height)
            else:
                x, y = side_x(width), clamp(center_y - height, top, bottom - height)
            self.conso.place(x, y)
            self.set_button_visibility(self.btn_top, self.conso)
        if self.dictation.body is not None:
            width, height = self.dictation.size
            fits = bottom_y - TAIL + height + (0 if self.dictation.tail else TAIL) <= bottom
            self.dictation.set_mode(fits)
            width, height = self.dictation.size
            if fits:
                x = clamp(cx - width // 2, left, right - width)
                self.dictation.set_tail(cx - x)
                width, height = self.dictation.size
                y = clamp(bottom_y - TAIL, top, bottom - height)
            else:
                x, y = side_x(width), clamp(center_y, top, bottom - height)
            self.dictation.place(x, y)
            self.set_button_visibility(self.btn_bottom, self.dictation)

        right_x = wx + self.mascot_w + GAP
        left_x = wx - GAP - rail_w
        if any(bubble.visible() and bubble.anchored for bubble in (self.conso, self.dictation)):
            # keep the rail clear of the two bubbles while they are open
            right_x = max(right_x, cx + BUBBLE_W // 2 + GAP)
            left_x = min(left_x, cx - BUBBLE_W // 2 - GAP - rail_w)
        x = right_x if prefer_right else left_x
        if x + rail_w > right or x < left:
            x = left_x if prefer_right else right_x
        x = clamp(x, left, right - rail_w)
        y = clamp(center_y - rail_h // 2, top, bottom - rail_h)
        self.rail.slide = (-12 if x >= wx + self.mascot_w else 12, 0)
        self.rail.place(x, y)

    def set_button_visibility(self, round_button: ImageWindow, bubble: Bubble) -> None:
        """The round button gives way to an anchored bubble but stays under a side bubble."""
        if bubble.visible() and bubble.anchored:
            round_button.vanish()
        elif self.revealed:
            round_button.appear()

    # ------------------------------------------------------------------ animation

    def kick_animation(self) -> None:
        if not self.anim_running:
            self.anim_running = True
            self.last_frame = time.monotonic()
            self.root.after(15, self.animation_step)

    def animation_step(self) -> None:
        now = time.monotonic()
        dt, self.last_frame = now - self.last_frame, now
        moving = False
        for popup in self.animated:
            moving = popup.step(dt) or moving
        if moving:
            self.root.after(15, self.animation_step)
        else:
            self.anim_running = False

    def finish_animations(self) -> None:
        for popup in self.animated:
            while popup.step(1.0):
                pass

    # ------------------------------------------------------------------ state

    def dict_state(self) -> str:
        if self.recorder.recording:
            return "recording"
        if self.error_msg and time.monotonic() < self.error_until:
            return "error"
        if self.busy:
            return "processing"
        if self.pending_text:
            return "text"
        return "ready"

    def state_color(self, state: str | None = None) -> str:
        theme = self.theme
        return {"recording": theme["rec"], "processing": theme["accent"], "error": theme["err"]}.get(state or self.dict_state(), theme["ok"])

    def update_status_visuals(self, rebuild_bubbles: bool = False) -> None:
        theme = self.theme
        state = self.dict_state()
        color = self.state_color(state)
        if color != self.badge_color:
            self.badge_color = color
            self.paint_mascot()
        if self.mset is not None and self.base_event() != self.base_ev and not (self.drag_origin and self.dragged):
            self.play_base()
        glyph = icon("stop", "#ffffff", 22) if state == "recording" else icon("mic", color, 22)
        fill = color if state == "recording" else theme["panel"]
        self.btn_bottom.set_image(circle_button(BUTTON, fill, color, glyph, border_width=1.5))
        self.btn_top.set_image(circle_button(BUTTON, theme["panel"], theme["accent"], icon("gauge", theme["accent"], 22), border_width=1.5))
        if rebuild_bubbles or self.conso.visible():
            self.render_conso()
        if rebuild_bubbles or self.dictation.visible():
            self.render_dictation()
        self.position_all()

    def set_status(self, text: str) -> None:
        self.notice = text
        self.notice_until = time.monotonic() + 3 if text else 0.0
        self.update_status_visuals()

    def thread_status(self, text: str):
        def apply():
            if text == "transcribing":
                self.step = "whisper"
            self.update_status_visuals()
        self.root.after(0, apply)

    def thread_error(self, message: str):
        self.root.after(0, lambda: self.finish_error(message))

    def finish_error(self, message: str):
        self.busy = False
        self.error_msg = message[:160]
        self.error_until = time.monotonic() + 10
        self.update_status_visuals()
        self.react("error")

    def transcription_ready(self, transcript: str, language: str):
        self.last_transcript, self.last_language = transcript, language
        if not rewrite_enabled(self.prefs):
            self.root.after(0, lambda: self.complete_rewrite(transcript))
            return
        self.step = "codex"
        self.root.after(0, self.update_status_visuals)
        threading.Thread(target=self.rewrite_worker, args=(transcript, language), daemon=True).start()

    def rewrite_worker(self, transcript: str, language: str):
        try:
            revised = rewrite_text(transcript, language, self.prefs)
            self.root.after(0, lambda: self.complete_rewrite(revised))
        except Exception as exc:
            self.thread_error(str(exc))

    def complete_rewrite(self, revised: str):
        self.busy = False
        self.last_output = revised
        self.pending_text = revised
        if self.prefs["insert"] == "copy":
            self.pending_text = ""
            self.copy_text(revised)
            self.react("success")
            return
        current = foreground_window()
        if current in (self.target_hwnd, 0) or is_own_window(current):
            if self.insert_pending():
                self.react("success")
                return
        self.set_status(tr("The field changed: click to insert."))
        self.react("success")

    def copy_text(self, text: str | None = None, silent: bool = False) -> bool:
        """Put the text on the clipboard: the always-works alternative to typing it."""
        text = text if text is not None else (self.pending_text or self.last_output or self.last_transcript)
        if not text:
            return False
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        except tk.TclError:
            return False
        if not silent:
            self.set_status(tr("Copied · paste with Ctrl+V"))
        return True

    def insert_pending(self) -> bool:
        if not self.pending_text:
            return False
        if insert_text(self.target_hwnd, self.pending_text):
            self.pending_text = ""
            self.set_status(tr("Prompt inserted · nothing was sent"))
            return True
        self.set_status(tr("Could not find the original field. Text kept."))
        return False

    def cancel_pending(self):
        self.pending_text = ""
        self.set_status("")

    def retry(self):
        self.error_msg = ""
        if self.last_transcript:
            self.busy = True
            self.step = "codex"
            self.update_status_visuals()
            threading.Thread(target=self.rewrite_worker, args=(self.last_transcript, self.last_language), daemon=True).start()
        else:
            self.update_status_visuals()
            self.toggle_voice()

    def insert_raw(self):
        self.error_msg = ""
        self.pending_text = self.last_transcript
        if not self.insert_pending():
            self.update_status_visuals()

    def set_language(self, language: str):
        self.language = language
        self.prefs["language"] = language
        self.saved_prefs["language"] = language
        prefs.save(self.saved_prefs)
        self.notice = ""
        self.update_status_visuals()

    def toggle_voice(self, from_hotkey: bool = False):
        if self.recorder.recording:
            self.recorder.stop()
            self.step = "whisper"
            self.root.after(50, self.update_status_visuals)
            return
        if self.busy:
            return
        current = foreground_window()
        self.target_hwnd = current if current and not is_own_window(current) else self.last_external_hwnd
        if not self.target_hwnd:
            self.error_msg = tr("Click in the field where the prompt should go first")
            self.last_transcript = ""
            self.error_until = time.monotonic() + 6
            self.update_status_visuals()
            self.react("error")
            return
        self.error_msg = ""
        self.notice = ""
        self.busy = True
        if not self.recorder.start(self.language):
            self.busy = False
            return
        self.rec_started = time.monotonic()
        self.wave.extend([0.0] * 30)
        self.update_status_visuals()

    def loop(self, body, delay) -> None:
        """Run `body` forever; one failure is logged and never stops the loop."""
        try:
            body()
        except Exception as exc:
            self.log_exception(type(exc), exc, exc.__traceback__)
        finally:
            self.root.after(delay() if callable(delay) else delay, lambda: self.loop(body, delay))

    def poll_hotkey(self):
        if self.hotkey_ok and consume_hotkey():
            self.toggle_voice(from_hotkey=True)

    def track_focus(self):
        hwnd = foreground_window()
        if hwnd and not is_own_window(hwnd):
            self.last_external_hwnd = hwnd

    def animate(self):
        if self.mset is None:
            return
        now = time.monotonic()
        if now >= self.next_frame_at:
            self.advance_frame(now)
        if (self.clip_name == "idle" and self.mset.variants and now >= self.next_variant and self.base_ev == "idle"
                and not self.drag_origin):
            self.next_variant = now + random.uniform(9, 22)
            self.play_clip(random.choice(self.mset.variants), False)

    # ------------------------------------------------------------------ hover, timers

    def tick(self):
        now = time.monotonic()
        if not self.drag_origin:
            self.handle_hover(now)
        state = self.dict_state()
        if self.error_msg and state != "error" and not self.busy:
            self.error_msg = ""
            self.update_status_visuals()
        widgets = self.live_widgets
        if state == "recording" and widgets.get("wave"):
            self.wave.append(min(1.0, self.recorder.level * 4))
            self.draw_wave()
            if widgets.get("timer"):
                elapsed = int(now - self.rec_started)
                widgets["timer"].configure(text=f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        elif state == "processing" and widgets.get("spinner"):
            self.spin = (self.spin + 1) % 12
            self.draw_spinner()

    def handle_hover(self, now: float) -> None:
        """Round buttons and the rail appear while the pointer is on Rover; a button opens its bubble."""
        cluster = (self.btn_top.win, self.btn_bottom.win, self.conso.win, self.dictation.win, self.rail.win)
        over_any = self.pointer_on_mascot() or any(pointer_inside(window) for window in cluster)
        if over_any:
            self.hover_last = now
        revealed = over_any or now - self.hover_last < REVEAL_GRACE
        if revealed != self.revealed:
            self.set_revealed(revealed)

        over_conso = pointer_inside(self.btn_top.win) or pointer_inside(self.conso.win)
        if over_conso:
            self.conso_last_over = now
        if self.revealed and over_conso and not self.conso.visible():
            self.open_conso()
        elif self.conso.visible() and (not self.revealed or now - self.conso_last_over > CLOSE_DELAY):
            self.close_conso()

        forced = self.dict_state() != "ready" or now < self.notice_until
        over_dict = pointer_inside(self.btn_bottom.win) or pointer_inside(self.dictation.win)
        if over_dict:
            self.dict_last_over = now
        if (forced or (self.revealed and over_dict)) and not self.dictation.visible():
            self.open_dictation()
        elif self.dictation.visible() and not forced and (not self.revealed or now - self.dict_last_over > CLOSE_DELAY):
            self.close_dictation()

    def pointer_on_mascot(self) -> bool:
        """True over the resting mascot (a few pixels of margin), not over the empty corners of its window."""
        px, py = self.root.winfo_pointerxy()
        left, top, right, bottom = self.idle_box
        x, y = px - self.root.winfo_rootx(), py - self.root.winfo_rooty()
        return left - 10 <= x <= right + 10 and top - 10 <= y <= bottom + 10

    def set_revealed(self, revealed: bool) -> None:
        self.revealed = revealed
        if revealed:
            if self.base_ev == "idle":
                self.react("hover")
            self.rail.appear()
            self.set_button_visibility(self.btn_top, self.conso)
            self.set_button_visibility(self.btn_bottom, self.dictation)
        else:
            for popup in (self.btn_top, self.btn_bottom, self.rail):
                popup.vanish()

    def open_conso(self) -> None:
        self.render_conso()
        self.position_all()
        self.conso.appear()
        self.set_button_visibility(self.btn_top, self.conso)
        self.refresh_quotas_async()

    def close_conso(self) -> None:
        self.conso.vanish()
        if self.revealed:
            self.btn_top.appear()
        self.position_all()

    def open_dictation(self) -> None:
        self.render_dictation()
        self.position_all()
        self.dictation.appear()
        self.set_button_visibility(self.btn_bottom, self.dictation)

    def close_dictation(self) -> None:
        self.notice = ""
        self.dictation.vanish()
        if self.revealed:
            self.btn_bottom.appear()
        self.position_all()

    # ------------------------------------------------------------------ quotas

    def refresh_quotas_async(self, force: bool = False) -> None:
        if self.quota_loading or (not force and time.monotonic() - self.last_quota_refresh < 60 and self.cards is not None):
            return
        self.quota_loading = True
        self.last_quota_refresh = time.monotonic()
        threading.Thread(target=self.refresh_quotas, daemon=True).start()

    def refresh_quotas(self):
        cards = read_quotas(self.prefs["quotas_url"])
        self.root.after(0, lambda: self.quotas_loaded(cards))

    def quotas_loaded(self, cards: list[dict]) -> None:
        self.cards = cards
        self.quota_loading = False
        self.quota_time = datetime.now()
        if self.conso.visible():
            self.render_conso()
            self.position_all()

    def render_conso(self) -> None:
        self.conso.rebuild(self.theme, self.fill_conso)

    def fill_conso(self, body: tk.Frame) -> None:
        theme = self.theme
        panel = theme["panel"]
        head = tk.Frame(body, bg=panel)
        head.pack(fill="x")
        tk.Label(head, text=tr("USAGE"), bg=panel, fg=theme["sec"], font=(FONT_SEMIBOLD, 8)).pack(side="left")
        cards = self.cards
        if cards is None:
            status, color = tr("Reading…"), theme["sec"]
        elif all(c.get("status") == "ok" for c in cards if c["name"] != "Claude") and any(c["name"] != "Claude" for c in cards):
            status, color = tr("Data up to date · {time}").format(time=f"{self.quota_time:%H:%M}"), theme["ok"]
        else:
            status, color = tr("Incomplete data"), theme["err"]
        tk.Label(head, text=status, bg=panel, fg=theme["sec"], font=(FONT_REGULAR, 8)).pack(side="right")
        tk.Label(head, text="●", bg=panel, fg=color, font=(FONT_REGULAR, 7)).pack(side="right", padx=(0, 4))

        for entry in cards or []:
            tk.Frame(body, bg=panel, height=10).pack()
            if entry["name"] == "Claude":
                card(body, theme, INNER_W, lambda inner, e=entry: self.claude_card(inner, e)).pack()
            else:
                card(body, theme, INNER_W, lambda inner, e=entry: self.codex_card(inner, e)).pack()

    def codex_card(self, inner: tk.Frame, entry: dict) -> None:
        theme = self.theme
        bg = theme["card"]
        header = tk.Frame(inner, bg=bg)
        header.pack(fill="x")
        logo = photo(icon_pil("openai", theme["text"], 18))
        header._logo = logo
        tk.Label(header, image=logo, bg=bg).pack(side="left")
        tk.Label(header, text="Codex", bg=bg, fg=theme["text"], font=(FONT_SEMIBOLD, 10)).pack(side="left", padx=(6, 0))
        plan = entry.get("plan") or entry["name"]
        tk.Label(header, text=tr("{plan} account").format(plan=plan), bg=bg, fg=theme["sec"], font=(FONT_SEMIBOLD, 8), highlightthickness=1,
                 highlightbackground=theme["border"], padx=6).pack(side="right")
        windows = entry.get("windows") or []
        if not windows:
            tk.Label(inner, text=entry.get("status", tr("Unknown")), bg=bg, fg=theme["err"], font=(FONT_REGULAR, 9),
                     wraplength=INNER_W - 30, justify="left").pack(anchor="w", pady=(8, 0))
            return
        for window in windows:
            row = tk.Frame(inner, bg=bg)
            row.pack(fill="x", pady=(10, 0))
            tk.Label(row, text=tr("{label} · used").format(label=window['label']), bg=bg, fg=theme["text"], font=(FONT_REGULAR, 9)).pack(side="left")
            tk.Label(row, text=f"{window['used']} %", bg=bg, fg=theme["text"], font=(FONT_MONO, 9, "bold")).pack(side="right")
            self.meter(inner, window["used"], bg).pack(pady=(5, 0))
            if window.get("reset"):
                tk.Label(inner, text=tr("Resets {date}").format(date=format_reset(window['reset'])), bg=bg, fg=theme["sec"],
                         font=(FONT_REGULAR, 8)).pack(anchor="w", pady=(4, 0))
        if entry.get("extra_credits"):
            tk.Label(inner, text=tr("Additional credits: {amount}").format(amount=format_credits(entry['extra_credits'])), bg=bg, fg=theme["sec"],
                     font=(FONT_REGULAR, 8)).pack(anchor="w", pady=(8, 0))
        if entry.get("status") != "ok":
            tk.Label(inner, text=tr("Data may be out of date"), bg=bg, fg=theme["err"],
                     font=(FONT_REGULAR, 8)).pack(anchor="w", pady=(6, 0))

    def claude_card(self, inner: tk.Frame, entry: dict) -> None:
        theme = self.theme
        bg = theme["card"]
        header = tk.Frame(inner, bg=bg)
        header.pack(fill="x")
        logo = photo(icon_pil("claude", CLAUDE_COLOR, 18))
        header._logo = logo
        tk.Label(header, image=logo, bg=bg).pack(side="left")
        tk.Label(header, text="Claude", bg=bg, fg=theme["text"], font=(FONT_SEMIBOLD, 10)).pack(side="left", padx=(6, 0))
        tk.Label(inner, text=tr("Local account detected · quota unavailable"), bg=bg, fg=theme["sec"],
                 font=(FONT_REGULAR, 8)).pack(anchor="w", pady=(4, 0))
        link = tk.Label(inner, text=tr("View usage ↗"), bg=bg, fg=theme["link"], cursor="hand2",
                        font=(FONT_SEMIBOLD, 9, "underline"))
        link.pack(anchor="w", pady=(6, 0))
        link.bind("<Button-1>", lambda event: webbrowser.open(entry.get("url") or CLAUDE_USAGE_URL))

    def meter(self, parent, percent: int, bg: str) -> tk.Canvas:
        theme = self.theme
        width = INNER_W - 24
        bar = tk.Canvas(parent, width=width, height=6, bg=bg, bd=0, highlightthickness=0)
        bar.create_line(3, 3, width - 3, 3, width=6, capstyle="round", fill=theme["track"])
        if percent > 0:
            end = 3 + (width - 6) * min(percent, 100) / 100
            bar.create_line(3, 3, max(end, 3.5), 3, width=6, capstyle="round", fill=theme["bar"])
        return bar

    # ------------------------------------------------------------------ dictation bubble

    def render_dictation(self) -> None:
        self.live_widgets = {}
        self.dictation.rebuild(self.theme, self.fill_dictation)

    def fill_dictation(self, body: tk.Frame) -> None:
        theme = self.theme
        panel = theme["panel"]
        state = self.dict_state()
        head = tk.Frame(body, bg=panel)
        head.pack(fill="x")
        tk.Label(head, text=tr("DICTATION"), bg=panel, fg=theme["sec"], font=(FONT_SEMIBOLD, 8)).pack(side="left")
        seg = tk.Frame(head, bg=theme["card"], highlightthickness=1, highlightbackground=theme["border"])
        seg.pack(side="right")
        for key, caption in (("fr", "FR"), ("en", "EN")):
            active = self.language == key
            label = tk.Label(seg, text=caption, bg=theme["accent"] if active else theme["card"],
                             fg=theme["on_accent"] if active else theme["sec"], font=(FONT_SEMIBOLD, 9), padx=10, pady=2,
                             cursor="hand2")
            label.pack(side="left", padx=2, pady=2)
            label.bind("<Button-1>", lambda event, k=key: self.set_language(k))

        row = tk.Frame(body, bg=panel)
        row.pack(fill="x", pady=(12, 0))
        color = self.state_color(state)
        mic_fill = color if state == "recording" else theme["accent"] if state == "ready" else panel
        mic_fg = "#ffffff" if state == "recording" else theme["on_accent"] if state == "ready" else theme["sec"]
        mic_border = color if state == "recording" else theme["accent"] if state == "ready" else theme["border"]
        glyph = icon("stop", mic_fg, 22) if state == "recording" else icon("mic", mic_fg, 24)
        mic_image = photo(circle_button(48, mic_fill, mic_border, glyph, border_width=1.5))
        mic = tk.Label(row, image=mic_image, bg=panel, cursor="hand2")
        mic._image = mic_image
        mic.pack(side="left")
        mic.bind("<Button-1>", lambda event: self.toggle_voice())

        title, sub = self.state_texts(state)
        text_col = tk.Frame(row, bg=panel)
        text_col.pack(side="left", padx=12)
        tk.Label(text_col, text=title, bg=panel, fg=color if state in ("recording", "error") else theme["text"],
                 font=(FONT_SEMIBOLD, 10), anchor="w").pack(anchor="w")
        tk.Label(text_col, text=sub, bg=panel, fg=theme["sec"], font=(FONT_REGULAR, 8), anchor="w").pack(anchor="w")
        keys = tk.Frame(row, bg=panel)
        keys.pack(side="right")
        for key_name in ("Ctrl", "Alt", "R"):
            tk.Label(keys, text=key_name, bg=theme["kbd"], fg=theme["sec"], font=(FONT_MONO, 8), padx=4,
                     highlightthickness=1, highlightbackground=theme["border"]).pack(side="left", padx=(0, 3))

        tk.Frame(body, bg=panel, height=12).pack()
        card(body, theme, INNER_W, lambda inner: self.fill_state_zone(inner, state), min_height=132).pack()

    def transcription_step(self) -> str:
        config = self.prefs["transcription"]
        if transcribe.is_remote(config):
            return tr("Transcription ({engine})").format(engine=transcribe.engine_label(config))
        return tr("Local transcription (Whisper)")

    def pipeline_line(self) -> str:
        config = self.prefs["transcription"]
        tool = provider_label(self.prefs) if rewrite_enabled(self.prefs) else ""
        if transcribe.is_remote(config):
            engine = transcribe.engine_label(config)
            return (tr("{engine} transcription  ·  {tool} rewrite").format(engine=engine, tool=tool) if tool
                    else tr("{engine} transcription  ·  no rewrite").format(engine=engine))
        return tr("Local Whisper  ·  {tool} rewrite").format(tool=tool) if tool else tr("Local Whisper  ·  no rewrite")

    def state_texts(self, state: str) -> tuple[str, str]:
        language = tr("Dictation in French") if self.language == "fr" else tr("Dictation in English")
        return {
            "ready": (tr("Ready"), language),
            "recording": (tr("Listening…"), tr("Microphone on")),
            "processing": (tr("Processing"), tr("A few seconds")),
            "text": (tr("Text ready"), tr("Check before inserting")),
            "error": (tr("Error"), tr("Nothing was inserted")),
        }[state]

    def fill_state_zone(self, inner: tk.Frame, state: str) -> None:
        theme = self.theme
        bg = theme["card"]
        width = INNER_W - 24 - 4
        if state == "ready":
            message = self.notice or tr("Click the mascot or use the shortcut, then speak. The text is inserted into the active field and never sent.")
            tk.Label(inner, text=message, bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 9), wraplength=width,
                     justify="left").pack(anchor="w")
            tk.Label(inner, text=self.pipeline_line(), bg=bg, fg=theme["sec"],
                     font=(FONT_REGULAR, 8)).pack(anchor="w", pady=(8, 0))
        elif state == "recording":
            top = tk.Frame(inner, bg=bg)
            top.pack(fill="x")
            tk.Label(top, text=tr("● Recording"), bg=bg, fg=theme["rec"], font=(FONT_SEMIBOLD, 9)).pack(side="left")
            timer = tk.Label(top, text="00:00", bg=bg, fg=theme["text"], font=(FONT_MONO, 9))
            timer.pack(side="right")
            wave = tk.Canvas(inner, width=width, height=44, bg=bg, bd=0, highlightthickness=0)
            wave.pack(pady=(6, 4))
            tk.Label(inner, text=tr("Click again to finish."), bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 8)).pack(anchor="w")
            self.live_widgets = {"wave": wave, "timer": timer, "wave_width": width}
            self.draw_wave()
        elif state == "processing":
            done = self.step == "codex"
            self.step_row(inner, self.transcription_step(), done=done, spinner=not done)
            self.step_row(inner, tr("Rewriting ({tool})…").format(tool=provider_label(self.prefs) or "…"), done=False, spinner=done, dim=not done)
        elif state == "text":
            tk.Label(inner, text=tr("Text ready to insert"), bg=bg, fg=theme["ok"], font=(FONT_SEMIBOLD, 9)).pack(anchor="w")
            preview = self.pending_text if len(self.pending_text) <= 200 else self.pending_text[:197] + "…"
            tk.Label(inner, text=preview, bg=bg, fg=theme["text"], font=(FONT_REGULAR, 9), wraplength=width,
                     justify="left").pack(anchor="w", pady=(4, 8))
            actions = tk.Frame(inner, bg=bg)
            actions.pack(fill="x")
            button(actions, theme, tr("Insert into field"), self.insert_pending, primary=True).pack(side="left")
            button(actions, theme, tr("Copy"), self.copy_text).pack(side="left", padx=(8, 0))
            button(actions, theme, tr("Cancel"), self.cancel_pending).pack(side="left", padx=(8, 0))
        else:
            tk.Label(inner, text=tr("Rewrite failed") if self.last_transcript else tr("Dictation failed"), bg=bg,
                     fg=theme["err"], font=(FONT_SEMIBOLD, 9)).pack(anchor="w")
            tk.Label(inner, text=self.error_msg, bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 9), wraplength=width,
                     justify="left").pack(anchor="w", pady=(4, 8))
            actions = tk.Frame(inner, bg=bg)
            actions.pack(fill="x")
            button(actions, theme, tr("Retry"), self.retry, primary=True).pack(side="left")
            if self.last_transcript:
                button(actions, theme, tr("Insert raw text"), self.insert_raw).pack(side="left", padx=(8, 0))
                button(actions, theme, tr("Copy"), lambda: self.copy_text(self.last_transcript)).pack(side="left", padx=(8, 0))

    def step_row(self, inner, text: str, done: bool, spinner: bool, dim: bool = False) -> None:
        theme = self.theme
        bg = theme["card"]
        row = tk.Frame(inner, bg=bg)
        row.pack(fill="x", pady=4)
        if done:
            image = photo(circle_button(18, theme["ok"], theme["ok"], icon("check", theme["on_ok"], 12)))
            marker = tk.Label(row, image=image, bg=bg)
            marker._image = image
        elif spinner:
            marker = tk.Label(row, bg=bg)
            self.live_widgets = {"spinner": marker}
            self.draw_spinner()
        else:
            image = photo(circle_button(18, bg, theme["border"], None, border_width=1.5))
            marker = tk.Label(row, image=image, bg=bg)
            marker._image = image
        marker.pack(side="left")
        tk.Label(row, text=text, bg=bg, fg=theme["sec"] if dim else theme["text"], font=(FONT_REGULAR, 10)).pack(side="left", padx=8)

    def draw_spinner(self) -> None:
        marker = self.live_widgets.get("spinner")
        if marker is None:
            return
        theme = self.theme
        size = 18 * SS
        ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(ring).arc((SS * 1.5, SS * 1.5, size - SS * 1.5, size - SS * 1.5), 0, 270, fill=rgb(theme["accent"]), width=round(2 * SS))
        ring = ring.rotate(-self.spin * 30, resample=Image.Resampling.BICUBIC).resize((18, 18), Image.Resampling.LANCZOS)
        marker._image = photo(ring)
        marker.configure(image=marker._image)

    def draw_wave(self) -> None:
        wave = self.live_widgets.get("wave")
        if wave is None:
            return
        theme = self.theme
        width = self.live_widgets["wave_width"]
        wave.delete("all")
        slot = width / len(self.wave)
        for index, level in enumerate(self.wave):
            height = 4 + 38 * level
            x = index * slot + slot / 2
            wave.create_line(x, 22 - height / 2, x, 22 + height / 2, width=max(2, slot - 4), capstyle="round", fill=theme["rec"])

    # ------------------------------------------------------------------ dragging

    def mouse_down(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        self.dragged = False
        self.last_drag_x = event.x_root
        self.canvas.grab_set()

    def mouse_drag(self, event):
        if not self.drag_origin:
            return
        x0, y0, wx, wy = self.drag_origin
        dx, dy = event.x_root - x0, event.y_root - y0
        if not self.dragged and abs(dx) + abs(dy) <= 10:
            return
        self.dragged = True
        step = event.x_root - self.last_drag_x
        if abs(step) >= 2:
            direction = "drag_right" if step > 0 else "drag_left"
            if direction != self.drag_event:
                self.drag_event = direction
                self.play_event(direction, loop=True)
            self.last_drag_x = event.x_root
        x, y = self.clamp_position(wx + dx, wy + dy)
        place_window(self.root, x, y)
        self.position_all()

    def mouse_up(self, event):
        if not self.drag_origin:
            return
        self.mouse_drag(event)
        self.drag_origin = None
        self.canvas.grab_release()
        if self.dragged:
            self.drag_event = None
            self.play_base()
        else:
            self.toggle_voice()

    # ------------------------------------------------------------------ AI shortcuts rail

    def rail_index(self, y: int) -> int | None:
        for index, (low, high) in enumerate(self.rail_ranges):
            if low <= y < high:
                return index
        return None

    def rail_click(self, event):
        index = self.rail_index(event.y)
        if index is None:
            return
        apps = self.prefs["apps"]
        if index == len(apps):
            self.open_settings()
        elif index > len(apps):
            self.add_app_dialog()
        else:
            self.open_app(apps[index])

    def rail_menu(self, event):
        index = self.rail_index(event.y)
        apps = self.prefs["apps"]
        if index is None or index >= len(apps):
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=tr("Open {name}").format(name=apps[index]['name']), command=lambda: self.open_app(apps[index]))
        menu.add_command(label=tr("Remove {name}").format(name=apps[index]['name']), command=lambda: self.remove_app(index))
        menu.tk_popup(event.x_root, event.y_root)

    def open_app(self, entry: dict) -> None:
        try:
            if entry.get("path"):
                os.startfile(entry["path"])
            else:
                webbrowser.open(entry["url"])
        except OSError:
            self.set_status(tr("Could not open {name}").format(name=entry['name']))

    def save_apps(self) -> None:
        self.saved_prefs["apps"] = list(self.prefs["apps"])
        prefs.save(self.saved_prefs)

    def apps_changed(self) -> None:
        self.build_rail()
        self.position_all()
        if self.settings_window is not None and self.settings_window.alive():
            self.settings_window.build()

    def remove_app(self, index: int):
        del self.prefs["apps"][index]
        self.save_apps()
        self.apps_changed()

    def add_app(self, name: str, target: str) -> bool:
        """Add a shortcut to a web address, a local web service (http://127.0.0.1:3100), a program or a folder."""
        target = target.strip().strip('"')
        if len(self.prefs["apps"]) >= prefs.MAX_APPS:
            return False
        if target and os.path.exists(target):
            entry = {"name": name.strip() or Path(target).stem or target, "path": str(Path(target)), "kind": "custom"}
        else:
            url = normalize_url(target)
            if not url:
                return False
            entry = {"name": name.strip() or domain_name(url), "url": url, "kind": "custom"}
        self.prefs["apps"].append(entry)
        self.save_apps()
        self.apps_changed()
        threading.Thread(target=self.fetch_icon, args=(entry,), daemon=True).start()
        return True

    def fetch_icon(self, entry: dict) -> None:
        target = entry.get("path") or entry["url"]
        try:
            picture = appicons.shell_icon(entry["path"]) if entry.get("path") else appicons.favicon(entry["url"])
            if picture is not None:
                entry["icon"] = appicons.save_icon(target, picture)
                self.root.after(0, self.icon_ready)
        except Exception as exc:          # an unreadable icon must never break a shortcut
            self.log_exception(type(exc), exc, exc.__traceback__)

    def icon_ready(self) -> None:
        self.save_apps()
        self.build_rail()
        self.position_all()

    def add_app_dialog(self):
        add_app_dialog_window(self)

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.alive():
            self.settings_window.top.lift()
            return
        self.settings_window = SettingsWindow(self)

    def open_benchmark(self) -> None:
        """The benchmark is its own small program, so a slow model never freezes the mascot."""
        import subprocess
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        subprocess.Popen([str(pythonw if pythonw.exists() else python), str(HERE / "benchmark.py")], cwd=str(HERE),
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def apply_advanced(self) -> None:
        """Settings that need no redraw: the speech model takes effect at the next dictation."""
        self.recorder.set_model(self.prefs["whisper_model"])
        self.language = self.prefs["language"]

    def update_pref(self, **changes) -> None:
        self.prefs.update(changes)
        if "ui_language" in changes:
            i18n.set_language(changes["ui_language"])
            self.build_menu()
        self.language = self.prefs["language"]
        self.apply_look()

    def show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def quit(self):
        self.recorder.stop()
        accel.stop()
        if self.hotkey_ok:
            unregister_hotkey()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def make_chrome(top, app, title: str, on_close):
    """Give a borderless Toplevel its own title bar (drag to move, close button); returns (body, actions)."""
    theme = app.theme
    for child in top.winfo_children():
        child.destroy()
    top.configure(bg=theme["border"])
    inner = tk.Frame(top, bg=theme["desk"])
    inner.pack(padx=1, pady=1, fill="both", expand=True)
    bar = tk.Frame(inner, bg=theme["desk"])
    bar.pack(fill="x", padx=14, pady=(9, 0))
    label = tk.Label(bar, text=title, bg=theme["desk"], fg=theme["sec"], font=(FONT_SEMIBOLD, 9))
    label.pack(side="left")
    close = tk.Label(bar, text="✕", bg=theme["desk"], fg=theme["sec"], font=(FONT_REGULAR, 11), padx=9, pady=1, cursor="hand2")
    close.pack(side="right")
    close.bind("<Enter>", lambda event: close.configure(bg=theme["err"], fg="#ffffff"))
    close.bind("<Leave>", lambda event: close.configure(bg=theme["desk"], fg=theme["sec"]))
    close.bind("<Button-1>", lambda event: on_close())
    actions = tk.Frame(bar, bg=theme["desk"])
    actions.pack(side="right", padx=(0, 6))
    grab = {}

    def start(event):
        grab["offset"] = (event.x_root - top.winfo_x(), event.y_root - top.winfo_y())

    def move(event):
        if "offset" in grab:
            place_window(top, event.x_root - grab["offset"][0], event.y_root - grab["offset"][1])

    for widget in (bar, label):
        widget.bind("<ButtonPress-1>", start)
        widget.bind("<B1-Motion>", move)
    body = tk.Frame(inner, bg=theme["desk"])
    body.pack(fill="both", expand=True)
    top.bind("<Escape>", lambda event: on_close())
    return body, actions


def restyle_toggle(widget: tk.Button, theme: dict, active: bool, base: str) -> None:
    widget.configure(bg=theme["accent"] if active else base, fg=theme["on_accent"] if active else theme["text"],
                     activebackground=theme["accent"], activeforeground=theme["on_accent"],
                     highlightbackground=theme["accent"] if active else theme["border"])


class SettingsWindow:
    """Personnalisation: mascot, size, side of the shortcut rail and theme (applied live, edited in place)."""

    def __init__(self, app: RoverApp):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.images: list = []
        self.preview_images: list = []
        self.placed = False
        self.page = "main"
        self.refreshers: list = []
        self.build()
        self.center()
        self.top.focus_force()

    def alive(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def center(self):
        self.top.update_idletasks()
        width, height = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        wx, wy = self.app.root.winfo_x(), self.app.root.winfo_y()
        left, top, right, bottom = monitor_work_area(wx, wy)
        x = left + max(0, (right - left - width) // 2)
        y = top + max(0, (bottom - top - height) // 2)
        place_window(self.top, x, y, width, height)
        self.placed = True

    def close(self):
        # Unsaved live changes are reverted to what was last saved.
        app = self.app
        if app.prefs != app.saved_prefs:
            app.prefs = json.loads(json.dumps(app.saved_prefs))
            app.language = app.prefs["language"]
            app.apply_look()
            app.apply_advanced()
        self.top.destroy()

    def save(self):
        app = self.app
        app.saved_prefs = json.loads(json.dumps(app.prefs))
        prefs.save(app.saved_prefs)
        if app.prefs.get("startup") is not None:
            try:
                startup.set_enabled(bool(app.prefs["startup"]))
            except OSError as exc:
                app.log_exception(type(exc), exc, exc.__traceback__)
        app.apply_advanced()
        self.top.destroy()

    def reset(self):
        defaults = prefs.DEFAULTS
        fresh = json.loads(json.dumps(defaults))
        fresh.update({"apps": list(self.app.prefs["apps"]), "language": self.app.prefs["language"],
                      "codex": {**fresh["codex"], "home": self.app.prefs["codex"].get("home", ""),
                                "auth_store": self.app.prefs["codex"].get("auth_store", "")},
                      "quotas_url": self.app.prefs["quotas_url"]})
        self.app.prefs = fresh
        self.app.apply_look()
        self.app.apply_advanced()
        self.build()

    def set(self, **changes):
        self.app.update_pref(**changes)
        if "theme" in changes or "ui_language" in changes:
            self.build()          # every colour or every label changes
        else:
            self.sync()           # touch only what changed: no flicker, no scroll or position reset

    # ------------------------------------------------------------------ construction

    def build(self):
        app = self.app
        theme = app.theme
        self.images = []
        self.tile_holders: dict[str, tk.Frame] = {}
        self.toggles: dict[tuple[str, str], tk.Button] = {}
        self.preset_buttons: list[tuple[int, tk.Button]] = []
        self.refreshers = []
        body, _ = make_chrome(self.top, app, "LLM Mascot", self.close)
        outer = tk.Frame(body, bg=theme["desk"], padx=28, pady=10)
        outer.pack()
        advanced = self.page == "advanced"

        header = tk.Frame(outer, bg=theme["desk"])
        header.pack(fill="x")
        titles = tk.Frame(header, bg=theme["desk"])
        titles.pack(side="left")
        tk.Label(titles, text=tr("SETTINGS"), bg=theme["desk"], fg=theme["sec"], font=(FONT_SEMIBOLD, 8)).pack(anchor="w")
        tk.Label(titles, text=tr("Advanced settings") if advanced else tr("Customization"), bg=theme["desk"], fg=theme["text"],
                 font=(FONT_SEMIBOLD, 20)).pack(anchor="w")
        actions = tk.Frame(header, bg=theme["desk"])
        actions.pack(side="right")
        button(actions, theme, tr("← Back") if advanced else tr("Advanced settings →"), self.toggle_page).pack(side="left", padx=(0, 8))
        button(actions, theme, tr("Reset"), self.reset).pack(side="left", padx=(0, 8))
        button(actions, theme, tr("Save"), self.save, primary=True).pack(side="left")

        if advanced:
            self.build_advanced(outer)
            self.finish_build()
            return
        columns = tk.Frame(outer, bg=theme["desk"])
        columns.pack(pady=(18, 4))
        preview = tk.Frame(columns, bg=theme["desk"])
        preview.pack(side="left", padx=(0, 18), anchor="n")
        tk.Label(preview, text=tr("LIVE PREVIEW"), bg=theme["desk"], fg=theme["sec"], font=(FONT_SEMIBOLD, 8)).pack(anchor="w", pady=(0, 6))
        self.preview_canvas = tk.Canvas(preview, width=300, height=340, bg=theme["desk"], bd=0, highlightthickness=0)
        self.preview_canvas.pack(pady=(0, 12))
        self.section(preview, tr("Shortcuts on the right"), self.fill_apps, width=300).pack(pady=(0, 12))
        self.section(preview, tr("Display"), self.fill_display, width=300).pack()
        right = tk.Frame(columns, bg=theme["desk"])
        right.pack(side="left", anchor="n")
        self.section(right, tr("Mascot"), self.fill_mascots).pack(pady=(0, 12))
        self.section(right, tr("Size"), self.fill_size).pack()
        self.sync()
        self.finish_build()

    def finish_build(self):
        if self.placed:
            x, y = self.top.winfo_x(), self.top.winfo_y()
            self.top.update_idletasks()
            place_window(self.top, x, y, self.top.winfo_reqwidth(), self.top.winfo_reqheight())

    def toggle_page(self):
        self.page = "main" if self.page == "advanced" else "advanced"
        self.build()

    # ------------------------------------------------------------------ advanced page

    def build_advanced(self, outer):
        theme = self.app.theme
        columns = tk.Frame(outer, bg=theme["desk"])
        columns.pack(pady=(18, 4))
        left = tk.Frame(columns, bg=theme["desk"])
        left.pack(side="left", padx=(0, 18), anchor="n")
        right = tk.Frame(columns, bg=theme["desk"])
        right.pack(side="left", anchor="n")
        self.section(left, tr("General"), self.fill_general, width=440).pack(pady=(0, 12))
        self.section(left, tr("Speech recognition"), self.fill_speech, width=440).pack()
        self.section(right, tr("Transcription style"), self.fill_style, width=440).pack(pady=(0, 12))
        self.section(right, tr("Text rewriting"), self.fill_rewrite, width=440).pack()
        self.refresh_advanced()

    def refresh_advanced(self):
        for refresh in self.refreshers:
            refresh()

    def startup_enabled(self) -> bool:
        value = self.app.prefs.get("startup")
        if value is None:
            if not hasattr(self.app, "startup_actual"):
                self.app.startup_actual = startup.is_enabled()
            return self.app.startup_actual
        return bool(value)

    def choice(self, parent, options, getter, setter, pady=(10, 0), label=None, label_width=None):
        """A label on the left and a row of exclusive buttons on the right; updates in place."""
        theme = self.app.theme
        bg = theme["card"]
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=pady)
        if label:
            tk.Label(row, text=label, bg=bg, fg=theme["text"], font=(FONT_REGULAR, 10)).pack(side="left")
        buttons = []
        for caption, value in reversed(options):
            widget = tk.Button(row, text=caption, relief="flat", bd=0, highlightthickness=1, font=(FONT_SEMIBOLD, 9),
                               padx=11, pady=4, cursor="hand2")
            widget.configure(command=lambda v=value: (setter(v), self.refresh_advanced()))
            widget.pack(side="right", padx=(6, 0))
            buttons.append((value, widget))

        def refresh():
            current = getter()
            for value, widget in buttons:
                restyle_toggle(widget, theme, current == value, bg)

        self.refreshers.append(refresh)
        return row

    def entry(self, parent, initial: str, on_change, width: int = 26):
        theme = self.app.theme
        variable = tk.StringVar(value=initial)
        field = tk.Entry(parent, textvariable=variable, width=width, relief="flat", bg=theme["panel"], fg=theme["text"],
                         insertbackground=theme["text"], highlightthickness=1, highlightbackground=theme["border"],
                         highlightcolor=theme["accent"], font=(FONT_MONO, 9))
        field.bind("<Button-1>", lambda event: field.focus_force())
        variable.trace_add("write", lambda *_: on_change(variable.get().strip()))
        return field, variable

    def fill_general(self, inner):
        app = self.app
        self.choice(inner, ((tr("On"), "on"), (tr("Off"), "off")), lambda: "on" if self.startup_enabled() else "off",
                    lambda v: app.prefs.__setitem__("startup", v == "on"), label=tr("Launch at Windows startup"))
        self.choice(inner, ((tr("Type into the field"), "type"), (tr("Copy only"), "copy")), lambda: app.prefs["insert"],
                    lambda v: app.prefs.__setitem__("insert", v), label=tr("When the text is ready"))
        self.choice(inner, (("Français", "fr"), ("English", "en")), lambda: app.prefs["language"],
                    lambda v: (app.prefs.__setitem__("language", v), app.apply_advanced()), label=tr("Dictation language"))

    def fill_speech(self, inner):
        app = self.app
        config = app.prefs["transcription"]

        def set_engine(value):
            config["engine"] = value
            app.apply_advanced()
            self.top.after(10, self.build)          # the section below changes shape

        self.choice(inner, ((tr("CPU"), "local"), ("GPU", "gpu"), ("OpenAI", "openai"), ("Groq", "groq"), (tr("Other"), "custom")),
                    lambda: config.get("engine", "local"), set_engine, pady=(2, 0), label=tr("Transcribe with"))
        engine = config.get("engine", "local")
        if engine == "local":
            self.fill_local_models(inner)
        elif engine == "gpu":
            self.fill_gpu(inner)
        else:
            self.fill_remote(inner)
        button(inner, self.app.theme, tr("Benchmark the models…"), app.open_benchmark).pack(anchor="w", pady=(8, 0))

    def fill_local_models(self, inner, on_gpu: bool = False):
        app, theme = self.app, self.app.theme
        bg = theme["card"]
        grid = tk.Frame(inner, bg=bg)
        grid.pack(fill="x", pady=(10, 0))
        holders = {}
        for index, (name, size, hint) in enumerate(prefs.WHISPER_MODELS):
            holder = tk.Frame(grid, bg=theme["border"], padx=2, pady=2)
            cell = tk.Frame(holder, bg=theme["panel"], cursor="hand2")
            cell.pack(fill="both", expand=True)
            top = tk.Frame(cell, bg=theme["panel"], cursor="hand2")
            top.pack(fill="x", padx=8, pady=(6, 0))
            tk.Label(top, text=name, bg=theme["panel"], fg=theme["text"], font=(FONT_SEMIBOLD, 10), cursor="hand2").pack(side="left")
            if name == "base":
                tk.Label(top, text="★", bg=theme["panel"], fg=theme["accent"], font=(FONT_SEMIBOLD, 9), cursor="hand2").pack(side="left", padx=(4, 0))
            size_text = (f"≈{accel.download_size_mb(name)} MB" + (" ✓" if accel.model_installed(name) else "")) if on_gpu else size
            tk.Label(cell, text=size_text, bg=theme["panel"], fg=theme["ok"] if on_gpu and accel.model_installed(name) else theme["accent"],
                     font=(FONT_MONO, 8), cursor="hand2").pack(anchor="w", padx=8)
            tk.Label(cell, text=tr(hint), bg=theme["panel"], fg=theme["sec"], font=(FONT_REGULAR, 8), wraplength=112, justify="left",
                     cursor="hand2").pack(anchor="w", padx=8, pady=(0, 7))
            for widget in (holder, cell, top, *cell.winfo_children(), *top.winfo_children()):
                widget.bind("<Button-1>", lambda event, n=name: (app.prefs.__setitem__("whisper_model", n), app.apply_advanced(),
                                                              self.refresh_advanced(), self.on_model_picked()))
            holder.grid(row=index // 3, column=index % 3, padx=(0, 8), pady=(0, 8), sticky="nsew")
            holders[name] = holder
        for column in range(3):
            grid.grid_columnconfigure(column, uniform="speech", weight=1)

        def refresh():
            for name, holder in holders.items():
                holder.configure(bg=theme["accent"] if app.prefs["whisper_model"] == name else theme["border"])

        self.refreshers.append(refresh)
        if not on_gpu:
            tk.Label(inner, text="★ " + tr("Fast default. Models run on this PC and download once; compare them with the benchmark."), bg=bg,
                     fg=theme["sec"], font=(FONT_REGULAR, 8), wraplength=400, justify="left").pack(anchor="w", pady=(2, 0))

    def on_model_picked(self):
        """In GPU mode the status rows depend on the chosen model."""
        if self.app.prefs["transcription"].get("engine") == "gpu":
            self.top.after(10, self.build)

    def fill_gpu(self, inner):
        app, theme = self.app, self.app.theme
        bg = theme["card"]
        info = getattr(app, "gpu_info", None)
        if info is None:
            info = app.gpu_info = gpu.detect()
        model = app.prefs["whisper_model"]
        self.fill_local_models(inner, on_gpu=True)

        def status(ok: bool | None, text: str):
            row = tk.Frame(inner, bg=bg)
            row.pack(fill="x", pady=(2, 0))
            mark = {True: ("✓", theme["ok"]), False: ("✗", theme["err"]), None: ("•", theme["sec"])}[ok]
            tk.Label(row, text=mark[0], bg=bg, fg=mark[1], font=(FONT_SEMIBOLD, 9), width=2).pack(side="left")
            tk.Label(row, text=text, bg=bg, fg=theme["text"] if ok else theme["sec"], font=(FONT_REGULAR, 9), anchor="w", justify="left",
                     wraplength=380).pack(side="left", fill="x")

        best = info["best"]
        status(bool(best), (best["name"] if best else tr("No graphics card found")))
        status(info["vulkan_ready"] if best else None, tr("Graphics driver with Vulkan support") if info["vulkan_ready"]
               else tr("The graphics driver has no Vulkan support: install or update it"))
        status(accel.runtime_installed(), tr("GPU speech runtime") + (" · " + tr("installed") if accel.runtime_installed() else
                                                                          (" · " + tr("not published yet") if not accel.runtime_published()
                                                                           else " · " + tr("to download"))))
        status(accel.model_installed(model), tr("Model {model}").format(model=model) + (" · " + tr("installed") if accel.model_installed(model)
                                                                                        else f" · {tr('to download')} ≈{accel.download_size_mb(model)} MB"))
        message = tk.Label(inner, bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 8), anchor="w", justify="left", wraplength=400)
        message.pack(fill="x", pady=(6, 0))
        bar = tk.Canvas(inner, width=400, height=6, bg=bg, bd=0, highlightthickness=0)

        def ui(function):
            try:
                self.top.after(0, function)
            except (RuntimeError, tk.TclError):
                pass

        def say(text: str, good: bool | None = None):
            message.configure(text=text, fg=theme["ok"] if good else (theme["err"] if good is False else theme["sec"]))

        def draw_progress(done: int, total: int, label: str):
            bar.pack(pady=(4, 0))
            bar.delete("all")
            bar.create_line(3, 3, 397, 3, width=6, capstyle="round", fill=theme["track"])
            if total:
                bar.create_line(3, 3, 3 + 394 * done / total, 3, width=6, capstyle="round", fill=theme["accent"])
            say(f"{label} {done / 1e6:.0f} / {total / 1e6:.0f} MB" if total else label)

        def set_up():
            setup_button.configure(state="disabled")

            def work():
                try:
                    if not accel.runtime_installed():
                        accel.install_runtime(lambda d, t: ui(lambda: draw_progress(d, t, tr("Downloading the runtime"))))
                    if not accel.model_installed(model):
                        accel.install_model(model, lambda d, t: ui(lambda: draw_progress(d, t, tr("Downloading the model"))))
                except accel.AccelError as exc:
                    ui(lambda: (say(str(exc), False), setup_button.configure(state="normal")))
                    return
                ui(self.build)

            threading.Thread(target=work, daemon=True).start()

        def test():
            say(tr("Starting the GPU speech server…"))

            def work():
                try:
                    started = time.perf_counter()
                    accel.server.ensure(model)
                    loaded = time.perf_counter() - started
                    started = time.perf_counter()
                    accel.server.transcribe(np.zeros(8000, dtype=np.float32), "en", model)
                    ui(lambda: say(tr("✓ GPU ready: model loaded in {load} s, answers in {run} s").format(
                        load=f"{loaded:.1f}", run=f"{time.perf_counter() - started:.2f}"), True))
                except accel.AccelError as exc:
                    ui(lambda: say("✗ " + str(exc), False))

            threading.Thread(target=work, daemon=True).start()

        def refresh_detection():
            app.gpu_info = None
            self.build()

        actions = tk.Frame(inner, bg=bg)
        actions.pack(fill="x", pady=(6, 0))
        needs_setup = not (accel.runtime_installed() and accel.model_installed(model))
        setup_button = button(actions, theme, tr("Set up GPU acceleration"), set_up, primary=True)
        if needs_setup and info["vulkan_ready"] and accel.runtime_published():
            setup_button.pack(side="left")
        elif not needs_setup:
            button(actions, theme, tr("Test the GPU"), test, primary=True).pack(side="left")
        if best and not info["vulkan_ready"]:
            button(actions, theme, tr("Open the driver page"), lambda: webbrowser.open(info["driver_page"]), primary=True).pack(side="left")
        button(actions, theme, tr("Detect again"), refresh_detection).pack(side="right")
        tk.Label(inner, text=tr("Downloads only happen when you press the button: the runtime is built from public source by this project "
                                "(checksum verified), the model comes from the whisper.cpp repository on Hugging Face (checksum verified)."),
                 bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 8), wraplength=400, justify="left").pack(anchor="w", pady=(6, 0))

    def fill_remote(self, inner):
        app, theme = self.app, self.app.theme
        bg = theme["card"]
        config = app.prefs["transcription"]
        engine = config["engine"]
        preset = transcribe.ENGINES[engine]

        def row(label: str):
            frame = tk.Frame(inner, bg=bg)
            frame.pack(fill="x", pady=(8, 0))
            tk.Label(frame, text=label, bg=bg, fg=theme["text"], font=(FONT_REGULAR, 10)).pack(side="left")
            return frame

        # model
        frame = row(tr("Model"))
        field, variable = self.entry(frame, config.get("model") or preset["model"],
                                     lambda v: config.__setitem__("model", "" if v == preset["model"] else v), width=30)
        field.pack(side="right", ipady=3)
        if preset["models"]:
            chips = tk.Frame(inner, bg=bg)
            chips.pack(fill="x", pady=(5, 0))
            for index, name in enumerate(preset["models"]):
                chip = tk.Button(chips, text=name, relief="flat", bd=0, highlightthickness=1, font=(FONT_MONO, 8), padx=7, pady=2,
                                 cursor="hand2", command=lambda v=name: variable.set(v))
                chip.grid(row=index // 2, column=index % 2, padx=(5, 0), pady=(0, 4), sticky="e")
                restyle_toggle(chip, theme, False, bg)
            chips.grid_columnconfigure(0, weight=1)
        if engine == "custom":
            frame = row(tr("Service address"))
            address, _ = self.entry(frame, config.get("base_url", ""), lambda v: config.__setitem__("base_url", v), width=30)
            address.pack(side="right", ipady=3)
            tk.Label(inner, text=tr("OpenAI-compatible, for example https://api.example.com/v1"), bg=bg, fg=theme["sec"],
                     font=(FONT_REGULAR, 8)).pack(anchor="e")

        # API key: typed here, stored encrypted for this Windows account, never shown again
        frame = row(tr("API key"))
        key_field = tk.Entry(frame, show="•", width=22, relief="flat", bg=theme["panel"], fg=theme["text"], insertbackground=theme["text"],
                             highlightthickness=1, highlightbackground=theme["border"], highlightcolor=theme["accent"], font=(FONT_MONO, 9))
        key_field.bind("<Button-1>", lambda event: key_field.focus_force())
        status = tk.Label(inner, bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 8), anchor="w", justify="left", wraplength=400)

        def show_status(text: str | None = None, good: bool | None = None):
            source = transcribe.key_source(config)
            if text is None:
                text = {"stored": tr("Key saved on this PC (encrypted)"),
                        "environment": tr("Using {env} from your environment").format(env=preset["env"]),
                        "": tr("No key yet: paste it above, or set {env}").format(env=preset["env"])}[source]
                good = True if source else None
            status.configure(text=text, fg=theme["ok"] if good else (theme["err"] if good is False else theme["sec"]))

        def save_key():
            value = key_field.get().strip()
            if value:
                config["api_key"] = secrets_store.protect(value)
                key_field.delete(0, "end")
            show_status()

        def remove_key():
            config["api_key"] = ""
            show_status()

        def test():
            show_status(tr("Testing…"))
            snapshot = dict(config)

            def work():
                ok, message = transcribe.test_connection(snapshot)
                self.top.after(0, lambda: show_status(("✓ " if ok else "✗ ") + message, ok))

            threading.Thread(target=work, daemon=True).start()

        for caption, command in ((tr("Test"), test), (tr("Remove"), remove_key), (tr("Save key"), save_key)):
            tk.Button(frame, text=caption, command=command, relief="flat", bd=0, highlightthickness=1, font=(FONT_SEMIBOLD, 8),
                      padx=8, pady=3, cursor="hand2", bg=theme["card"], fg=theme["text"], activebackground=theme["border"],
                      highlightbackground=theme["border"]).pack(side="right", padx=(5, 0))
        key_field.pack(side="right", ipady=3)
        status.pack(fill="x", pady=(4, 0))
        show_status()
        tk.Label(inner, text=tr("The recording is sent to this service to be transcribed. An API key is required: a ChatGPT or Codex "
                                "sign-in does not cover the audio API, which the service bills separately."),
                 bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 8), wraplength=400, justify="left").pack(anchor="w", pady=(6, 0))

    def fill_rewrite(self, inner):
        app, theme = self.app, self.app.theme
        bg = theme["card"]
        self.choice(inner, ((tr("Automatic"), "auto"), ("Codex", "codex"), ("Claude", "claude"), (tr("Off"), "off")),
                    lambda: app.prefs["rewrite_provider"], lambda v: app.prefs.__setitem__("rewrite_provider", v),
                    label=tr("Rewrite with"))
        status = tk.Label(inner, bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 8), anchor="w", justify="left")
        status.pack(fill="x", pady=(4, 0))

        def refresh_status():
            found = lambda ok: tr("found") if ok else tr("not found")
            status.configure(text=f"Codex: {found(codex_available(app.prefs['codex']))}   ·   Claude: {found(claude_available())}")

        self.refreshers.append(refresh_status)

        def model_row(label: str, key: str, presets=()):
            row = tk.Frame(inner, bg=bg)
            row.pack(fill="x", pady=(10, 0))
            tk.Label(row, text=label, bg=bg, fg=theme["text"], font=(FONT_REGULAR, 10)).pack(side="left")
            field, variable = self.entry(row, app.prefs[key].get("model", ""), lambda v, k=key: app.prefs[k].__setitem__("model", v))
            field.pack(side="right", ipady=3)
            if presets:
                chips = tk.Frame(inner, bg=bg)
                chips.pack(fill="x", pady=(5, 0))
                for caption, value in reversed(presets):
                    chip = tk.Button(chips, text=caption, relief="flat", bd=0, highlightthickness=1, font=(FONT_SEMIBOLD, 8), padx=8, pady=2,
                                     cursor="hand2", command=lambda v=value, var=variable: var.set(v))
                    chip.pack(side="right", padx=(5, 0))
                    restyle_toggle(chip, theme, False, bg)

        model_row(tr("Codex model"), "codex")
        self.choice(inner, ((tr("Low"), "low"), (tr("Medium"), "medium"), (tr("High"), "high")),
                    lambda: app.prefs["codex"].get("reasoning") or "low", lambda v: app.prefs["codex"].__setitem__("reasoning", v),
                    label=tr("Reasoning (Codex)"))
        model_row(tr("Claude model"), "claude", ((tr("Default"), ""), ("sonnet", "sonnet"), ("opus", "opus"), ("haiku", "haiku")))
        tk.Label(inner, text=tr("Leave empty for the tool's default model.") + " " +
                 tr("Your dictation text is sent to the selected service to be rewritten."), bg=bg, fg=theme["sec"],
                 font=(FONT_REGULAR, 8), wraplength=400, justify="left").pack(anchor="w", pady=(8, 0))

    def fill_style(self, inner):
        app, theme = self.app, self.app.theme
        bg = theme["card"]
        chips = tk.Frame(inner, bg=bg)
        chips.pack(fill="x", pady=(10, 0))
        buttons = {}
        entries = [(item[0], tr(item[1])) for item in styles.STYLES] + [(styles.CUSTOM, tr("Custom"))]
        description = tk.Label(inner, bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 9), anchor="w", justify="left", wraplength=400)
        box = tk.Text(inner, width=50, height=9, wrap="word", relief="flat", bg=theme["panel"], fg=theme["text"],
                      insertbackground=theme["text"], highlightthickness=1, highlightbackground=theme["border"],
                      highlightcolor=theme["accent"], font=(FONT_REGULAR, 10), padx=8, pady=6, undo=True)
        loading = {"busy": False}

        def load(text: str):
            loading["busy"] = True
            box.delete("1.0", "end")
            box.insert("1.0", text)
            loading["busy"] = False

        def pick(style_id: str):
            if style_id == styles.CUSTOM:
                text = app.prefs["rewrite_prompt"].strip() or box.get("1.0", "end-1c")
                app.prefs["rewrite_prompt"] = text
            else:
                text = styles.instruction(style_id)
            app.prefs["rewrite_style"] = style_id
            load(text)
            self.refresh_advanced()

        for index, (style_id, caption) in enumerate(entries):
            chip = tk.Button(chips, text=caption, relief="flat", bd=0, highlightthickness=1, font=(FONT_SEMIBOLD, 9), padx=8, pady=5,
                             cursor="hand2", command=lambda sid=style_id: pick(sid))
            chip.grid(row=index // 3, column=index % 3, padx=(0, 6), pady=(0, 6), sticky="ew")
            buttons[style_id] = chip
        for column in range(3):
            chips.grid_columnconfigure(column, uniform="chip", weight=1)
        description.pack(fill="x", pady=(2, 8))
        box.pack(fill="x")
        box.bind("<Button-1>", lambda event: box.focus_force())

        def edited(_event=None):
            if loading["busy"]:
                return
            text = box.get("1.0", "end-1c")
            if app.prefs["rewrite_style"] != styles.CUSTOM and text.strip() != styles.instruction(app.prefs["rewrite_style"]).strip():
                app.prefs["rewrite_style"] = styles.CUSTOM
            if app.prefs["rewrite_style"] == styles.CUSTOM:
                app.prefs["rewrite_prompt"] = text
            self.refresh_advanced()

        box.bind("<KeyRelease>", edited)
        box.bind("<<Paste>>", lambda event: box.after(10, edited))

        def refresh():
            current = app.prefs["rewrite_style"]
            for style_id, chip in buttons.items():
                restyle_toggle(chip, theme, current == style_id, bg)
            found = styles.preset(current)
            description.configure(text=tr(found[2]) if found else tr("Your own instruction"))

        self.refreshers.append(refresh)
        current = app.prefs["rewrite_style"]
        load(app.prefs["rewrite_prompt"] if current == styles.CUSTOM else styles.instruction(current))
        tk.Label(inner, text=tr("This instruction is sent to the model with your dictation. Edit it freely: the style switches to Custom."),
                 bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 8), wraplength=400, justify="left").pack(anchor="w", pady=(6, 0))

    def section(self, parent, title: str, fill, width: int = 470) -> tk.Canvas:
        theme = self.app.theme

        def build(inner):
            tk.Label(inner, text=title, bg=theme["card"], fg=theme["text"], font=(FONT_SEMIBOLD, 11)).pack(anchor="w")
            fill(inner)
        return card(parent, theme, width, build, pad=16, outer=theme["desk"])

    # ------------------------------------------------------------------ live sync

    def sync(self):
        if self.page != "main":
            return
        self.redraw_preview()
        for value, holder in self.tile_holders.items():
            holder.configure(bg=self.app.theme["accent"] if self.app.prefs["mascot"] == value else self.app.theme["border"])
        self.refresh_size()
        theme = self.app.theme
        for (key, value), widget in self.toggles.items():
            current = i18n.LANG if key == "ui_language" else self.app.prefs[key]
            restyle_toggle(widget, theme, current == value, theme["card"])

    def redraw_preview(self):
        app, theme = self.app, self.app.theme
        canvas = self.preview_canvas
        canvas.delete("all")
        width, height = 300, 340
        backdrop = photo(rounded_box(width, height, theme["panel"], theme["border"], 16, outer=theme["desk"]))
        canvas.create_image(0, 0, anchor="nw", image=backdrop)
        sprite_image = app.load_mascot().idle.frames[0]
        if sprite_image.height > 150:          # illustrative only: keep the two round buttons on screen
            ratio = 150 / sprite_image.height
            sprite_image = sprite_image.resize((max(1, round(sprite_image.width * ratio)), 150), Image.Resampling.LANCZOS)
        sprite = photo(sprite_image)
        mid = height // 2
        shift = -24 if app.prefs["side"] == "right" else 24
        canvas.create_image(width // 2 + shift, mid, image=sprite)
        gauge = photo(circle_button(BUTTON, theme["panel"], theme["border"], icon("gauge", theme["accent"], 22)))
        mic = photo(circle_button(BUTTON, theme["panel"], theme["ok"], icon("mic", theme["ok"], 22), border_width=1.5))
        top_y = max(34, mid - sprite_image.height // 2 - 34)
        bottom_y = min(height - 34, mid + sprite_image.height // 2 + 34)
        canvas.create_image(width // 2 + shift, top_y, image=gauge)
        canvas.create_image(width // 2 + shift, bottom_y, image=mic)
        glyphs = [icon("claude", CLAUDE_COLOR, 24), icon("openai", theme["text"], 24), icon("sliders", theme["sec"], 22),
                  icon("plus", theme["sec"], 20)]
        rail_img, _ = rail_image(theme, glyphs, ["C", "O", "s", "+"])
        rail = photo(rail_img)
        rail_x = width - 16 - rail_img.width // 2 if app.prefs["side"] == "right" else 16 + rail_img.width // 2
        canvas.create_image(rail_x, mid, image=rail)
        canvas.create_text(16, height - 14, anchor="w", text=tr("Current size: {size} px").format(size=app.prefs['size']), fill=theme["sec"], font=(FONT_REGULAR, 8))
        self.preview_images = [backdrop, sprite, gauge, mic, rail]

    # ------------------------------------------------------------------ sections

    def tile(self, parent, image: Image.Image | None, title: str, subtitle: str, value: str, command):
        theme = self.app.theme
        holder = tk.Frame(parent, bg=theme["accent"] if self.app.prefs["mascot"] == value else theme["border"], padx=2, pady=2)
        inner = tk.Frame(holder, bg=theme["panel"], cursor="hand2")
        inner.pack(fill="both", expand=True)
        if image is not None:
            ph = photo(image)
            self.images.append(ph)
            tk.Label(inner, image=ph, bg=theme["panel"], cursor="hand2").pack(pady=(8, 2))
        tk.Label(inner, text=title, bg=theme["panel"], fg=theme["text"], font=(FONT_SEMIBOLD, 9), wraplength=90, cursor="hand2").pack()
        tk.Label(inner, text=subtitle, bg=theme["panel"], fg=theme["sec"], font=(FONT_REGULAR, 8), cursor="hand2").pack(pady=(0, 7))
        for widget in (holder, inner, *inner.winfo_children()):
            widget.bind("<Button-1>", lambda event: command())
        if value:
            self.tile_holders[value] = holder
        return holder

    def fill_mascots(self, inner: tk.Frame):
        theme = self.app.theme
        bg = theme["card"]
        holder = tk.Frame(inner, bg=bg)
        holder.pack(fill="x", pady=(10, 0))
        view = tk.Canvas(holder, width=420, height=330, bg=bg, bd=0, highlightthickness=0)
        view.pack(side="left")
        bar = tk.Canvas(holder, width=6, height=330, bg=bg, bd=0, highlightthickness=0)
        bar.pack(side="left", padx=(6, 0))
        self.grid = tk.Frame(view, bg=bg)
        view.create_window(0, 0, window=self.grid, anchor="nw")
        self.fill_tiles()
        self.grid.update_idletasks()
        total = max(self.grid.winfo_reqheight(), 1)
        view.configure(scrollregion=(0, 0, 420, total))

        def draw_bar(first, last):
            bar.delete("all")
            if float(last) - float(first) < 0.999:
                bar.create_line(3, 330 * float(first) + 3, 3, 330 * float(last) - 3, width=4, capstyle="round", fill=theme["track"])

        view.configure(yscrollcommand=draw_bar)

        def wheel(event):
            if total > 330:
                view.yview_scroll(-1 if event.delta > 0 else 1, "units")

        def bind_wheel(widget):
            widget.bind("<MouseWheel>", wheel)
            for child in widget.winfo_children():
                bind_wheel(child)

        view.configure(yscrollincrement=40)
        bind_wheel(view)
        bind_wheel(self.grid)
        draw_bar(*view.yview())
        bloub = tk.Frame(inner, bg=bg, highlightthickness=1, highlightbackground=theme["border"], padx=10, pady=8)
        bloub.pack(fill="x", pady=(2, 0))
        text = tk.Frame(bloub, bg=bg)
        text.pack(side="left")
        tk.Label(text, text="Bloub", bg=bg, fg=theme["text"], font=(FONT_SEMIBOLD, 9)).pack(anchor="w")
        tk.Label(text, text=tr("Animated SVG avatar; opens in your browser."), bg=bg, fg=theme["sec"],
                 font=(FONT_REGULAR, 8), justify="left", wraplength=250).pack(anchor="w")
        button(bloub, theme, tr("Open Bloub ↗"), lambda: webbrowser.open(prefs.BLOUB_URL), primary=True).pack(side="right")
        tk.Label(inner, text=tr("Image: transparent background recommended. Pack: clippy.js-style folder (agent.js + map.png)."), bg=bg,
                 fg=theme["sec"], font=(FONT_REGULAR, 8), wraplength=410, justify="left").pack(anchor="w", pady=(8, 0))

    def fill_tiles(self):
        theme = self.app.theme
        for child in self.grid.winfo_children():
            child.destroy()
        self.tile_holders = {}
        entries: list[tuple[Image.Image | None, str, str, str]] = []
        sheet = Image.open(ROVER).convert("RGBA").crop((0, 0, SPRITE_W, SPRITE_H)).resize((44, 48), Image.Resampling.LANCZOS)
        entries.append((sheet, "Rover", tr("Default"), "rover"))
        dot = Image.new("RGBA", (26 * SS, 26 * SS), (0, 0, 0, 0))
        ImageDraw.Draw(dot).ellipse((0, 0, 26 * SS - 1, 26 * SS - 1), fill=rgb(theme["accent"]))
        dot_tile = Image.new("RGBA", (44, 48), (0, 0, 0, 0))
        dot_tile.alpha_composite(dot.resize((26, 26), Image.Resampling.LANCZOS), (9, 11))
        entries.append((dot_tile, tr("Dot only"), "Minimal", "dot"))
        for path in prefs.list_mascots():
            try:
                thumb = Image.open(path).convert("RGBA")
            except OSError:
                continue
            if mascots.is_atlas(thumb):
                thumb = thumb.crop((0, 0, SPRITE_W, SPRITE_H))
            scale = min(44 / thumb.width, 48 / thumb.height)
            thumb = thumb.resize((max(1, round(thumb.width * scale)), max(1, round(thumb.height * scale))), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (44, 48), (0, 0, 0, 0))
            canvas.alpha_composite(thumb, ((44 - thumb.width) // 2, (48 - thumb.height) // 2))
            entries.append((canvas, path.stem.replace("-", " ").replace("_", " ").title()[:16], tr("Imported"), f"file:{path.name}"))
        columns = 4
        for column in range(columns):
            self.grid.grid_columnconfigure(column, uniform="tile", weight=1)
        for pet_name, pet_file in prefs.codex_pets():
            try:
                cell = Image.open(pet_file).convert("RGBA").crop((0, 0, SPRITE_W, SPRITE_H))
                cell = cell.resize((44, 48), Image.Resampling.LANCZOS)
            except OSError:
                continue
            entries.append((cell, pet_name[:16], tr("Codex pet"), f"pet:{pet_name}"))
        for folder in prefs.list_packs():
            preview = mascots.pack_preview(folder, (44, 48))
            if preview is not None:
                entries.append((preview, {"Rover": "Rover XP"}.get(folder.name, folder.name)[:16], tr("Animated pack"), f"pack:{folder.name}"))
        for index, (img, title, subtitle, value) in enumerate(entries):
            tile = self.tile(self.grid, img, title, subtitle, value, lambda v=value: self.set(mascot=v))
            tile.grid(row=index // columns, column=index % columns, padx=(0, 8), pady=(0, 8), sticky="nsew")
        tail = [(icon_pil("upload", theme["text"], 26), tr("Import"), "PNG, GIF, WebP", self.import_image),
                (icon_pil("upload", theme["text"], 26), tr("Import a pack"), tr("clippy.js folder"), self.import_pack)]
        for offset, (img, title, subtitle, command) in enumerate(tail):
            position = len(entries) + offset
            tile = self.tile(self.grid, img, title, subtitle, "", command)
            tile.grid(row=position // columns, column=position % columns, padx=(0, 10), pady=(0, 10), sticky="nsew")
            tile.configure(bg=theme["border"])

    def import_image(self):
        chosen = filedialog.askopenfilename(parent=self.top, title=tr("Import a mascot"),
                                            filetypes=[("Images", "*.png *.gif *.webp")])
        if not chosen:
            return
        try:
            value = prefs.import_mascot(chosen)
        except (OSError, ValueError) as exc:
            messagebox.showerror(tr("Import failed"), str(exc), parent=self.top)
            return
        self.fill_tiles()
        self.set(mascot=value)

    def import_pack(self):
        chosen = filedialog.askdirectory(parent=self.top, title=tr("Choose the pack folder (agent.js and map.png)"))
        if not chosen:
            return
        try:
            value = prefs.import_pack(chosen)
        except (OSError, ValueError) as exc:
            messagebox.showerror(tr("Import failed"), str(exc), parent=self.top)
            return
        self.fill_tiles()
        self.set(mascot=value)

    def fill_apps(self, inner: tk.Frame):
        app, theme = self.app, self.app.theme
        bg = theme["card"]
        for index, entry in enumerate(app.prefs["apps"]):
            row = tk.Frame(inner, bg=bg)
            row.pack(fill="x", pady=(8, 0))
            tk.Label(row, text=entry["name"][:12], bg=bg, fg=theme["text"], font=(FONT_SEMIBOLD, 9)).pack(side="left")
            target = entry.get("path") or entry.get("url", "")
            tk.Label(row, text=(target[:20] + "…") if len(target) > 21 else target, bg=bg, fg=theme["sec"],
                     font=(FONT_REGULAR, 8)).pack(side="left", padx=(8, 0))
            remove = tk.Label(row, text="✕", bg=bg, fg=theme["sec"], font=(FONT_REGULAR, 10), cursor="hand2", padx=6)
            remove.pack(side="right")
            remove.bind("<Enter>", lambda event, w=remove: w.configure(fg=theme["err"]))
            remove.bind("<Leave>", lambda event, w=remove: w.configure(fg=theme["sec"]))
            remove.bind("<Button-1>", lambda event, i=index: app.remove_app(i))
        button(inner, theme, tr("Add a shortcut…"), app.add_app_dialog, primary=True).pack(anchor="w", pady=(12, 0))

    def fill_size(self, inner: tk.Frame):
        app, theme = self.app, self.app.theme
        bg = theme["card"]
        row = tk.Frame(inner, bg=bg)
        row.pack(fill="x", pady=(8, 0))
        self.readout = tk.Label(row, text=f"{app.prefs['size']} px", bg=bg, fg=theme["text"], font=(FONT_MONO, 10))
        self.readout.pack(side="right")
        width, low, high, step = 340, prefs.SIZE_MIN, prefs.SIZE_MAX, prefs.SIZE_STEP
        slider = tk.Canvas(row, width=width, height=26, bg=bg, bd=0, highlightthickness=0, cursor="hand2")
        slider.pack(side="left")
        chosen = {"value": app.prefs["size"]}

        def draw(value):
            slider.delete("all")
            x = 10 + (width - 20) * (value - low) / (high - low)
            slider.create_line(10, 13, width - 10, 13, width=4, capstyle="round", fill=theme["track"])
            slider.create_line(10, 13, x, 13, width=4, capstyle="round", fill=theme["accent"])
            slider.create_oval(x - 9, 4, x + 9, 22, fill=theme["accent"], outline=bg, width=2)

        def pick(event):
            fraction = (min(max(event.x, 10), width - 10) - 10) / (width - 20)
            chosen["value"] = low + round(fraction * (high - low) / step) * step
            self.readout.configure(text=f"{chosen['value']} px")
            draw(chosen["value"])

        def release(event):
            if chosen["value"] != app.prefs["size"]:
                self.set(size=chosen["value"])

        slider.bind("<Button-1>", pick)
        slider.bind("<B1-Motion>", pick)
        slider.bind("<ButtonRelease-1>", release)
        self.draw_slider = lambda value: (chosen.update(value=value), draw(value))
        draw(chosen["value"])
        presets = tk.Frame(inner, bg=bg)
        presets.pack(fill="x", pady=(8, 0))
        for label, value in prefs.SIZE_PRESETS:
            preset = tk.Button(presets, text=f"{tr(label)} · {value}", command=lambda v=value: self.set(size=v), relief="flat", bd=0,
                               highlightthickness=1, font=(FONT_SEMIBOLD, 9), padx=8, pady=5, cursor="hand2")
            preset.pack(side="left", expand=True, fill="x", padx=(0, 6))
            self.preset_buttons.append((value, preset))

    def refresh_size(self):
        theme = self.app.theme
        size = self.app.prefs["size"]
        self.readout.configure(text=f"{size} px")
        self.draw_slider(size)
        for value, widget in self.preset_buttons:
            restyle_toggle(widget, theme, size == value, theme["card"])

    def fill_display(self, inner: tk.Frame):
        theme = self.app.theme
        bg = theme["card"]

        def segmented(label: str, options, key: str):
            row = tk.Frame(inner, bg=bg)
            row.pack(fill="x", pady=(10, 0))
            tk.Label(row, text=label, bg=bg, fg=theme["text"], font=(FONT_REGULAR, 10)).pack(side="left")
            for caption, value in reversed(options):
                widget = tk.Button(row, text=caption, command=lambda v=value: self.set(**{key: v}), relief="flat", bd=0,
                                   highlightthickness=1, font=(FONT_SEMIBOLD, 9), padx=12, pady=5, cursor="hand2")
                widget.pack(side="right", padx=(6, 0))
                self.toggles[(key, value)] = widget

        segmented(tr("Interface language"), (("Français", "fr"), ("English", "en")), "ui_language")
        segmented(tr("AI shortcuts"), ((tr("Left"), "left"), (tr("Right"), "right")), "side")
        segmented(tr("Theme"), ((tr("Dark"), "dark"), (tr("Light"), "light")), "theme")


def add_app_dialog_window(app: RoverApp) -> None:
    dialog = tk.Toplevel(app.root)
    dialog.overrideredirect(True)
    dialog.attributes("-topmost", True)
    theme = app.theme
    body, _ = make_chrome(dialog, app, tr("Add a shortcut"), dialog.destroy)
    form = tk.Frame(body, bg=theme["desk"], padx=22, pady=12)
    form.pack()
    tk.Label(form, text=tr("Website, local service or program"), bg=theme["desk"], fg=theme["text"], font=(FONT_SEMIBOLD, 10)).grid(row=0, column=0, columnspan=3, sticky="w")
    url_var, name_var = tk.StringVar(), tk.StringVar()
    entry_style = dict(relief="flat", bg=theme["card"], fg=theme["text"], insertbackground=theme["text"], highlightthickness=1,
                       highlightbackground=theme["border"], highlightcolor=theme["accent"])
    entry = tk.Entry(form, textvariable=url_var, width=44, **entry_style)
    entry.grid(row=1, column=0, sticky="ew", pady=(4, 2), ipady=5)

    def browse_file():
        chosen = filedialog.askopenfilename(parent=dialog, title=tr("Choose a program or shortcut"),
                                            filetypes=[(tr("Programs and shortcuts"), "*.exe *.lnk *.bat *.cmd *.url"), (tr("All files"), "*.*")])
        if chosen:
            url_var.set(os.path.normpath(chosen))

    def browse_folder():
        chosen = filedialog.askdirectory(parent=dialog, title=tr("Choose a folder"))
        if chosen:
            url_var.set(os.path.normpath(chosen))

    button(form, theme, tr("File…"), browse_file).grid(row=1, column=1, padx=(8, 0), pady=(4, 2))
    button(form, theme, tr("Folder…"), browse_folder).grid(row=1, column=2, padx=(6, 0), pady=(4, 2))
    tk.Label(form, text=tr("E.g. https://chatgpt.com, http://localhost:3000, C:\\Program Files\\App\\app.exe"), bg=theme["desk"], fg=theme["sec"],
             font=(FONT_REGULAR, 8), wraplength=420, justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 10))
    tk.Label(form, text=tr("Name (optional)"), bg=theme["desk"], fg=theme["text"], font=(FONT_SEMIBOLD, 10)).grid(row=3, column=0, sticky="w")
    tk.Entry(form, textvariable=name_var, width=44, **entry_style).grid(row=4, column=0, sticky="ew", pady=(4, 12), ipady=5)
    error = tk.Label(form, text="", bg=theme["desk"], fg=theme["err"], font=(FONT_REGULAR, 9))
    error.grid(row=5, column=0, columnspan=3, sticky="w")

    def submit():
        if app.add_app(name_var.get(), url_var.get()):
            dialog.destroy()
        elif len(app.prefs["apps"]) >= prefs.MAX_APPS:
            error.configure(text=tr("Up to {count} shortcuts: remove one first").format(count=prefs.MAX_APPS))
        else:
            error.configure(text=tr("Address, program or folder not found"))

    actions = tk.Frame(form, bg=theme["desk"])
    actions.grid(row=6, column=0, columnspan=3, sticky="e", pady=(8, 0))
    button(actions, theme, tr("Cancel"), dialog.destroy).pack(side="left", padx=(0, 8))
    button(actions, theme, tr("Add"), submit, primary=True).pack(side="left")
    dialog.bind("<Return>", lambda event: submit())
    dialog.update_idletasks()
    wx, wy = app.root.winfo_x(), app.root.winfo_y()
    left, top, right, bottom = monitor_work_area(wx, wy)
    width, height = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
    place_window(dialog, left + (right - left - width) // 2, top + (bottom - top - height) // 2, width, height)
    dialog.focus_force()
    entry.focus_set()


if __name__ == "__main__":
    if acquire_single_instance():
        RoverApp().run()
