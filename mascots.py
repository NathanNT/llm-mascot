"""Animated mascots: Rover's sprite atlas, clippy.js-style character packs (Merlin, Clippit…), images and GIFs."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageSequence

ATLAS_COLS = 8
CELL_W, CELL_H = 192, 208
# clip name -> (row in the atlas, frame count, milliseconds per frame)
ATLAS_CLIPS = {
    "idle": (0, 6, 220), "run_right": (1, 8, 90), "run_left": (2, 8, 90), "wave": (3, 4, 150),
    "jump": (4, 5, 130), "fail": (5, 8, 140), "wait": (6, 6, 200), "run": (7, 6, 100), "review": (8, 6, 190),
}
# event -> clips to try, in order
ATLAS_EVENTS = {
    "idle": ["idle"], "hover": ["wave"], "record": ["wait"], "process": ["review", "run"], "success": ["jump"],
    "error": ["fail"], "drag_right": ["run_right"], "drag_left": ["run_left"],
}
PACK_EVENTS = {
    "idle": ["idle"],
    "hover": ["Greeting", "Greet", "Wave", "GetAttention", "Acknowledge", "Show"],
    "record": ["StartListening", "Hearing_1", "Hearing_2", "Hearing", "Listening", "GestureUp", "LookUp"],
    "process": ["Processing", "Process", "Thinking", "Think", "Searching", "Search", "Reading", "Read", "Writing", "Write",
                "CheckingSomething", "GetTechy"],
    "success": ["Congratulate", "Congratulate_2", "Pleased", "Acknowledge", "GetArtsy", "DoMagic1", "Wave"],
    "error": ["Alert", "Sad", "Confused", "DontRecognize", "Uncertain", "Surprised"],
    "drag_right": ["LookRight", "GestureRight", "MoveRight"],
    "drag_left": ["LookLeft", "GestureLeft", "MoveLeft"],
}


@dataclass
class Clip:
    frames: list[Image.Image]
    delays: list[int]
    branching: list | None = None      # per frame: [(target frame, weight), ...] or None

    def next_index(self, index: int) -> int:
        if self.branching and self.branching[index]:
            targets, weights = zip(*self.branching[index])
            return random.choices(targets, weights=weights)[0]
        return index + 1


@dataclass
class MascotSet:
    clips: dict[str, Clip]
    events: dict[str, list[str]]
    variants: list[str] = field(default_factory=list)   # occasional idle animations (packs)
    size: tuple[int, int] = (1, 1)
    idle_box: tuple[int, int, int, int] = (0, 0, 1, 1)  # where the resting mascot sits inside `size`

    @property
    def idle(self) -> Clip:
        return self.clips["idle"]

    def find(self, event: str) -> str | None:
        for name in self.events.get(event, []):
            if name in self.clips and self.clips[name].frames:
                return name
        return None

    def crop_to_content(self) -> None:
        """Crop every frame of every clip to the same box (the union of all visible pixels)."""
        def union(frames):
            boxes = [b for b in (frame.getchannel("A").getbbox() for frame in frames) if b]
            if not boxes:
                return None
            return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

        every = [frame for clip in self.clips.values() for frame in clip.frames]
        box = union(every)
        if box is None:
            self.size = every[0].size
            self.idle_box = (0, 0, *self.size)
            return
        idle_box = union(self.idle.frames) or box
        for clip in self.clips.values():
            clip.frames = [frame.crop(box) for frame in clip.frames]
        self.size = (box[2] - box[0], box[3] - box[1])
        self.idle_box = (idle_box[0] - box[0], idle_box[1] - box[1], idle_box[2] - box[0], idle_box[3] - box[1])


def static_set(frames: list[Image.Image], delay: int) -> MascotSet:
    return MascotSet({"idle": Clip(frames, [delay] * len(frames))}, {"idle": ["idle"]})


def dot_set(diameter: int, color: tuple[int, int, int, int]) -> MascotSet:
    hi = 4
    image = Image.new("RGBA", (diameter * hi, diameter * hi), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((0, 0, diameter * hi - 1, diameter * hi - 1), fill=color)
    return static_set([image.resize((diameter, diameter), Image.Resampling.LANCZOS)], 1000)


def is_atlas(image: Image.Image) -> bool:
    return image.size == (ATLAS_COLS * CELL_W, 9 * CELL_H)


def file_set(path: Path, size: int, box_h: int) -> MascotSet:
    source = Image.open(path)
    if is_atlas(source):                       # a full sprite atlas dropped in mascots/ plays every reaction
        return rover_set(path, size, box_h)
    frames = []
    for frame in ImageSequence.Iterator(source):
        frame = frame.convert("RGBA")
        scale = min(size / frame.width, box_h / frame.height)
        frames.append(frame.resize((max(1, round(frame.width * scale)), max(1, round(frame.height * scale))),
                                   Image.Resampling.LANCZOS))
        if len(frames) >= 60:
            break
    if not frames:
        raise ValueError("image vide")
    return static_set(frames, 100 if len(frames) > 1 else 1000)


def rover_set(path: Path, size: int, box_h: int) -> MascotSet:
    sheet = Image.open(path).convert("RGBA")
    if not is_atlas(sheet):
        raise ValueError("unexpected atlas size")
    clips = {}
    for name, (row, count, delay) in ATLAS_CLIPS.items():
        frames = []
        for column in range(min(count, ATLAS_COLS)):
            cell = sheet.crop((column * CELL_W, row * CELL_H, (column + 1) * CELL_W, (row + 1) * CELL_H))
            if cell.getchannel("A").getbbox():
                frames.append(cell.resize((size, box_h), Image.Resampling.LANCZOS))
        if frames:
            clips[name] = Clip(frames, [delay] * len(frames))
    return MascotSet(clips, ATLAS_EVENTS)


def is_pack(folder: Path) -> bool:
    return (folder / "agent.js").is_file() and (folder / "map.png").is_file()


def pack_set(folder: Path, size: int, box_h: int) -> MascotSet:
    """Load a clippy.js character folder (agent.js + map.png): the format used for Clippit, Merlin, Links…"""
    text = (folder / "agent.js").read_text(encoding="utf-8")
    match = re.search(r"\{.*\}", text, re.S)
    if match is None:
        raise ValueError("agent.js illisible")
    data = json.loads(match.group(0))
    frame_w, frame_h = data["framesize"]
    sheet = Image.open(folder / "map.png").convert("RGBA")
    scale = min(size / frame_w, box_h / frame_h)
    target = (max(1, round(frame_w * scale)), max(1, round(frame_h * scale)))
    cells: dict[tuple[int, int], Image.Image] = {}

    def cell(x: int, y: int) -> Image.Image:
        if (x, y) not in cells:
            cells[(x, y)] = sheet.crop((x, y, x + frame_w, y + frame_h)).resize(target, Image.Resampling.LANCZOS)
        return cells[(x, y)]

    clips: dict[str, Clip] = {}
    for name, animation in data["animations"].items():
        frames, delays, branching = [], [], []
        for frame in animation.get("frames", []):
            composed = Image.new("RGBA", target, (0, 0, 0, 0))
            for x, y in reversed(frame.get("images") or []):   # the first image is the top layer
                composed.alpha_composite(cell(x, y))
            frames.append(composed)
            delays.append(max(40, int(frame.get("duration") or 100)))
            branches = (frame.get("branching") or {}).get("branches")
            branching.append([(b["frameIndex"], b["weight"]) for b in branches] if branches else None)
        if frames:
            clips[name] = Clip(frames, delays, branching if any(branching) else None)
    if not clips:
        raise ValueError("aucune animation dans agent.js")
    rest = clips.get("RestPose") or clips.get("Idle1_1") or next(iter(clips.values()))
    clips["idle"] = Clip([rest.frames[0]], [1000])
    variants = [name for name in clips if name.startswith("Idle") and name != "idle"]
    return MascotSet(clips, PACK_EVENTS, variants)


def pack_preview(folder: Path, size: tuple[int, int]) -> Image.Image | None:
    """Thumbnail of a pack's resting pose without decoding all of its animations."""
    try:
        text = (folder / "agent.js").read_text(encoding="utf-8")
        data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        frame_w, frame_h = data["framesize"]
        animations = data["animations"]
        animation = animations.get("RestPose") or animations.get("Idle1_1") or next(iter(animations.values()))
        sheet = Image.open(folder / "map.png").convert("RGBA")
        composed = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        for x, y in reversed(animation["frames"][0].get("images") or []):
            composed.alpha_composite(sheet.crop((x, y, x + frame_w, y + frame_h)))
        scale = min(size[0] / frame_w, size[1] / frame_h)
        thumb = composed.resize((max(1, round(frame_w * scale)), max(1, round(frame_h * scale))), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.alpha_composite(thumb, ((size[0] - thumb.width) // 2, (size[1] - thumb.height) // 2))
        return canvas
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
        return None
