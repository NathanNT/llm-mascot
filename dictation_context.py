"""The last few dictations, kept in memory only, so that the rewrite can understand what "it", "that file" or a name refers to."""

from __future__ import annotations

import time
from collections import deque

LIMIT = 5                 # dictations kept
MAX_AGE = 30 * 60.0       # seconds after which a dictation is no longer "recent"
MAX_CHARS = 700           # per dictation: long ones are cut so the rewrite request stays small


class RecentDictations:
    def __init__(self, limit: int = LIMIT, max_age: float = MAX_AGE):
        self.limit, self.max_age = limit, max_age
        self.items: deque[tuple[float, str]] = deque(maxlen=limit)

    def add(self, text: str, now: float | None = None) -> None:
        text = " ".join(text.split())
        if text:
            self.items.append((time.monotonic() if now is None else now, text[:MAX_CHARS]))

    def recent(self, now: float | None = None) -> list[str]:
        """Oldest first, without the ones that are too old."""
        now = time.monotonic() if now is None else now
        return [text for stamp, text in self.items if now - stamp <= self.max_age]

    def clear(self) -> None:
        self.items.clear()
