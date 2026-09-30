import ast
from pathlib import Path

import i18n

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ["rover.py", "core.py", "voice.py", "settings.py", "appicons.py", "transcribe.py", "benchmark.py", "accel.py"]


def literals_passed_to_tr():
    found = set()
    for name in SOURCES:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
    return found


def test_every_translatable_string_has_a_french_translation():
    missing = sorted(text for text in literals_passed_to_tr() if text not in i18n.FR)
    assert not missing, f"missing French translations: {missing}"


def test_size_preset_labels_are_translated():
    import settings
    assert all(label in i18n.FR for label, _ in settings.SIZE_PRESETS)


def test_translation_switches_with_the_language():
    try:
        i18n.set_language("fr")
        assert i18n.tr("Cancel") == "Annuler"
        i18n.set_language("en")
        assert i18n.tr("Cancel") == "Cancel"
        assert i18n.tr("not a known key") == "not a known key"
    finally:
        i18n.set_language("en")


def test_placeholders_survive_translation():
    import re
    for english, french in i18n.FR.items():
        assert sorted(re.findall(r"\{\w+\}", english)) == sorted(re.findall(r"\{\w+\}", french)), english
