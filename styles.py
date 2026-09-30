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
    ("prompt", "AI-ready prompt", "Clear goal first, then context, requirements, output",
     "Rewrite it as a prompt an AI assistant gets right the first time. Start with the task as one direct imperative "
     "sentence. Then add only what the speaker actually gave, as short labelled lines: Context (why, for whom), "
     "Requirements (one per line, concrete and checkable), Constraints (say what to do rather than what to avoid), "
     "Output (format, length, language). Use plain, direct wording: no greeting, no politeness filler, no repetition. "
     "If order matters, number the steps; if several unrelated requests were made, number them. Keep names, paths, "
     "identifiers, numbers and quoted text exactly. Never invent context, requirements or examples."),
    ("coding", "Coding task", "Goal, where, expected behavior, how to verify",
     "Rewrite it as a precise task for a coding agent. First line: the goal as an imperative sentence. Then only the "
     "parts the speaker gave, as short labelled lines: Where (exact files, functions, components or commands), "
     "Current vs expected behavior, Constraints (libraries, style, what must not change), Done when (how to check it: "
     "tests, commands, visible result). Keep every identifier, path, command and error message exactly as said. "
     "Never invent file names, requirements or details."),
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
