import dictation_context
import hotkeys
import i18n
import settings
import json


def test_only_recent_dictations_are_kept_oldest_first():
    memory = dictation_context.RecentDictations(limit=3, max_age=100)
    for stamp, text in enumerate(["one", "two", "three", "four"]):
        memory.add(text, now=float(stamp))
    assert memory.recent(now=10.0) == ["two", "three", "four"]                  # the limit drops the oldest
    assert memory.recent(now=102.5) == ["four"]                                 # and old ones expire
    memory.clear()
    assert memory.recent(now=0.0) == []


def test_long_and_blank_dictations_are_trimmed_or_ignored():
    memory = dictation_context.RecentDictations()
    memory.add("   \n ")
    memory.add("a  b\n c" + "x" * 2000)
    assert len(memory.recent()) == 1 and len(memory.recent()[0]) == dictation_context.MAX_CHARS and memory.recent()[0].startswith("a b c")


def test_shortcuts_are_parsed_normalised_and_shown():
    assert hotkeys.parse("ctrl+alt+r") == (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("R"))
    assert hotkeys.normalize("Alt + Ctrl + R") == "ctrl+alt+r" and hotkeys.label("alt+ctrl+r") == "Ctrl+Alt+R"
    assert hotkeys.parse("ctrl+shift+f12") == (hotkeys.MOD_CONTROL | hotkeys.MOD_SHIFT, 0x70 + 11)
    assert hotkeys.parts("win+space") == ["Win", "Space"] and hotkeys.parse("f9") == (0, 0x78)
    for unusable in ("r", "shift+r", "ctrl+alt", "ctrl+tab", "ctrl+f25", "", "ctrl+alt+rr"):
        assert hotkeys.parse(unusable) is None, unusable


def test_typing_a_shortcut_ignores_lone_modifiers_and_unusable_keys():
    assert hotkeys.from_keypress({"ctrl", "alt"}, "R") == "ctrl+alt+r"
    assert hotkeys.from_keypress({"ctrl"}, "F5") == "ctrl+f5"
    assert hotkeys.from_keypress({"ctrl"}, "Alt_L") is None and hotkeys.from_keypress({"ctrl"}, "Return") is None
    assert hotkeys.from_keypress(set(), "r") is None                               # a plain letter is not a shortcut


def test_settings_validate_the_new_options(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"hotkey": "Alt+Ctrl+K", "keep_context": True, "live_transcript": False}))
    data = settings.load()
    assert data["hotkey"] == "alt+ctrl+k" and data["keep_context"] is True and data["live_transcript"] is False
    (tmp_path / "settings.json").write_text(json.dumps({"hotkey": "k", "keep_context": "yes"}))
    data = settings.load()
    assert data["hotkey"] == "ctrl+alt+r" and data["keep_context"] is False and data["live_transcript"] is True
