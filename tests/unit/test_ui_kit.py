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
    assert ui_kit.THEMES["dark"].keys() == ui_kit.THEMES["light"].keys() == ui_kit.THEMES["xp"].keys()


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


def test_every_theme_has_the_same_tokens_and_xp_is_selectable():
    assert set(ui_kit.THEMES) == {"dark", "light", "xp"}
    assert ui_kit.THEMES["dark"].keys() == ui_kit.THEMES["xp"].keys()


def test_fonts_follow_the_active_theme():
    ui_kit.set_active("xp")
    try:
        assert ui_kit.rg(9) == ("Tahoma", 8) and ui_kit.rg(10) == ("Tahoma", 9) and ui_kit.rg(12) == ("Tahoma", 12)   # XP draws controls in 8 pt
        assert ui_kit.sb(12) == ("Tahoma", 12, "bold") and ui_kit.sb(9) == ("Tahoma", 8)      # small controls stay regular in XP
    finally:
        ui_kit.set_active("dark")
    assert ui_kit.sb(9) == ("Segoe UI Semibold", 9) and ui_kit.rg(9, "bold") == ("Segoe UI", 9, "bold")


def test_xp_drawing_helpers_have_the_expected_geometry():
    bar = ui_kit.xp_titlebar(200, "Dictée", 28)
    assert bar.size == (200 * ui_kit.SS, 28 * ui_kit.SS)
    assert bar.getpixel((0, 0))[3] == 0 and bar.getpixel((100 * ui_kit.SS, 14 * ui_kit.SS))[3] == 255      # round top corners, opaque body
    normal, hover = ui_kit.xp_button_image(80, 25, "normal"), ui_kit.xp_button_image(80, 25, "hover")
    assert normal.size == (80, 25) and normal.tobytes() != hover.tobytes()
    panel, top = ui_kit.panel_image(200, 120, "#ece9d8", "#0054e3", 8, frame=3, square_bottom=True,
                                    titlebar=ui_kit.xp_titlebar(194, "x", 28))
    assert panel.size == (200, 120) and top == 0
    assert all(abs(a - b) <= 3 for a, b in zip(panel.getpixel((100, 119))[:3], ui_kit.rgb("#0054e3")[:3]))      # 3 px blue frame along the flat bottom


def test_paint_toolbox_buttons_are_bevelled_and_the_toolbox_keeps_the_rail_geometry():
    raised, sunken = ui_kit.tool_button(44, None), ui_kit.tool_button(44, None, pressed=True)
    assert raised.getpixel((0, 0))[:3] == ui_kit.rgb("#ffffff")[:3] and raised.getpixel((43, 43))[:3] == ui_kit.rgb("#716f64")[:3]
    assert sunken.getpixel((0, 0))[:3] == ui_kit.rgb("#716f64")[:3]                     # light comes from the top left
    assert {sunken.getpixel((x, 10))[:3] for x in (10, 11)} == {ui_kit.rgb("#ffffff")[:3], ui_kit.rgb("#ece9d8")[:3]}   # checkered fill
    theme = ui_kit.THEMES["xp"]
    glyphs = [ui_kit.icon("plus", "#000000", 20)] * 3
    image, ranges = ui_kit.rail_image(theme, glyphs, ["a", "b", "+"])
    assert image.width == 58 and all(high - low == 44 for low, high in ranges) and ranges[0][1] < ranges[1][0]
    band, reach = ui_kit.flyout_image(theme, glyphs[0], [None, None], True)
    assert band.height == ui_kit.FLYOUT_HEIGHT and len(reach) == 2 and all(high - low == 44 for low, high in reach)
    mirrored, reach_left = ui_kit.flyout_image(theme, glyphs[0], [None, None], False)
    assert reach_left[0][0] > reach_left[1][0] and mirrored.size == band.size


def test_xp_text_fields_are_white_with_the_blue_grey_rim_and_radios_show_their_state():
    xp = ui_kit.THEMES["xp"]
    assert xp["field"] == "#ffffff" and xp["field_border"] == "#7f9db9"
    assert ui_kit.THEMES["dark"]["field"] == ui_kit.THEMES["dark"]["panel"]
    off, on = ui_kit.xp_radio_image(False), ui_kit.xp_radio_image(True)
    assert off.size == on.size == (13, 13) and off.tobytes() != on.tobytes()
    centre = on.getpixel((6, 6))
    assert centre[1] > centre[0] and centre[1] > centre[2]                # the chosen radio has a green dot
    assert off.getpixel((0, 0))[3] == 0                                    # round, not square
