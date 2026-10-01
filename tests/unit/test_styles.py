import i18n
import settings
import styles


def test_presets_have_unique_ids_and_real_instructions():
    ids = [item[0] for item in styles.STYLES]
    assert len(ids) == len(set(ids)) and styles.DEFAULT_STYLE in ids and styles.CUSTOM not in ids
    assert all(len(item[3]) > 60 for item in styles.STYLES)


def test_instruction_lookup():
    assert styles.instruction("proofread").startswith("Only correct spelling")
    assert styles.instruction("custom", "  My own rule  ") == "My own rule"
    assert styles.instruction("custom", "") == styles.instruction(styles.DEFAULT_STYLE)
    assert styles.instruction("nonsense") == styles.instruction(styles.DEFAULT_STYLE)


def test_there_are_eight_presets_and_the_first_is_the_main_one():
    ids = [item[0] for item in styles.STYLES]
    assert len(ids) == 8 and ids[0] == styles.DEFAULT_STYLE == "clear"
    assert "faithful" not in ids and "prompt" not in ids


def test_the_main_style_is_short_and_asks_for_a_clear_corrected_prompt():
    main = styles.instruction("clear")
    assert len(main) < 400                                   # no wall of rules: the frame and the model do the rest
    for wanted in ("clear, corrected prompt", "syntax and grammar", "hesitations", "what the answer must contain", "clearly implies"):
        assert wanted in main, wanted
    assert styles.writes_prompt("clear") and not styles.writes_prompt("concise")


def test_retired_presets_fold_into_the_main_style(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    for old in ("faithful", "prompt"):
        (tmp_path / "settings.json").write_text(json.dumps({"rewrite_style": old}))
        assert settings.load()["rewrite_style"] == "clear"
    (tmp_path / "settings.json").write_text(json.dumps({"rewrite_style": "coding"}))
    assert settings.load()["rewrite_style"] == "coding"


def test_the_coding_style_follows_the_published_prompting_advice():
    coding = styles.instruction("coding").lower()
    for advice in ("goal", "where", "done when", "exactly", "never invent"):
        assert advice in coding, advice


def test_every_visible_label_has_a_french_translation():
    labels = [text for item in styles.STYLES for text in item[1:3]] + ["Custom"]
    labels += [hint for _, _, hint in settings.WHISPER_MODELS]
    missing = [text for text in labels if text not in i18n.FR]
    assert not missing, missing
