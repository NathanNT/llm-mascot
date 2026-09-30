import json

import pytest

import settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "MASCOT_DIR", tmp_path / "mascots")


def test_defaults_when_no_file_exists():
    data = settings.load()
    assert data["mascot"] == "rover" and data["theme"] == "dark"
    assert data["language"] in ("fr", "en")
    assert [a["name"] for a in data["apps"]] == ["Claude", "ChatGPT"]
    assert data["quotas_url"] == "" and data["codex"]["rewrite"] == "auto"


def test_round_trip_and_atomic_write(tmp_path):
    data = settings.load()
    data["theme"] = "light"
    data["size"] = 96
    settings.save(data)
    assert not (tmp_path / "settings.tmp").exists()
    again = settings.load()
    assert again["theme"] == "light" and again["size"] == 96


def test_invalid_values_fall_back_to_defaults():
    settings.SETTINGS_FILE.write_text(json.dumps({"theme": "neon", "side": "up", "size": 5000, "ui_language": "de",
                                                  "apps": [{"name": "no target"}, "junk"]}))
    data = settings.load()
    assert data["theme"] == "dark" and data["side"] == "right"
    assert data["size"] == settings.SIZE_MAX and data["ui_language"] == "auto"
    assert data["apps"] == []


def test_broken_json_is_ignored():
    settings.SETTINGS_FILE.write_text("{ not json")
    assert settings.load()["theme"] == "dark"


def test_size_snaps_to_the_step():
    assert settings.clamp_size(101) == 104
    assert settings.clamp_size(1) == settings.SIZE_MIN


def test_app_list_is_capped():
    apps = [{"name": f"a{i}", "url": f"https://a{i}.example"} for i in range(20)]
    settings.SETTINGS_FILE.write_text(json.dumps({"apps": apps}))
    assert len(settings.load()["apps"]) == settings.MAX_APPS


def test_mascot_paths_never_leave_the_mascot_folder(tmp_path):
    settings.MASCOT_DIR.mkdir()
    (settings.MASCOT_DIR / "friend.png").write_bytes(b"x")
    assert settings.mascot_path("file:friend.png").name == "friend.png"
    assert settings.mascot_path("file:../settings.json") is None
    assert settings.pack_path("pack:../../etc") is None


def test_import_pack_requires_both_files(tmp_path):
    folder = tmp_path / "Pack"
    folder.mkdir()
    (folder / "agent.js").write_text("x")
    with pytest.raises(ValueError):
        settings.import_pack(str(folder))
    (folder / "map.png").write_bytes(b"x")
    assert settings.import_pack(str(folder)) == "pack:Pack"
    assert settings.pack_path("pack:Pack") is not None
