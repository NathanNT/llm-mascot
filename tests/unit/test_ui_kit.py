import numpy as np

import ui_kit


def test_every_named_icon_renders_with_pixels():
    for name in list(ui_kit.ICON_PATHS) + ["openai", "claude", "stop"]:
        image = ui_kit.icon_pil(name, "#ffffff", 24)
        assert image.size == (24, 24)
        assert image.getchannel("A").getbbox() is not None, name


def test_svg_path_arcs_and_relative_moves():
    (points, closed), = ui_kit.flatten_path("M4 19a9 9 0 1 1 16 0")
    assert not closed and len(points) > 10
    xs = [p[0] for p in points]
    assert 2.9 <= min(xs) < 4 and 20 < max(xs) <= 21.1     # a large arc bulges past its end points
    (square, closed), = ui_kit.flatten_path("M0 0h10v10h-10z")
    assert closed and square[:4] == [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_circle_button_centres_its_glyph():
    glyph = ui_kit.icon("gauge", "#ffffff", 22)
    button = ui_kit.circle_button(44, "#000000", "#444444", glyph)
    alpha = np.asarray(button.getchannel("A"))
    assert alpha[22, 22] == 255 and alpha[0, 0] == 0          # opaque centre, transparent corner


def test_panel_image_has_room_for_its_tail():
    flat, top = ui_kit.panel_image(200, 100, "#111111", "#333333")
    tailed, top_up = ui_kit.panel_image(200, 100, "#111111", "#333333", tail="up")
    assert flat.size == (200, 100) and tailed.size == (200, 107) and top == 0 and top_up == 7


def test_harden_edges_leaves_no_half_transparent_pixels():
    panel, _ = ui_kit.panel_image(120, 60, "#1c1c1e", "#333336", tail="down")
    hard = ui_kit.harden_edges(panel, "#333336")
    alpha = np.unique(np.asarray(hard.getchannel("A")))
    assert set(alpha.tolist()) <= {0, 255}


def test_rail_image_reports_one_hit_area_per_button():
    theme = ui_kit.THEMES["dark"]
    glyphs = [ui_kit.icon("plus", "#ffffff", 20)] * 3
    image, ranges = ui_kit.rail_image(theme, glyphs, ["a", "b", "+"])
    assert len(ranges) == 3 and all(high - low == 44 for low, high in ranges)
    assert ranges[0][1] < ranges[1][0] < ranges[2][0]
    assert image.height > ranges[-1][1]


def test_themes_have_the_same_tokens():
    assert ui_kit.THEMES["dark"].keys() == ui_kit.THEMES["light"].keys()


def test_text_contrast_meets_wcag_aa():
    def luminance(color):
        channels = [c / 255 for c in ui_kit.rgb(color)[:3]]
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    def ratio(a, b):
        high, low = sorted((luminance(a), luminance(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    for name, theme in ui_kit.THEMES.items():
        for foreground in ("text", "sec", "accent", "rec", "ok", "err"):
            assert ratio(theme[foreground], theme["panel"]) >= 4.5, (name, foreground)
        assert ratio(theme["on_accent"], theme["accent"]) >= 4.5, name
