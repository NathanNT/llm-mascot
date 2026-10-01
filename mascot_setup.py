"""What can be customised per mascot: which animation plays for each event, and where the floating icons sit.

Stored in settings.json, keyed by the mascot (`"rover"`, `"pack:Clippy"`…):
    "animations": {"pack:Clippy": {"hover": "Greeting", "error": "Alert"}}      # only the events you changed
    "layout":     {"pack:Clippy": {"top": [0, -20], "rail": [30, 0]}}            # pixel offsets from the default place
"""

from __future__ import annotations

# (event, English label): the reactions a mascot has
EVENTS: tuple[tuple[str, str], ...] = (
    ("idle", "Resting"), ("hover", "Mouse over"), ("record", "Listening"), ("process", "Processing"),
    ("success", "Success"), ("error", "Error"), ("drag_left", "Dragged to the left"), ("drag_right", "Dragged to the right"),
)
# (key, English label): the pieces around the mascot that can be moved
ICONS: tuple[tuple[str, str], ...] = (("top", "Usage gauge"), ("bottom", "Microphone"), ("rail", "Shortcuts"))
LIMIT = 600                      # farthest an icon may sit from its default place, in pixels
EVENT_IDS = tuple(event for event, _ in EVENTS)
ICON_IDS = tuple(key for key, _ in ICONS)


def sanitize_animations(raw) -> dict[str, dict[str, str]]:
    clean: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return clean
    for mascot, events in raw.items():
        if not isinstance(mascot, str) or not isinstance(events, dict):
            continue
        kept = {event: name for event, name in events.items()
                if event in EVENT_IDS and isinstance(name, str) and 0 < len(name) <= 80}
        if kept:
            clean[mascot[:200]] = kept
    return clean


def sanitize_layout(raw) -> dict[str, dict[str, list[int]]]:
    clean: dict[str, dict[str, list[int]]] = {}
    if not isinstance(raw, dict):
        return clean
    for mascot, icons in raw.items():
        if not isinstance(mascot, str) or not isinstance(icons, dict):
            continue
        kept = {}
        for key, value in icons.items():
            if key in ICON_IDS and isinstance(value, (list, tuple)) and len(value) == 2 and all(
                    isinstance(part, (int, float)) and not isinstance(part, bool) for part in value):
                offset = [max(-LIMIT, min(LIMIT, round(part))) for part in value]
                if offset != [0, 0]:
                    kept[key] = offset
        if kept:
            clean[mascot[:200]] = kept
    return clean


def offset(prefs: dict, mascot: str, icon: str) -> tuple[int, int]:
    """Where `icon` sits relative to its default place for this mascot."""
    value = prefs.get("layout", {}).get(mascot, {}).get(icon)
    return (int(value[0]), int(value[1])) if value else (0, 0)


def overrides(prefs: dict, mascot: str) -> dict[str, str]:
    """The animation chosen by hand for each event of this mascot (events left on automatic are absent)."""
    return dict(prefs.get("animations", {}).get(mascot, {}))


def store(prefs: dict, mascot: str, animations: dict[str, str], layout: dict[str, list[int]]) -> None:
    """Put one mascot's choices into `prefs`, dropping empty entries so settings.json stays small."""
    for key, value in (("animations", sanitize_animations({mascot: animations}).get(mascot)),
                       ("layout", sanitize_layout({mascot: layout}).get(mascot))):
        table = dict(prefs.get(key, {}))
        if value:
            table[mascot] = value
        else:
            table.pop(mascot, None)
        prefs[key] = table
