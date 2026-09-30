"""Word-level accuracy of a transcript against the text that was actually read (no Tk, no audio)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_WORD = re.compile(r"[\w']+", re.UNICODE)


def tokens(text: str, strip_accents: bool = False) -> list[str]:
    """Lower-case words without punctuation; hyphens split words ('back-end' = 'back end'), apostrophes stay."""
    text = text.lower().replace("’", "'").replace("‑", " ").replace("-", " ").replace("_", " ")
    if strip_accents:
        text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return [word.strip("'") for word in _WORD.findall(text) if word.strip("'")]


@dataclass
class Score:
    n_ref: int
    substitutions: int
    deletions: int
    insertions: int
    ops: list[tuple[str, str | None, str | None]]      # ("ok"|"sub"|"del"|"ins", reference word, transcript word)

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self) -> float:
        """Word error rate: errors divided by the number of reference words (0 = perfect)."""
        return self.errors / self.n_ref if self.n_ref else 0.0

    @property
    def missed(self) -> list[str]:
        """Reference words that were dropped or replaced by something else."""
        return [ref for op, ref, _ in self.ops if op in ("sub", "del") and ref]


def align(reference: list[str], hypothesis: list[str]) -> list[tuple[str, str | None, str | None]]:
    rows, cols = len(reference), len(hypothesis)
    cost = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        cost[i][0] = i
    for j in range(1, cols + 1):
        cost[0][j] = j
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            same = reference[i - 1] == hypothesis[j - 1]
            cost[i][j] = min(cost[i - 1][j - 1] + (0 if same else 1), cost[i - 1][j] + 1, cost[i][j - 1] + 1)
    ops: list[tuple[str, str | None, str | None]] = []
    i, j = rows, cols
    while i or j:
        if i and j and cost[i][j] == cost[i - 1][j - 1] + (0 if reference[i - 1] == hypothesis[j - 1] else 1):
            ops.append(("ok" if reference[i - 1] == hypothesis[j - 1] else "sub", reference[i - 1], hypothesis[j - 1]))
            i, j = i - 1, j - 1
        elif i and cost[i][j] == cost[i - 1][j] + 1:
            ops.append(("del", reference[i - 1], None))
            i -= 1
        else:
            ops.append(("ins", None, hypothesis[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def score(reference: str, hypothesis: str, strip_accents: bool = False) -> Score:
    ref, hyp = tokens(reference, strip_accents), tokens(hypothesis, strip_accents)
    ops = align(ref, hyp)
    return Score(len(ref), sum(op == "sub" for op, *_ in ops), sum(op == "del" for op, *_ in ops),
                 sum(op == "ins" for op, *_ in ops), ops)


def recommend(results: list[dict], tolerance: float = 0.02, snappy_seconds: float = 2.0) -> dict:
    """Pick the most accurate, the fastest, the fastest within `tolerance` of the most accurate,
    and the most accurate one that still answers within `snappy_seconds` ('snappy', absent when none does)."""
    valid = [r for r in results if r.get("wer") is not None and r.get("seconds") is not None]
    if not valid:
        return {}
    best = min(valid, key=lambda r: (r["wer"], r["seconds"]))
    fastest = min(valid, key=lambda r: (r["seconds"], r["wer"]))
    balanced = min((r for r in valid if r["wer"] <= best["wer"] + tolerance), key=lambda r: r["seconds"])
    quick = [r for r in valid if r["seconds"] <= snappy_seconds]
    picks = {"accuracy": best["name"], "speed": fastest["name"], "balanced": balanced["name"]}
    if quick:
        picks["snappy"] = min(quick, key=lambda r: (r["wer"], r["seconds"]))["name"]
    return picks
