"""active-tracks-board.md source (spec §5's third row, granularity fixed
by this plan's Global Constraint 5): one SyncItem per `## Track <letter>`
heading - never per sub-step (CH1/CH2/...). A track whose section contains
a hard-gate keyword depends on every track heading that appears earlier in
the file; this is a deliberately simple, conservative heuristic (spec §8) -
over-blocking is safer than inventing a wrong edge from free-form prose.
"""
from __future__ import annotations

import re

from tools.kanban_sync import labels
from tools.kanban_sync.models import SyncItem

_TRACK_HEADING_RE = re.compile(r"^## Track (?P<letter>[A-Z]) — (?P<title>.+)$", re.MULTILINE)
# Spans the WHOLE status paragraph, not just its first line - stops at the
# first blank line (a real paragraph boundary in this doc's own markdown
# convention) or end of text. A single-line capture previously let a
# multi-line status ("CH1 done... [next line] CH2 is next, not started")
# read as done purely from its first line, since the "not started" qualifier
# a few lines down was never even seen by the done-check below. Found live
# 2026-08-27: this exact shape incorrectly auto-closed a real, still-open
# GitHub issue for a track that was nowhere near finished.
_STATUS_LINE_RE = re.compile(r"\*\*Status[^*]*:\*\*.*?(?=\n[ \t]*\n|\Z)", re.DOTALL)
_GATE_KEYWORDS = ("hard-gated", "hard gate", "gated behind")
_DONE_KEYWORDS = ("complete", "done")
# Overrides a _DONE_KEYWORDS match: these phrases are a stronger, more
# specific signal that the track (or a piece of it) is explicitly NOT
# finished, even when "done"/"complete" also appears elsewhere in the same
# paragraph describing a sub-step. Conservative on purpose, matching this
# module's own stated bias (see the file docstring) - a track that's
# ambiguous between done and not-done should read as not-done, the same
# direction this file already errs for gate detection.
_NOT_DONE_OVERRIDE_KEYWORDS = (
    "not started", "not done", "not complete", "not yet", "still open", "in progress",
)


def _split_sections(text: str) -> list[tuple[str, str, str]]:
    """Returns (letter, title, section_body) for every `## Track X` heading,
    where section_body runs to the next `## ` heading or end of file."""
    headings = list(_TRACK_HEADING_RE.finditer(text))
    sections = []
    for idx, match in enumerate(headings):
        start = match.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        sections.append((match.group("letter"), match.group("title"), text[start:end]))
    return sections


def parse_track_items(text: str) -> list[SyncItem]:
    sections = _split_sections(text)
    letters_in_order = [letter for letter, _, _ in sections]
    items: list[SyncItem] = []

    for idx, (letter, title, body) in enumerate(sections):
        status_match = _STATUS_LINE_RE.search(body)
        status_text = status_match.group(0) if status_match else ""
        status_lower = status_text.lower()
        done = any(kw in status_lower for kw in _DONE_KEYWORDS) and not any(
            kw in status_lower for kw in _NOT_DONE_OVERRIDE_KEYWORDS
        )

        heading = f"Track {letter} — {title}"
        depends_on: tuple[tuple[str, str], ...] = ()
        if any(kw in (heading + " " + body).lower() for kw in _GATE_KEYWORDS):
            depends_on = tuple(
                ("track", earlier_letter) for earlier_letter in letters_in_order[:idx]
            )
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_TRACK,
            key=letter,
            title=heading,
            status_label=labels.STATUS_CLAIMABLE,
            type_label=labels.TYPE_INVESTIGATION,
            context_body=f"## Context\n{heading}\n\n{status_text or body.strip()[:500]}",
            acceptance_criteria=(
                f"`active-tracks-board.md`'s Track {letter} section is "
                f"updated to a completed status.",
            ),
            depends_on_keys=depends_on,
            done=done,
        ))
    return items
