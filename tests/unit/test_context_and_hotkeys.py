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


def test_standby_delays_only_accept_the_offered_choices(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"idle_shrink": 15, "idle_hide": 60}))
    data = settings.load()
    assert data["idle_shrink"] == 15 and data["idle_hide"] == 60
    (tmp_path / "settings.json").write_text(json.dumps({"idle_shrink": 7, "idle_hide": True}))
    data = settings.load()
    assert data["idle_shrink"] == 0 and data["idle_hide"] == 0                # unknown values and booleans mean "never"
    assert settings.DEFAULTS["idle_shrink"] == 0 and settings.DEFAULTS["idle_hide"] == 0      # off unless you turn it on


def test_the_vocabulary_becomes_a_short_prompt_in_the_dictation_language():
    import vocab
    assert vocab.parse_terms("commit, Push ;pull request\ncommit,  ,build") == ["commit", "Push", "pull request", "build"]
    prompt = vocab.build_prompt("commit, push", "", "fr")
    assert prompt.startswith("Dictée en français") and prompt.endswith("commit, push.")
    assert vocab.build_prompt("commit", "", "en").startswith("Dictation in English") and vocab.build_prompt("  ,; ", "", "fr") == ""
    long = vocab.build_prompt(", ".join(f"terme{i}" for i in range(500)), "", "fr")
    assert 0 < len(long) <= vocab.MAX_CHARS and long.endswith(".")           # cut to what Whisper can use


def test_the_prompt_reaches_every_engine(monkeypatch):
    import accel
    import numpy as np
    import transcribe
    seen = {}
    monkeypatch.setattr(accel.server, "transcribe", lambda audio, language, model, prompt="": seen.setdefault("gpu", prompt) or "ok")
    accel.transcribe_gpu(np.zeros(8000, dtype=np.float32), "fr", "turbo", "Dictée : commit.")
    assert seen["gpu"] == "Dictée : commit."

    class Reply:
        ok = True

        def json(self):
            return {"text": "ok"}

    def fake_post(url, **kwargs):
        seen["remote"] = kwargs["data"].get("prompt")
        return Reply()

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    monkeypatch.setattr(transcribe, "resolve", lambda config: {"base_url": "https://x.test/v1", "model": "m", "key": "k"})
    transcribe.transcribe_remote(np.zeros(8000, dtype=np.float32), "fr", {"engine": "openai", "prompt": "Dictée : commit."})
    assert seen["remote"] == "Dictée : commit."
    transcribe.transcribe_remote(np.zeros(8000, dtype=np.float32), "fr", {"engine": "openai"})
    assert seen["remote"] is None


def test_the_settings_keep_the_vocabulary_and_start_with_a_useful_list(tmp_path, monkeypatch):
    import vocab
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    assert settings.load()["vocabulary"] == vocab.DEFAULT_TERMS and "commit" in vocab.DEFAULT_TERMS
    (tmp_path / "settings.json").write_text(json.dumps({"vocabulary": "Paperclip, Codex"}))
    assert settings.load()["vocabulary"] == "Paperclip, Codex"
    (tmp_path / "settings.json").write_text(json.dumps({"vocabulary": ""}))
    assert settings.load()["vocabulary"] == ""                                # emptying the list turns the feature off


def test_the_rewrite_is_told_about_english_words_inside_the_sentence():
    import core
    for as_prompt in (False, True):
        text = core.build_rewrite_instruction("il faut un commis", "fr", "Fix it.", as_prompt)
        assert "keep them in English" in text and "commit" in text


def test_your_words_come_first_in_the_prompt_and_survive_the_length_limit():
    import vocab
    prompt = vocab.build_prompt("Paperclip, codex-mux", vocab.DEFAULT_TERMS, "fr")
    assert prompt.startswith("Dictée en français avec des termes anglais : Paperclip, codex-mux, commit")
    many_technical = ", ".join(f"terme{i}" for i in range(300))
    crowded = vocab.build_prompt("Paperclip", many_technical, "fr")
    assert "Paperclip" in crowded and len(crowded) <= vocab.MAX_CHARS                    # the long list is what gets cut
    assert vocab.merge("a, b", "B, c") == "a, b, c"


def test_a_project_folder_teaches_its_own_names(tmp_path):
    import vocab
    project = tmp_path / "mcp-async-hero"
    (project / "src" / "queue_worker").mkdir(parents=True)
    (project / "node_modules" / "left-pad").mkdir(parents=True)
    (project / "package.json").write_text(json.dumps({"name": "async-hero", "dependencies": {"@modelcontextprotocol/sdk": "1", "zod": "3"}}))
    (project / "requirements.txt").write_text("faster-whisper>=1.0\nrequests\n# comment\n")
    (project / "README.md").write_text("# Async Hero\nBlah\n## Why Frida helps\n")
    (project / "src" / "dispatcher.py").write_text("print('secret = hunter2')")
    (project / "src" / "utils.py").write_text("")
    (project / ".env").write_text("TOKEN=abc")
    found = vocab.harvest(project)
    lowered = [w.lower() for w in found]
    assert found[0] == "mcp-async-hero"                                           # the project's own name ranks first
    for expected in ("async-hero", "sdk", "zod", "faster-whisper", "requests", "frida", "dispatcher", "queue_worker"):
        assert expected in lowered, (expected, found)
    for unwanted in ("utils", "src", "left-pad", "hunter2", "abc", ".env"):
        assert unwanted not in lowered, unwanted                                  # generic names, skipped folders, file contents
    assert vocab.harvest(tmp_path / "missing") == []
