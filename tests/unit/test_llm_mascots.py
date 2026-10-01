import importlib.util
import io
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("get_llm_mascots", ROOT / "tools" / "get_llm_mascots.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_the_catalog_is_well_formed_and_every_name_is_a_distinct_file():
    names = [name for name, *_ in tool.CATALOG.values()]
    assert len(names) == len(set(names)) and len(names) >= 12
    for name, source, pet, about in tool.CATALOG.values():
        assert source in ("gallery", "petdex") and pet and about and "/" not in name and "\\" not in name


def test_only_the_two_galleries_are_ever_fetched():
    for url in ("https://raw.githubusercontent.com/legeling/awesome-codex-pet/main/pets/x/spritesheet.webp",
                "https://assets.petdex.dev/pets/x/sprite.webp", "https://petdex.dev/api/manifest"):
        assert tool.trusted(url) == url
    for url in ("http://assets.petdex.dev/x.webp", "https://evil.example/x.webp", "https://assets.petdex.dev.evil.example/x.webp",
                "file:///C:/secret.txt", "https://user@evil.example/x"):
        with pytest.raises(SystemExit):
            tool.trusted(url)


def test_installing_writes_one_credit_line_per_mascot_however_often_it_runs(tmp_path, monkeypatch):
    sheet = io.BytesIO()
    Image.new("RGBA", (1536, 1872), (0, 0, 0, 0)).save(sheet, "WEBP")
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "source_of", lambda source, pet: ("https://assets.petdex.dev/pets/x/sprite.webp", {"author": "someone", "license": None}))
    monkeypatch.setattr(tool, "fetch", lambda url: sheet.getvalue())
    for _ in range(2):
        tool.install("llama")
    credits = (tmp_path / "mascots" / "CREDITS.txt").read_text(encoding="utf-8")
    assert credits.count("Llama:") == 1 and "someone" in credits and "personal use only" in credits
    assert (tmp_path / "mascots" / "Llama.webp").is_file()


def test_a_sheet_of_the_wrong_size_is_refused(tmp_path, monkeypatch):
    sheet = io.BytesIO()
    Image.new("RGBA", (500, 500)).save(sheet, "WEBP")
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "source_of", lambda source, pet: ("https://assets.petdex.dev/x.webp", {"author": None, "license": None}))
    monkeypatch.setattr(tool, "fetch", lambda url: sheet.getvalue())
    with pytest.raises(SystemExit):
        tool.install("llama")
    assert not (tmp_path / "mascots").exists()
