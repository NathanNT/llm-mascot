import json

import i18n
import mascot_setup
import mascots
import settings
from PIL import Image


def frame():
    return Image.new("RGBA", (4, 4), (255, 0, 0, 255))


def test_sanitizing_drops_anything_unexpected():
    assert mascot_setup.sanitize_animations({"pack:A": {"hover": "Greeting", "dance": "x", "error": 5, "idle": ""}, "bad": 3, 7: {}}) == {"pack:A": {"hover": "Greeting"}}
    assert mascot_setup.sanitize_layout({"rover": {"top": [10.4, -9999], "bottom": [0, 0], "nope": [1, 1], "rail": "x", "x": True}}) == {"rover": {"top": [10, -600]}}
    assert mascot_setup.sanitize_layout("junk") == {} and mascot_setup.sanitize_animations(None) == {}


def test_choices_are_stored_per_mascot_and_empty_ones_disappear():
    prefs = {}
    mascot_setup.store(prefs, "pack:A", {"hover": "Wave"}, {"top": [5, 6]})
    mascot_setup.store(prefs, "rover", {}, {"rail": [-20, 0]})
    assert mascot_setup.overrides(prefs, "pack:A") == {"hover": "Wave"} and mascot_setup.offset(prefs, "pack:A", "top") == (5, 6)
    assert mascot_setup.offset(prefs, "pack:A", "rail") == (0, 0) and mascot_setup.offset(prefs, "rover", "rail") == (-20, 0)
    mascot_setup.store(prefs, "pack:A", {}, {})
    assert "pack:A" not in prefs["animations"] and "pack:A" not in prefs["layout"] and prefs["layout"]["rover"] == {"rail": [-20, 0]}


def test_settings_keep_the_choices_and_clean_a_damaged_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"animations": {"rover": {"hover": "jump"}}, "layout": {"rover": {"top": [3, 4]}}}))
    data = settings.load()
    assert data["animations"] == {"rover": {"hover": "jump"}} and data["layout"] == {"rover": {"top": [3, 4]}}
    (tmp_path / "settings.json").write_text(json.dumps({"animations": [1], "layout": {"rover": {"top": "far"}}}))
    data = settings.load()
    assert data["animations"] == {} and data["layout"] == {}


def test_a_chosen_animation_wins_over_the_automatic_one_and_a_missing_one_is_ignored():
    clips = {"idle": mascots.Clip([frame()], [100]), "wave": mascots.Clip([frame()], [100]), "jump": mascots.Clip([frame()], [100])}
    mascot = mascots.MascotSet(clips, {"idle": ["idle"], "hover": ["wave"]})
    assert mascot.find("hover") == "wave" and mascot.automatic("hover") == "wave"
    mascot.overrides = {"hover": "jump"}
    assert mascot.find("hover") == "jump" and mascot.automatic("hover") == "wave"
    mascot.overrides = {"hover": "gone"}                       # an animation that no longer exists falls back to automatic
    assert mascot.find("hover") == "wave"


def test_every_editor_label_has_a_french_translation():
    labels = [label for _, label in mascot_setup.EVENTS] + [label for _, label in mascot_setup.ICONS]
    assert [text for text in labels if text not in i18n.FR] == []
