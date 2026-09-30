"""Theme tokens and anti-aliased drawing helpers (Pillow) for Rover's panels and icons."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SS = 4  # supersampling factor

TRANSPARENT = "#010203"  # colour key of the borderless windows

THEMES = {
    "dark": dict(desk="#111111", panel="#1c1c1e", card="#262628", border="#333336", text="#f2f2f0",
                 sec="#a3a3a8", accent="#f0b04a", on_accent="#1a1200", bar="#f0b04a", track="#3a3a3e",
                 rec="#ff6a5c", ok="#5ccf98", on_ok="#0d2016", err="#ff7d72", link="#f0b04a", kbd="#2e2e31"),
    "light": dict(desk="#efe7da", panel="#fffcf6", card="#f6efe3", border="#e4d8c4", text="#2b2620",
                  sec="#675e53", accent="#8f5200", on_accent="#ffffff", bar="#b0620a", track="#e6dac6",
                  rec="#c4372b", ok="#1f7a4d", on_ok="#ffffff", err="#b3261e", link="#8f5200", kbd="#efe5d3"),
}

CLAUDE_COLOR = "#D97757"

FONT_REGULAR = "Segoe UI"
FONT_SEMIBOLD = "Segoe UI Semibold"
FONT_MONO = "Consolas"


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


def panel_image(width: int, height: int, fill: str, border: str, radius: int = 16,
                tail: str | None = None, tail_x: int | None = None, tail_size: int = 7) -> tuple[Image.Image, int]:
    """Rounded panel with an optional 'up' or 'down' tail; returns (image, body_top_offset)."""
    top = tail_size if tail == "up" else 0
    total_h = height + (tail_size if tail else 0)
    w, h = width * SS, total_h * SS
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    body = (0, top * SS, w - 1, (top + height) * SS - 1)
    draw.rounded_rectangle(body, radius=radius * SS, fill=rgb(border))
    inset = SS
    draw.rounded_rectangle((body[0] + inset, body[1] + inset, body[2] - inset, body[3] - inset),
                           radius=max(1, (radius - 1) * SS), fill=rgb(fill))
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
    button, gap, pad = 44, 8, 6
    count = len(glyphs)
    width = button + 2 * pad + 2
    height = count * button + (count - 1) * gap + 2 * pad + 2
    w, h = width * SS, height * SS
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=(width // 2) * SS, fill=rgb(theme["border"]))
    draw.rounded_rectangle((SS, SS, w - 1 - SS, h - 1 - SS), radius=(width // 2 - 1) * SS, fill=rgb(theme["panel"]))
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


def harden_edges(image: Image.Image, edge_color: str, threshold: int = 96) -> Image.Image:
    """Make a colour-keyed window's edge crisp: half-transparent pixels would blend with the key colour (dark halo)."""
    data = np.array(image.convert("RGBA"))
    alpha = data[..., 3]
    edge = (alpha < 255) & (alpha >= threshold)
    data[edge, :3] = rgb(edge_color)[:3]
    data[..., 3] = np.where(alpha >= threshold, 255, 0)
    return Image.fromarray(data, "RGBA")
