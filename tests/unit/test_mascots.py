import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import mascots

ROOT = Path(__file__).resolve().parents[2]


def make_pack(folder: Path) -> Path:
    folder.mkdir(parents=True)
    sheet = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    for index, colour in enumerate(("#d33", "#3d3")):
        ImageDraw.Draw(sheet).ellipse((index * 100 + 20, 20, index * 100 + 80, 80), fill=colour)
    sheet.save(folder / "map.png")

    def frame(x, **extra):
        return {"duration": 80, "images": [[x, 0]], **extra}

    animations = {
        "RestPose": {"frames": [frame(0)]},
        "Idle1_1": {"frames": [frame(0), frame(100)]},
        "Greeting": {"frames": [frame(100), frame(0)]},
        "Processing": {"frames": [frame(0), frame(100, branching={"branches": [{"frameIndex": 0, "weight": 100}]})]},
        "Alert": {"frames": [frame(0)]},
    }
    (folder / "agent.js").write_text("clippy.ready('T', " + json.dumps({"framesize": [100, 100], "animations": animations}) + ");",
                                     encoding="utf-8")
    return folder


def test_bundled_atlas_has_every_reaction():
    mascot = mascots.rover_set(ROOT / "assets" / "mascot-default.png", 144, 156)
    assert set(mascot.clips) == set(mascots.ATLAS_CLIPS)
    for event in ("idle", "hover", "record", "process", "success", "error", "drag_left", "drag_right"):
        assert mascot.find(event), event
    mascot.crop_to_content()
    sizes = {frame.size for clip in mascot.clips.values() for frame in clip.frames}
    assert len(sizes) == 1, "all frames must share one size after cropping"
    left, top, right, bottom = mascot.idle_box
    assert 0 <= left < right <= mascot.size[0] and 0 <= top < bottom <= mascot.size[1]


def test_bundled_atlas_is_a_full_atlas():
    assert mascots.is_atlas(Image.open(ROOT / "assets" / "mascot-default.png"))


def test_pack_loader_maps_events_and_variants(tmp_path):
    mascot = mascots.pack_set(make_pack(tmp_path / "T"), 144, 156)
    assert mascot.find("hover") == "Greeting" and mascot.find("process") == "Processing"
    assert mascot.find("error") == "Alert" and mascot.find("success") is None
    assert mascot.variants == ["Idle1_1"]
    assert len(mascot.idle.frames) == 1
    assert mascot.clips["Greeting"].frames[0].size == mascot.clips["Greeting"].frames[1].size


def test_branching_can_loop_forever(tmp_path):
    clip = mascots.pack_set(make_pack(tmp_path / "T"), 144, 156).clips["Processing"]
    assert clip.next_index(0) == 1
    assert clip.next_index(1) == 0


def test_pack_preview(tmp_path):
    preview = mascots.pack_preview(make_pack(tmp_path / "T"), (44, 48))
    assert preview is not None and preview.size == (44, 48)
    assert mascots.pack_preview(tmp_path / "missing", (44, 48)) is None


def test_broken_pack_raises_value_error(tmp_path):
    folder = make_pack(tmp_path / "T")
    (folder / "agent.js").write_text("not json at all")
    with pytest.raises(ValueError):
        mascots.pack_set(folder, 144, 156)


def test_static_and_dot_sets():
    dot = mascots.dot_set(30, (240, 176, 74, 255))
    dot.crop_to_content()
    assert dot.size == (30, 30) and dot.find("hover") is None and dot.find("idle") == "idle"


def test_codex_pet_atlases_with_eleven_rows_are_accepted(tmp_path):
    sheet = Image.new("RGBA", (mascots.ATLAS_COLS * mascots.CELL_W, 11 * mascots.CELL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for row in range(11):
        for column in range(7 if row == 0 else 6):        # idle rows can be longer than six frames
            x, y = column * mascots.CELL_W, row * mascots.CELL_H
            draw.ellipse((x + 60, y + 60, x + 130, y + 150), fill="#c83")
    path = tmp_path / "pet.png"
    sheet.save(path)
    assert mascots.is_atlas(Image.open(path))
    mascot = mascots.rover_set(path, 96, 104)
    assert len(mascot.clips["idle"].frames) == 7 and mascot.find("hover") and mascot.find("error")
    with pytest.raises(ValueError):
        Image.new("RGBA", (100, 100)).save(tmp_path / "small.png")
        mascots.rover_set(tmp_path / "small.png", 96, 104)
