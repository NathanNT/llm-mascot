"""Draws the bundled default mascot (an original flat-style dog) as a 9-row sprite atlas.

Run:  python tools/make_default_mascot.py     ->  assets/mascot-default.png  (1536 x 1872)

Layout (8 columns of 192 x 208 px cells): idle, run right, run left, wave, jump, fail, wait, sniff, read.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

K = 4                      # supersampling
W, H = 192, 208

FUR, FUR_DARK, BELLY = "#f2a93b", "#d98a1f", "#ffe3a8"
EAR, NOSE, OUTLINE = "#b8691a", "#2a1a12", "#7a4712"
COLLAR, TAG, WHITE, TONGUE = "#e2453b", "#ffd34d", "#ffffff", "#ff7b8a"
PAGE, PAGE_EDGE, COVER = "#fff6dc", "#d8c79a", "#2f6fd0"


def S(*values):
    return tuple(v * K for v in values)


def layer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (W * K, H * K), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def ellipse(d, cx, cy, rx, ry, fill, outline=OUTLINE, width=2.2):
    d.ellipse(S(cx - rx, cy - ry, cx + rx, cy + ry), fill=fill, outline=outline, width=round(width * K))


def rotated_ellipse(image, cx, cy, rx, ry, angle, fill):
    tile = Image.new("RGBA", (W * K, H * K), (0, 0, 0, 0))
    ellipse(ImageDraw.Draw(tile), cx, cy, rx, ry, fill)
    return Image.alpha_composite(image, tile.rotate(angle, center=(cx * K, cy * K), resample=Image.Resampling.BICUBIC))


def thick_line(d, points, width, fill, outline=OUTLINE):
    scaled = [S(x, y) for x, y in points]
    d.line(scaled, fill=outline, width=round((width + 4.4) * K), joint="curve")
    for x, y in scaled[:1] + scaled[-1:]:
        r = (width + 4.4) * K / 2
        d.ellipse((x - r, y - r, x + r, y + r), fill=outline)
    d.line(scaled, fill=fill, width=round(width * K), joint="curve")
    for x, y in scaled[:1] + scaled[-1:]:
        r = width * K / 2
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def shadow(image, cx, cy, rx, ry, strength=70):
    tile = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(tile).ellipse(S(cx - rx, cy - ry, cx + rx, cy + ry), fill=(0, 0, 0, strength))
    return Image.alpha_composite(tile, image)


def front_dog(*, bob=0.0, tail=0.0, blink=False, wave=None, tilt=0.0, look=(0, 0), mouth=0.0, xeyes=False,
              squash=1.0, ears=0.0, lift=0.0, slump=0.0, book=None, shadow_scale=1.0):
    """A sitting dog seen from the front. Returns a W x H (supersampled) RGBA layer."""
    ground = 198
    body, d = layer()
    # tail
    thick_line(d, [(130, 178), (150, 170), (158 + tail, 148)], 9, FUR_DARK)
    # haunches and body
    ellipse(d, 66, 178, 22, 17, FUR)
    ellipse(d, 126, 178, 22, 17, FUR)
    ellipse(d, 96, 152, 39, 43, FUR)
    ellipse(d, 96, 163, 22, 30, BELLY, outline=BELLY)
    # front legs (the left one waves)
    if wave is None:
        thick_line(d, [(80, 160), (80, 190)], 14, FUR)
        ellipse(d, 80, 194, 12, 7, FUR)
    else:
        thick_line(d, [(76, 158), (56 - wave * 2, 130 - wave * 10)], 14, FUR)
        ellipse(d, 55 - wave * 2, 126 - wave * 10, 9, 8, FUR)
    thick_line(d, [(112, 160), (112, 190)], 14, FUR)
    ellipse(d, 112, 194, 12, 7, FUR)
    if book is not None:
        page_l = [S(58, 160), S(94, 154 + book), S(94, 192), S(58, 196)]
        page_r = [S(98, 154 + book), S(134, 160), S(134, 196), S(98, 192)]
        d.polygon([S(54, 158), S(96, 150 + book), S(138, 158), S(138, 200), S(96, 194), S(54, 200)], fill=COVER, outline=OUTLINE)
        d.polygon(page_l, fill=PAGE, outline=PAGE_EDGE)
        d.polygon(page_r, fill=PAGE, outline=PAGE_EDGE)
        for row in range(4):
            y = 166 + row * 6
            d.line(S(64, y, 88, y - 1 + book / 4), fill=PAGE_EDGE, width=K)
            d.line(S(104, y - 1 + book / 4, 128, y), fill=PAGE_EDGE, width=K)
        ellipse(d, 58, 168, 8, 6, FUR)
        ellipse(d, 134, 168, 8, 6, FUR)
    # squash/stretch around the ground line
    if squash != 1.0:
        scaled = body.resize((W * K, round(H * K * squash)), Image.Resampling.BICUBIC)
        body = Image.new("RGBA", (W * K, H * K), (0, 0, 0, 0))
        body.alpha_composite(scaled, (0, round(ground * K - ground * K * squash)))

    head, h = layer()
    neck_y = 122
    for side in (-1, 1):
        x = 96 + side * 31
        angle = side * (-14 - 18 * ears) - side * 6 * slump
        head = rotated_ellipse(head, x, 86 - 7 * ears, 13, 29 - 2 * ears, angle, EAR)
    h = ImageDraw.Draw(head)
    ellipse(h, 96, 88, 37, 35, FUR)
    ellipse(h, 96, 105, 23, 17, BELLY, outline=OUTLINE)
    ellipse(h, 96, 96, 8, 6, NOSE, outline=NOSE)
    h.ellipse(S(93, 93, 97, 96), fill="#6b5b53")
    # mouth
    h.line(S(96, 101, 96, 108), fill=OUTLINE, width=round(2.2 * K))
    if mouth > 0:
        ellipse(h, 96, 114 + mouth * 2, 8, 6 + 7 * mouth, TONGUE, outline=OUTLINE, width=1.6)
        h.arc(S(84, 98, 108, 118), 10, 170, fill=OUTLINE, width=round(2.2 * K))
    else:
        h.arc(S(86, 100, 96, 112), 0, 160, fill=OUTLINE, width=round(2.2 * K))
        h.arc(S(96, 100, 106, 112), 20, 180, fill=OUTLINE, width=round(2.2 * K))
    # eyes
    for x in (79, 113):
        if xeyes:
            h.line(S(x - 5, 77, x + 5, 87), fill=NOSE, width=round(2.6 * K))
            h.line(S(x - 5, 87, x + 5, 77), fill=NOSE, width=round(2.6 * K))
        elif blink:
            h.arc(S(x - 6, 78, x + 6, 88), 200, 340, fill=NOSE, width=round(2.6 * K))
        else:
            ellipse(h, x, 82, 8, 9, WHITE, width=1.8)
            ellipse(h, x + look[0], 83 + look[1], 4.2, 5, NOSE, outline=NOSE)
            h.ellipse(S(x + look[0] - 1, 80 + look[1], x + look[0] + 1.6, 82.6 + look[1]), fill=WHITE)
    # collar
    h.arc(S(70, 112, 122, 136), 10, 170, fill=OUTLINE, width=round(9.4 * K))
    h.arc(S(70, 112, 122, 136), 10, 170, fill=COLLAR, width=round(6 * K))
    h.ellipse(S(91, 130, 101, 140), fill=TAG, outline=OUTLINE, width=K)
    if tilt:
        head = head.rotate(tilt, center=S(96, neck_y), resample=Image.Resampling.BICUBIC)

    dog = Image.new("RGBA", (W * K, H * K), (0, 0, 0, 0))
    dog.alpha_composite(body)
    # the head sits lower when the dog slumps
    dog.alpha_composite(head, (0, round((bob + 34 * slump) * K)))
    dog = dog.transform(dog.size, Image.Transform.AFFINE, (1, 0, 0, 0, 1, -round(lift * K)), resample=Image.Resampling.BICUBIC)
    return shadow(dog, 96, 200, 42 * shadow_scale * (1 - min(abs(lift), 40) / 90), 7, round(60 * shadow_scale)) if shadow_scale else dog


def side_dog(phase, *, head_drop=0.0, tail=0.0, speed=1.0):
    """A dog running to the right."""
    image, d = layer()
    swing = math.sin(phase) * 26 * speed
    bob = -4 * abs(math.sin(phase)) * speed
    # far legs (darker), then body, then near legs
    for x, sign in ((64, 1), (122, -1)):
        thick_line(d, [(x, 132 + bob), (x + sign * swing * 0.5 - 4, 186)], 12, FUR_DARK)
    thick_line(d, [(46, 112 + bob), (22, 88 + tail + bob)], 9, FUR_DARK)
    ellipse(d, 92, 118 + bob, 54, 30, FUR)
    ellipse(d, 96, 132 + bob, 34, 13, BELLY, outline=BELLY)
    for x, sign in ((58, -1), (128, 1)):
        thick_line(d, [(x, 134 + bob), (x + sign * swing * 0.6, 186)], 13, FUR)
        ellipse(d, x + sign * swing * 0.6 + 4, 188, 11, 6, FUR)
    hx, hy = 146, 98 + bob + head_drop
    thick_line(d, [(122, 106 + bob), (hx - 6, hy + 8)], 26, FUR)
    ellipse(d, hx, hy, 27, 25, FUR)
    ellipse(d, hx + 22, hy + 12 + head_drop * 0.2, 19, 12, BELLY)
    ellipse(d, hx + 38, hy + 10 + head_drop * 0.25, 6.5, 5.5, NOSE, outline=NOSE)
    d.arc(S(hx + 14, hy + 12, hx + 36, hy + 30), 20, 140, fill=OUTLINE, width=round(2 * K))
    image = rotated_ellipse(image, hx - 12, hy + 6, 10, 23, -8, EAR)
    d = ImageDraw.Draw(image)
    ellipse(d, hx + 6, hy - 4, 7, 8, WHITE, width=1.8)
    ellipse(d, hx + 8, hy - 3, 3.6, 4.6, NOSE, outline=NOSE)
    d.arc(S(118, 100 + bob, 136, 126 + bob), 280, 80, fill=OUTLINE, width=round(9.4 * K))
    d.arc(S(118, 100 + bob, 136, 126 + bob), 280, 80, fill=COLLAR, width=round(6 * K))
    return shadow(image, 94, 196, 56, 6, 55)


def cell(image: Image.Image) -> Image.Image:
    return image.resize((W, H), Image.Resampling.LANCZOS)


def build() -> Image.Image:
    sheet = Image.new("RGBA", (8 * W, 9 * H), (0, 0, 0, 0))

    def put(row, col, frame, mirror=False):
        frame = cell(frame)
        sheet.alpha_composite(frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if mirror else frame, (col * W, row * H))

    # 0 idle: breathing, a blink and a tail wag
    for i in range(6):
        put(0, i, front_dog(bob=math.sin(i / 6 * 2 * math.pi) * 1.6, tail=math.sin(i / 6 * 4 * math.pi) * 6, blink=(i == 4)))
    # 1/2 run right and left
    for i in range(8):
        frame = side_dog(i / 8 * 2 * math.pi, tail=math.sin(i / 8 * 4 * math.pi) * 6)
        put(1, i, frame)
        put(2, i, frame, mirror=True)
    # 3 wave
    for i, w in enumerate((0.0, 1.0, -0.4, 1.0)):
        put(3, i, front_dog(wave=w, mouth=0.6, tail=(-1) ** i * 8, ears=0.3))
    # 4 jump: crouch, launch, apex, fall, land
    for i, (lift, squash, ears) in enumerate(((0, 0.88, 0), (-18, 1.05, 0.6), (-44, 1.08, 1.0), (-20, 1.03, 0.7), (0, 0.92, 0.2))):
        put(4, i, front_dog(lift=lift, squash=squash, ears=ears, mouth=0.8 if i in (1, 2, 3) else 0.2, tail=10, shadow_scale=1))
    # 5 fail: a slow slump, dizzy eyes, then recovery
    for i, slump in enumerate((0.0, 0.25, 0.5, 0.8, 1.0, 1.0, 0.6, 0.0)):
        put(5, i, front_dog(slump=slump, xeyes=0.3 < slump, squash=1 - 0.22 * slump, tilt=10 * slump, mouth=0.9 if slump > 0.7 else 0,
                            tail=-8 * slump))
    # 6 wait: head tilts side to side, ears perked
    for i, (tilt, look, blink) in enumerate(((0, (0, 0), False), (-12, (-2, 0), False), (-12, (-2, 0), False), (0, (0, 0), True),
                                              (12, (2, 0), False), (12, (2, 0), False))):
        put(6, i, front_dog(tilt=tilt, look=look, blink=blink, ears=1.0, tail=(-1) ** i * 5))
    # 7 sniff: nose to the ground, slow steps
    for i in range(6):
        put(7, i, side_dog(i / 6 * 2 * math.pi, head_drop=26, speed=0.45, tail=math.sin(i / 6 * 4 * math.pi) * 6))
    # 8 read: holding a book, eyes moving along the lines
    for i, (look, blink) in enumerate((((-2, 3), False), ((0, 3), False), ((2, 3), False), ((2, 3), True), ((0, 3), False), ((-2, 3), False))):
        put(8, i, front_dog(look=look, blink=blink, book=-2 + (i % 2), bob=1.5, tail=(-1) ** i * 4, tilt=-3))
    return sheet


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    atlas = build()
    (root / "assets").mkdir(exist_ok=True)
    atlas.save(root / "assets" / "mascot-default.png", optimize=True)
    icon_cell = atlas.crop((0, 0, W, H)).resize((240, 260), Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (260, 260), (0, 0, 0, 0))
    square.alpha_composite(icon_cell, (10, 0))
    square.save(root / "assets" / "mascot.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("written", root / "assets" / "mascot-default.png")
