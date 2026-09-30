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


def test_every_visible_label_has_a_french_translation():
    labels = [text for item in styles.STYLES for text in item[1:3]] + ["Custom"]
    labels += [hint for _, _, hint in settings.WHISPER_MODELS]
    missing = [text for text in labels if text not in i18n.FR]
    assert not missing, missing
