"""Theme tokens and anti-aliased drawing helpers (Pillow) for Rover's panels and icons."""

from __future__ import annotations

import math
import re
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SS = 4  # supersampling factor

TRANSPARENT = "#010203"  # colour key of the borderless windows

# Shape and type tokens that every theme carries (the XP theme overrides them).
_MODERN = dict(field=None, field_border=None, radius=16, radius_card=12, radius_rail=None, frame_px=1, titlebar=False, blocks=False, tools=False,
               font="Segoe UI", font_strong="Segoe UI Semibold", strong_style=())

THEMES = {
    "dark": dict(desk="#111111", panel="#1c1c1e", card="#262628", border="#333336", text="#f2f2f0",
                 sec="#a3a3a8", accent="#f0b04a", on_accent="#1a1200", bar="#f0b04a", track="#3a3a3e",
                 rec="#ff6a5c", ok="#5ccf98", on_ok="#0d2016", err="#ff7d72", link="#f0b04a", kbd="#2e2e31",
                 frame="#333336", **{**_MODERN, "field": "#1c1c1e", "field_border": "#333336"}),
    "light": dict(desk="#efe7da", panel="#fffcf6", card="#f6efe3", border="#e4d8c4", text="#2b2620",
                  sec="#675e53", accent="#8f5200", on_accent="#ffffff", bar="#b0620a", track="#e6dac6",
                  rec="#c4372b", ok="#1f7a4d", on_ok="#ffffff", err="#b3261e", link="#8f5200", kbd="#efe5d3",
                  frame="#e4d8c4", **{**_MODERN, "field": "#fffcf6", "field_border": "#e4d8c4"}),
    # Windows XP "Luna": beige windows with a blue frame and title bar, white group boxes, green progress blocks, Tahoma.
    "xp": dict(desk="#ece9d8", panel="#ece9d8", card="#ffffff", border="#aca899", text="#000000",
               sec="#3f3f3f", accent="#2b5fb9", on_accent="#ffffff", bar="#30c030", track="#ffffff",
               rec="#c0190b", ok="#23761a", on_ok="#ffffff", err="#b3261e", link="#0b3bbd", kbd="#f5f4ea",
               frame="#0054e3", radius=8, radius_card=3, radius_rail=0, frame_px=3, titlebar=True, blocks=True, tools=True,
               font="Tahoma", font_strong="Tahoma", strong_style=("bold",), field="#ffffff", field_border="#7f9db9"),
}

CLAUDE_COLOR = "#D97757"

FONT_REGULAR = "Segoe UI"
FONT_SEMIBOLD = "Segoe UI Semibold"
FONT_MONO = "Consolas"

_ACTIVE = {"theme": "dark"}


def set_active(name: str) -> None:
    """The theme whose typeface rg() and sb() use (set by the app whenever the theme is read or changed)."""
    if name in THEMES:
        _ACTIVE["theme"] = name


XP_SIZES = {9: 8, 10: 9}            # Windows XP draws its controls in 8 pt Tahoma; ours are written for Segoe UI


def rg(size: int, *style: str) -> tuple:
    """Regular UI font for the active theme: a Tk font tuple."""
    theme = THEMES[_ACTIVE["theme"]]
    return (theme["font"], XP_SIZES.get(size, size) if theme["titlebar"] else size, *style)


def sb(size: int, *style: str) -> tuple:
    """Emphasised UI font (semibold, or bold in XP) for the active theme."""
    theme = THEMES[_ACTIVE["theme"]]
    weight = theme["strong_style"] if size >= 10 or not theme["titlebar"] else ()      # XP controls stay regular: bold Tahoma is too wide for them
    return (theme["font_strong"], XP_SIZES.get(size, size) if theme["titlebar"] else size, *weight, *style)


def rgb(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), alpha


# --------------------------------------------------------------------------- SVG paths

_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _arc(x1, y1, rx, ry, phi_deg, fa, fs, x2, y2):
    if rx == 0 or ry == 0:
        return [(x2, y2)]
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale
    numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, numerator / denominator)) if denominator else 0.0
    if fa == fs:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (x1 + x2) / 2, sp * cxp + cp * cyp + (y1 + y2) / 2
    th1 = math.atan2((y1p - cyp) / ry, (x1p - cxp) / rx)
    th2 = math.atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx)
    delta = th2 - th1
    if not fs and delta > 0:
        delta -= 2 * math.pi
    elif fs and delta < 0:
        delta += 2 * math.pi
    steps = max(8, int(abs(delta) / math.pi * 18) + 2)
    points = []
    for k in range(1, steps + 1):
        t = th1 + delta * k / steps
        px, py = rx * math.cos(t), ry * math.sin(t)
        points.append((cp * px - sp * py + cx, sp * px + cp * py + cy))
    return points


def flatten_path(d: str) -> list[tuple[list[tuple[float, float]], bool]]:
    """Flatten an SVG path (M L H V C A Z, relative or absolute) into polylines."""
    tokens = _TOKEN.findall(d)
    i, cmd = 0, ""
    x = y = sx = sy = 0.0
    subpaths: list[tuple[list[tuple[float, float]], bool]] = []
    current: list[tuple[float, float]] | None = None

    def number() -> float:
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if current is not None:
                    subpaths[-1] = (current, True)
                x, y = sx, sy
                continue
        relative, upper = cmd.islower(), cmd.upper()
        if upper == "M":
            nx, ny = number(), number()
            if relative:
                nx, ny = nx + x, ny + y
            x, y = sx, sy = nx, ny
            current = [(x, y)]
            subpaths.append((current, False))
            cmd = "l" if relative else "L"
        elif upper == "L":
            nx, ny = number(), number()
            x, y = (x + nx, y + ny) if relative else (nx, ny)
            current.append((x, y))
        elif upper == "H":
            nx = number()
            x = x + nx if relative else nx
            current.append((x, y))
        elif upper == "V":
            ny = number()
            y = y + ny if relative else ny
            current.append((x, y))
        elif upper == "C":
            c = [number() for _ in range(6)]
            if relative:
                c = [c[0] + x, c[1] + y, c[2] + x, c[3] + y, c[4] + x, c[5] + y]
            for k in range(1, 13):
                t = k / 12
                u = 1 - t
                current.append((u ** 3 * x + 3 * u * u * t * c[0] + 3 * u * t * t * c[2] + t ** 3 * c[4],
                                u ** 3 * y + 3 * u * u * t * c[1] + 3 * u * t * t * c[3] + t ** 3 * c[5]))
            x, y = c[4], c[5]
        elif upper == "A":
            rx, ry, rot = number(), number(), number()
            fa, fs = int(number()), int(number())
            nx, ny = number(), number()
            if relative:
                nx, ny = nx + x, ny + y
            current.extend(_arc(x, y, rx, ry, rot, fa, fs, nx, ny))
            x, y = nx, ny
        else:
            raise ValueError(f"Unsupported SVG command {cmd!r}")
    return subpaths


def _fill_mask(paths: list[str], view: float, size: int, offset=(0.0, 0.0)) -> Image.Image:
    scale = size / view
    mask = Image.new("1", (size, size), 0)
    for d in paths:
        for points, _closed in flatten_path(d):
            if len(points) < 3:
                continue
            layer = Image.new("1", (size, size), 0)
            ImageDraw.Draw(layer).polygon([((px + offset[0]) * scale, (py + offset[1]) * scale) for px, py in points], fill=1)
            mask = ImageChops.logical_xor(mask, layer)
    return mask.convert("L")


def svg_fill_icon(d: str, color: str, px: int, view: float = 24) -> Image.Image:
    """Even-odd filled SVG path, returned at SS x px resolution."""
    size = px * SS
    mask = _fill_mask([d], view, size)
    image = Image.new("RGBA", (size, size), rgb(color))
    image.putalpha(mask)
    return image


def svg_stroke_icon(paths: list[str], color: str, px: int, width: float, view: float = 24,
                    dots: tuple[tuple[float, float, float], ...] = ()) -> Image.Image:
    """Round-capped stroked SVG paths (plus optional filled dots), at SS x px resolution."""
    size = px * SS
    scale = size / view
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colour = rgb(color)
    radius = width * scale / 2
    for d in paths:
        for points, closed in flatten_path(d):
            scaled = [(px_ * scale, py_ * scale) for px_, py_ in points]
            if closed:
                scaled.append(scaled[0])
            if len(scaled) > 1:
                draw.line(scaled, fill=colour, width=max(1, round(width * scale)), joint="curve")
            for cx, cy in (scaled[0], scaled[-1]):
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour)
    for cx, cy, r in dots:
        draw.ellipse(((cx - r) * scale, (cy - r) * scale, (cx + r) * scale, (cy + r) * scale), fill=colour)
    return image


_OPENAI_PATH: str | None = None


def openai_path() -> str:
    global _OPENAI_PATH
    if _OPENAI_PATH is None:
        text = (HERE / "assets" / "openai.svg").read_text(encoding="utf-8")
        _OPENAI_PATH = re.search(r'<path d="([^"]+)"', text).group(1)
    return _OPENAI_PATH


CLAUDE_RAYS = ("M12 12V2.5M12 12v9.5M12 12L4.2 6.4M12 12l7.8 5.6M12 12L4.2 17.6M12 12l7.8-5.6M12 12L2.6 12M12 12h9.4"
               "M12 12L7.2 3.6M12 12l4.8 8.4M12 12L7.2 20.4M12 12l4.8-8.4")

ICON_PATHS = {
    "gauge": (["M4 19a9 9 0 1 1 16 0", "M12 15l4-5"], 2.0, ((12, 15, 1.4),)),
    "mic": (["M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0z", "M5 11a7 7 0 0 0 14 0M12 18v3"], 2.0, ()),
    "plus": (["M12 5v14M5 12h14"], 2.2, ()),
    "check": (["M5 12l5 5 9-10"], 3.2, ()),
    "arrow": (["M7 17L17 7M8 7h9v9"], 2.4, ()),
    "warn": (["M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0", "M12 7v6"], 2.2, ((12, 16.6, 1.2),)),
    "upload": (["M12 16V4M7 9l5-5 5 5M4 20h16"], 1.9, ()),
    "sliders": (["M4 7h9", "M17 7h3", "M4 12h3", "M11 12h9", "M4 17h11", "M19 17h1"], 1.9,
                ((15, 7, 2.3), (9, 12, 2.3), (17, 17, 2.3))),
}


def icon(name: str, color: str, px: int) -> Image.Image:
    """Hi-res (SS x px) icon by name."""
    if name == "openai":
        return svg_fill_icon(openai_path(), color, px)
    if name == "claude":
        return svg_stroke_icon([CLAUDE_RAYS], color, px, 2.6)
    if name == "stop":
        size = px * SS
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        m = size * 5 / 24
        ImageDraw.Draw(image).rounded_rectangle((m, m, size - m, size - m), radius=size * 3 / 24, fill=rgb(color))
        return image
    paths, width, dots = ICON_PATHS[name]
    return svg_stroke_icon(paths, color, px, width, dots=dots)


def letter_icon(letter: str, color: str, px: int) -> Image.Image:
    size = px * SS
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", int(size * 0.58))
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(image).text((size / 2, size / 2), letter.upper()[:1] or "?", font=font, fill=rgb(color), anchor="mm")
    return image


def icon_pil(name: str, color: str, px: int) -> Image.Image:
    return icon(name, color, px).resize((px, px), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------- composites

def circle_button(diameter: int, fill: str, border: str, glyph: Image.Image | None,
                  border_width: float = 1.0, glyph_px: int | None = None, dashed: bool = False) -> Image.Image:
    """Round button with an optional hi-res glyph; returns a diameter x diameter RGBA image."""
    size = diameter * SS
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    bw = round(border_width * SS)
    if dashed:
        if fill:
            draw.ellipse((0, 0, size - 1, size - 1), fill=rgb(fill))
        for k in range(0, 360, 24):
            draw.arc((bw / 2, bw / 2, size - 1 - bw / 2, size - 1 - bw / 2), k, k + 12, fill=rgb(border), width=bw)
    else:
        draw.ellipse((0, 0, size - 1, size - 1), fill=rgb(border))
        draw.ellipse((bw, bw, size - 1 - bw, size - 1 - bw), fill=rgb(fill))
    if glyph is not None:
        box = glyph.getchannel("A").getbbox()
        if box:  # centre the visible pixels, not the glyph's canvas
            gx = round((size - (box[0] + box[2])) / 2)
            gy = round((size - (box[1] + box[3])) / 2)
        else:
            gx, gy = (size - glyph.width) // 2, (size - glyph.height) // 2
        image.alpha_composite(glyph, (gx, gy))
    return image.resize((diameter, diameter), Image.Resampling.LANCZOS)


_XP_TITLE_STOPS = [(0, "#0058ee"), (4, "#3593ff"), (6, "#288eff"), (8, "#127dff"), (10, "#036ffc"), (14, "#0262ee"), (20, "#0057e5"),
                   (24, "#0054e3"), (56, "#0055eb"), (66, "#005bf5"), (76, "#026afe"), (86, "#0062ef"), (92, "#0052d6"), (94, "#0040ab"),
                   (100, "#003092")]


def vertical_gradient(width: int, height: int, stops: list[tuple[float, str]]) -> Image.Image:
    """RGBA image whose colour changes from top to bottom through `stops` ((percent, colour), ...)."""
    positions = [p for p, _ in stops]
    colours = np.array([rgb(c)[:3] for _, c in stops], dtype=float)
    ys = np.linspace(0, 100, max(height, 2))[:height]
    rows = np.stack([np.interp(ys, positions, colours[:, i]) for i in range(3)], axis=1)
    data = np.repeat(rows[:, None, :], width, axis=1)
    alpha = np.full((height, width, 1), 255, dtype=float)
    return Image.fromarray(np.concatenate([data, alpha], axis=2).astype(np.uint8), "RGBA")


def _windows_font(names: tuple[str, ...], px: int):
    from PIL import ImageFont
    folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in names:
        try:
            return ImageFont.truetype(str(folder / name), px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(px)
    except TypeError:
        return ImageFont.load_default()


def xp_titlebar(width: int, title: str, height: int = 28, radius: int = 5) -> Image.Image:
    """Luna title bar (blue gradient, small icon, bold white title with a shadow, red close button) at SS x scale."""
    w, h = width * SS, height * SS
    bar = vertical_gradient(w, h, _XP_TITLE_STOPS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h * 2), radius=radius * SS, fill=255)       # only the top corners are round
    bar.putalpha(mask)
    draw = ImageDraw.Draw(bar)
    icon, left = 16 * SS, 8 * SS
    top = (h - icon) // 2
    draw.rounded_rectangle((left, top, left + icon, top + icon), radius=3 * SS, fill=rgb("#2f6fe0"), outline=rgb("#ffffff"), width=SS)
    draw.rounded_rectangle((left + 6 * SS, top + 3 * SS, left + 10 * SS, top + 9 * SS), radius=2 * SS, fill=rgb("#ffffff"))
    draw.arc((left + 4 * SS, top + 4 * SS, left + 12 * SS, top + 12 * SS), 20, 160, fill=rgb("#ffffff"), width=SS)
    font = _windows_font(("trebucbd.ttf", "tahomabd.ttf", "arialbd.ttf"), 13 * SS)
    text_x = left + icon + 6 * SS
    draw.text((text_x + SS, h // 2 + SS), title, font=font, fill=rgb("#0f1089"), anchor="lm")
    draw.text((text_x, h // 2), title, font=font, fill=rgb("#ffffff"), anchor="lm")
    size = 21 * SS
    bx, by = w - size - 5 * SS, (h - size) // 2
    button = vertical_gradient(size, size, [(0, "#e9967e"), (55, "#d4512a"), (100, "#c2411c")])
    button_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(button_mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=3 * SS, fill=255)
    button.putalpha(button_mask)
    bar.alpha_composite(button, (bx, by))
    draw.rounded_rectangle((bx, by, bx + size - 1, by + size - 1), radius=3 * SS, outline=rgb("#ffffff"), width=SS)
    pad = 6 * SS
    for a, b in (((bx + pad, by + pad), (bx + size - pad, by + size - pad)), ((bx + size - pad, by + pad), (bx + pad, by + size - pad))):
        draw.line((a, b), fill=rgb("#ffffff"), width=round(2.4 * SS))
    return bar


def xp_button_image(width: int, height: int, state: str = "normal") -> Image.Image:
    """XP push button: white-to-beige gradient, dark blue border; states 'normal', 'default' (blue glow), 'hover' (orange), 'pressed'."""
    w, h = width * SS, height * SS
    stops = [(0, "#d6d0c0"), (100, "#d6d0c0")] if state == "pressed" else [(0, "#ffffff"), (86, "#ece9d8"), (100, "#d6d0c0")]
    face = vertical_gradient(w, h, stops)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=3 * SS, fill=255)
    face.putalpha(mask)
    draw = ImageDraw.Draw(face)
    if state in ("default", "hover"):
        glow = "#98b8f8" if state == "default" else "#fbd18b"
        draw.rounded_rectangle((SS, SS, w - 1 - SS, h - 1 - SS), radius=2 * SS, outline=rgb(glow), width=2 * SS)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=3 * SS, outline=rgb("#e68b2c" if state == "hover" else "#003c74"), width=SS)
    return face.resize((width, height), Image.Resampling.LANCZOS)


XP_FACE, XP_LIGHT, XP_SOFT, XP_SHADOW = "#ece9d8", "#ffffff", "#aca899", "#716f64"


def bevel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], raised: bool = True) -> None:
    """The two-pixel 3-D edge of Windows controls, drawn with 1 px lines: white and grey, swapped when sunken."""
    x0, y0, x1, y1 = box
    top_left, bottom_right = (XP_LIGHT, XP_SHADOW) if raised else (XP_SHADOW, XP_LIGHT)
    inner_tl, inner_br = ("#f4f2e8", XP_SOFT) if raised else (XP_SOFT, "#f4f2e8")
    draw.line((x0, y0, x1 - 1, y0), fill=top_left)
    draw.line((x0, y0, x0, y1 - 1), fill=top_left)
    draw.line((x0, y1, x1, y1), fill=bottom_right)
    draw.line((x1, y0, x1, y1), fill=bottom_right)
    draw.line((x0 + 1, y0 + 1, x1 - 2, y0 + 1), fill=inner_tl)
    draw.line((x0 + 1, y0 + 1, x0 + 1, y1 - 2), fill=inner_tl)
    draw.line((x0 + 1, y1 - 1, x1 - 1, y1 - 1), fill=inner_br)
    draw.line((x1 - 1, y0 + 1, x1 - 1, y1 - 1), fill=inner_br)


def tool_button(size: int, glyph: Image.Image | None, pressed: bool = False) -> Image.Image:
    """A MS Paint toolbox button: square, raised bevel; pressed = sunken on the checkered fill Paint uses for the chosen tool."""
    face = Image.new("RGBA", (size, size), rgb(XP_FACE))
    draw = ImageDraw.Draw(face)
    if pressed:
        for y in range(2, size - 2):
            for x in range(2, size - 2):
                if (x + y) % 2 == 0:
                    face.putpixel((x, y), rgb(XP_LIGHT))
    bevel(draw, (0, 0, size - 1, size - 1), raised=not pressed)
    big = face.resize((size * SS, size * SS), Image.Resampling.NEAREST)
    if glyph is not None:
        box = glyph.getchannel("A").getbbox()
        gx, gy = ((size * SS - (box[0] + box[2])) // 2, (size * SS - (box[1] + box[3])) // 2) if box else ((size * SS - glyph.width) // 2, (size * SS - glyph.height) // 2)
        shift = SS if pressed else 0                                   # a pressed button moves its picture one pixel
        big.alpha_composite(glyph, (gx + shift, gy + shift))
    return big.resize((size, size), Image.Resampling.BOX)           # box filter keeps the 1 px bevel and checker exactly


def toolbox_image(glyphs: list[Image.Image | None]) -> tuple[Image.Image, list[tuple[int, int]]]:
    """Vertical Paint-style toolbox: a raised panel holding square tool buttons; returns (image, [(y0, y1) per button])."""
    button, gap, pad = 44, 4, 6
    count = len(glyphs)
    width = button + 2 * pad + 2
    height = count * button + (count - 1) * gap + 2 * pad + 2
    image = Image.new("RGBA", (width, height), rgb(XP_FACE))
    bevel(ImageDraw.Draw(image), (0, 0, width - 1, height - 1))
    ranges = []
    for index, glyph in enumerate(glyphs):
        y = 1 + pad + index * (button + gap)
        image.alpha_composite(tool_button(button, glyph), ((width - button) // 2, y))
        ranges.append((y, y + button))
    return image, ranges


def toolbar_band(glyph: Image.Image | None, badges: list[Image.Image | None], toward_right: bool, rail_half: int) -> tuple[Image.Image, list[tuple[int, int]]]:
    """The horizontal toolbar that slides out of a toolbox button: flat where it meets the toolbox, raised edge elsewhere."""
    button, gap, pad, badge = FLYOUT_DISC, 8, 4, 18
    count = len(badges)
    width = button + gap + count * button + (count - 1) * gap + pad + 2
    height = FLYOUT_HEIGHT
    start = button // 2
    band = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(band)
    draw.rectangle((start, 0, width - 1, height - 1), fill=rgb(XP_FACE))
    edge = start + rail_half                       # the raised edge only begins where the toolbox ends
    tl, br, itl, ibr = XP_LIGHT, XP_SHADOW, "#f4f2e8", XP_SOFT
    draw.line((edge, 0, width - 2, 0), fill=tl)
    draw.line((edge, 1, width - 3, 1), fill=itl)
    draw.line((edge, height - 1, width - 1, height - 1), fill=br)
    draw.line((edge, height - 2, width - 2, height - 2), fill=ibr)
    draw.line((width - 1, 0, width - 1, height - 1), fill=br)
    draw.line((width - 2, 1, width - 2, height - 2), fill=ibr)
    if not toward_right:
        band = band.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    def at(x):
        return x if toward_right else width - x - button

    band.alpha_composite(tool_button(button, glyph), (at(0), pad))
    ranges = []
    for index, picture in enumerate(badges):
        x = at(button + gap + index * (button + gap))
        band.alpha_composite(tool_button(button, glyph), (x, pad))
        if picture is not None:
            corner = Image.new("RGBA", (badge, badge), rgb(XP_LIGHT))
            ImageDraw.Draw(corner).rectangle((0, 0, badge - 1, badge - 1), outline=rgb(XP_SHADOW))
            corner.alpha_composite(picture.resize((badge - 6, badge - 6), Image.Resampling.LANCZOS), (3, 3))
            band.alpha_composite(corner, (x + button - badge + 3, pad + button - badge + 3))
        ranges.append((x, x + button))
    return band, ranges


def xp_radio_image(selected: bool, size: int = 13) -> Image.Image:
    """A Luna radio button: white face, dark blue rim, and a green dot when chosen."""
    px = size * SS
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, px - 1, px - 1), fill=rgb("#1c5180"))
    face = vertical_gradient(px - 2 * SS, px - 2 * SS, [(0, "#dcdcd7"), (100, "#ffffff")])
    mask = Image.new("L", face.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, face.width - 1, face.height - 1), fill=255)
    face.putalpha(mask)
    image.alpha_composite(face, (SS, SS))
    if selected:
        dot = orb_image(5, "#9af09a", "#1f9d1f")
        image.alpha_composite(dot.resize((5 * SS, 5 * SS), Image.Resampling.LANCZOS), ((px - 5 * SS) // 2, (px - 5 * SS) // 2))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def orb_image(diameter: int, light: str, dark: str, glyph: Image.Image | None = None) -> Image.Image:
    """Glossy round button (the big blue or red XP orb): radial light-to-dark shading plus a soft highlight on the upper half."""
    size = diameter * SS
    yy, xx = np.mgrid[0:size, 0:size]
    distance = np.clip(np.hypot(xx - size * 0.36, yy - size * 0.30) / (size * 0.85), 0, 1)[..., None]
    shade = np.array(rgb(light)[:3], dtype=float) * (1 - distance) + np.array(rgb(dark)[:3], dtype=float) * distance
    orb = Image.fromarray(np.concatenate([shade, np.full((size, size, 1), 255.0)], axis=2).astype(np.uint8), "RGBA")
    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse((size * 0.14, size * 0.05, size * 0.86, size * 0.50), fill=(255, 255, 255, 85))
    orb.alpha_composite(gloss)
    circle = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle).ellipse((0, 0, size - 1, size - 1), fill=255)
    orb.putalpha(circle)
    rim = ImageDraw.Draw(orb)
    darker = tuple(round(c * 0.6) for c in rgb(dark)[:3])
    rim.ellipse((0, 0, size - 1, size - 1), outline=darker, width=round(1.5 * SS))
    if glyph is not None:
        box = glyph.getchannel("A").getbbox()
        gx, gy = ((size - (box[0] + box[2])) // 2, (size - (box[1] + box[3])) // 2) if box else ((size - glyph.width) // 2, (size - glyph.height) // 2)
        orb.alpha_composite(glyph, (gx, gy))
    return orb.resize((diameter, diameter), Image.Resampling.LANCZOS)


def panel_image(width: int, height: int, fill: str, border: str, radius: int = 16,
                tail: str | None = None, tail_x: int | None = None, tail_size: int = 7,
                frame: int = 1, square_bottom: bool = False, titlebar: Image.Image | None = None) -> tuple[Image.Image, int]:
    """Rounded panel with an optional 'up' or 'down' tail; returns (image, body_top_offset).
    `frame` is the border width in px; `titlebar` (from xp_titlebar, frame-wide) is drawn inside the frame at the top."""
    top = tail_size if tail == "up" else 0
    total_h = height + (tail_size if tail else 0)
    w, h = width * SS, total_h * SS
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    body = (0, top * SS, w - 1, (top + height) * SS - 1)
    corners = (True, True, not square_bottom, not square_bottom)
    draw.rounded_rectangle(body, radius=radius * SS, fill=rgb(border), corners=corners)
    inset = frame * SS
    draw.rounded_rectangle((body[0] + inset, body[1] + inset, body[2] - inset, body[3] - inset),
                           radius=max(1, (radius - frame) * SS), fill=rgb(fill), corners=corners)
    if titlebar is not None:
        image.alpha_composite(titlebar, (inset, body[1] + inset))
    if tail:
        cx = (tail_x if tail_x is not None else width // 2) * SS
        cx = max((radius + tail_size) * SS, min(w - (radius + tail_size) * SS, cx))
        t = tail_size * SS
        if tail == "down":
            edge = (top + height) * SS - 1
            outer = [(cx - t - inset, edge - inset), (cx + t + inset, edge - inset), (cx, edge + t)]
            inner = [(cx - t + inset, edge - 2 * inset), (cx + t - inset, edge - 2 * inset), (cx, edge + t - 2 * inset)]
        else:
            edge = top * SS
            outer = [(cx - t - inset, edge + inset), (cx + t + inset, edge + inset), (cx, edge - t)]
            inner = [(cx - t + inset, edge + 2 * inset), (cx + t - inset, edge + 2 * inset), (cx, edge - t + 2 * inset)]
        draw.polygon(outer, fill=rgb(border))
        draw.polygon(inner, fill=rgb(fill))
        # erase the border line where the tail meets the body
        if tail == "down":
            draw.rectangle((cx - t + 2 * inset, edge - 2 * inset, cx + t - 2 * inset, edge), fill=rgb(fill))
        else:
            draw.rectangle((cx - t + 2 * inset, edge, cx + t - 2 * inset, edge + 2 * inset), fill=rgb(fill))
    return image.resize((width, total_h), Image.Resampling.LANCZOS), top


def rounded_box(width: int, height: int, fill: str, border: str, radius: int = 12, outer: str | None = None) -> Image.Image:
    """Opaque-corner rounded card background (corners blended into `outer`)."""
    w, h = width * SS, height * SS
    image = Image.new("RGBA", (w, h), rgb(outer) if outer else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius * SS, fill=rgb(border))
    draw.rounded_rectangle((SS, SS, w - 1 - SS, h - 1 - SS), radius=max(1, (radius - 1) * SS), fill=rgb(fill))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def rail_image(theme: dict, glyphs: list[Image.Image | None], letters: list[str]) -> tuple[Image.Image, list[tuple[int, int]]]:
    """Vertical pill of 44 px round shortcut buttons; returns (image, [(y0, y1) per button])."""
    if theme.get("tools"):
        return toolbox_image(glyphs)
    button, gap, pad = 44, 8, 6
    count = len(glyphs)
    width = button + 2 * pad + 2
    height = count * button + (count - 1) * gap + 2 * pad + 2
    w, h = width * SS, height * SS
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = theme.get("radius_rail") or width // 2
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius * SS, fill=rgb(theme["frame"] if theme.get("titlebar") else theme["border"]))
    draw.rounded_rectangle((SS, SS, w - 1 - SS, h - 1 - SS), radius=(radius - 1) * SS, fill=rgb(theme["panel"]))
    pill = image.resize((width, height), Image.Resampling.LANCZOS)
    ranges = []
    for index, glyph in enumerate(glyphs):
        y = 1 + pad + index * (button + gap)
        is_add = letters[index] == "+"
        circle = circle_button(button, "" if is_add else theme["card"], theme["sec"] if is_add else theme["border"],
                               glyph, border_width=1.5 if is_add else 1, dashed=is_add)
        pill.alpha_composite(circle, ((width - button) // 2, y))
        ranges.append((y, y + button))
    return pill, ranges


FLYOUT_DISC = 44
FLYOUT_HEIGHT = FLYOUT_DISC + 8


def flyout_image(theme: dict, glyph: Image.Image | None, badges: list[Image.Image | None],
                 toward_right: bool = True, rail_half: int = 29) -> tuple[Image.Image, list[tuple[int, int]]]:
    """A band that grows out of a rail button. Its first disc is that very button, redrawn at the same place so the two
    merge; the band itself starts at the disc's centre and is a little lower than the rail, with a rounded far end.
    Each further button is the product logo with a small disc (bottom right) showing where it will open.
    Its border lines only begin where the rail ends (`rail_half` from the disc centre)."""
    if theme.get("tools"):
        return toolbar_band(glyph, badges, toward_right, rail_half)
    button, gap, badge, pad = FLYOUT_DISC, 8, 20, 4
    count = len(badges)
    height = FLYOUT_HEIGHT
    width = button + gap + count * button + (count - 1) * gap + pad + 2
    start = button // 2
    w, h = width * SS, height * SS
    line = theme["frame"] if theme.get("titlebar") else theme["border"]          # the band wears the rail's outline
    radius, edge = (height // 2) * SS, (start + height // 2) * SS
    over_rail = (start * SS, 0, (start + rail_half) * SS - 1, h - 1)
    outer, inner = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    od, idr = ImageDraw.Draw(outer), ImageDraw.Draw(inner)
    od.rounded_rectangle((start * SS, 0, w - 1, h - 1), radius=radius, fill=255)
    od.rectangle((start * SS, 0, edge, h - 1), fill=255)                                # the end next to the rail is square,
    idr.rounded_rectangle(((start + 1) * SS, SS, w - 1 - SS, h - 1 - SS), radius=radius - SS, fill=255)
    idr.rectangle((start * SS, SS, edge, h - 1 - SS), fill=255)                        # with no border line across it
    od.rectangle(over_rail, fill=255)
    idr.rectangle(over_rail, fill=255)                                                  # and none over the rail itself
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste(Image.new("RGBA", (w, h), rgb(line)), mask=outer)
    fill = Image.new("RGBA", (w, h), rgb(theme["panel"]))
    layer.paste(fill, mask=inner)
    band = layer.resize((width, height), Image.Resampling.LANCZOS)
    if not toward_right:
        band = band.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    def at(x):
        return x if toward_right else width - x - button

    band.alpha_composite(circle_button(button, theme["card"], theme["border"], glyph), (at(0), pad))
    ranges = []
    for index, picture in enumerate(badges):
        x = at(button + gap + index * (button + gap))
        band.alpha_composite(circle_button(button, theme["card"], theme["border"], glyph), (x, pad))
        if picture is not None:
            disc = circle_button(badge, theme["panel"], theme["border"], None)
            disc.alpha_composite(picture.resize((badge - 6, badge - 6), Image.Resampling.LANCZOS), (3, 3))
            band.alpha_composite(disc, (x + button - badge + 3, pad + button - badge + 3))
        ranges.append((x, x + button))
    return band, ranges


def harden_edges(image: Image.Image, edge_color: str, threshold: int = 96) -> Image.Image:
    """Make a colour-keyed window's edge crisp: half-transparent pixels would blend with the key colour (dark halo)."""
    data = np.array(image.convert("RGBA"))
    alpha = data[..., 3]
    edge = (alpha < 255) & (alpha >= threshold)
    data[edge, :3] = rgb(edge_color)[:3]
    data[..., 3] = np.where(alpha >= threshold, 255, 0)
    return Image.fromarray(data, "RGBA")
