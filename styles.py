"""Transcription styles: the editing instruction sent to the model together with your dictation."""

from __future__ import annotations

# (id, label, one-line description, instruction). Labels and descriptions go through tr().
STYLES: list[tuple[str, str, str, str]] = [
    ("faithful", "Clean-up", "Fix hesitations and punctuation, keep your own words",
     "Remove hesitations, false starts and repetitions, fix punctuation and obvious recognition mistakes, and group "
     "related ideas. Keep the speaker's wording, order and every idea."),
    ("proofread", "Proofread", "Only spelling and grammar, nothing else",
     "Only correct spelling, grammar, conjugation, agreement and punctuation. Do not reword, reorder, shorten, add or "
     "remove anything, and keep hesitation words exactly as spoken."),
    ("concise", "Short and simple", "The plainest version that keeps everything needed",
     "Make it as short and plain as possible while keeping every requirement: simple words, no filler, no repetition, "
     "short sentences."),
    ("detailed", "Detailed", "Spell out what is implied and organise it",
     "Expand it into a fuller, well-organised text: spell out the context and requirements the speaker clearly implies, "
     "use clear paragraphs and precise wording. Never invent facts, names, numbers or requirements."),
    ("prompt", "Structured prompt", "Goal, context, constraints, expected output",
     "Turn it into an effective prompt for an AI assistant: state the goal first, then the context, the constraints and "
     "the expected output format as short labelled sections. Use only what was said."),
    ("bullets", "Bullet points", "One idea per line",
     "Turn it into a concise bulleted list, one idea per line, grouped logically. Keep technical terms and names exactly."),
    ("professional", "Professional tone", "Polished and courteous",
     "Rewrite it in a polished, courteous professional tone suited to a work message, keeping the meaning and every "
     "detail exactly."),
    ("casual", "Casual tone", "Relaxed and friendly",
     "Rewrite it in a relaxed, friendly conversational tone, keeping the meaning and every detail."),
]

DEFAULT_STYLE = "faithful"
CUSTOM = "custom"


def preset(style_id: str) -> tuple[str, str, str, str] | None:
    return next((item for item in STYLES if item[0] == style_id), None)


def instruction(style_id: str, custom_text: str = "") -> str:
    """The editing instruction for a preset id, or the user's own text for 'custom' (falling back to the default)."""
    if style_id == CUSTOM and custom_text.strip():
        return custom_text.strip()
    found = preset(style_id) or preset(DEFAULT_STYLE)
    return found[3]
