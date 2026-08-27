"""ROADMAP.md source (spec §5's second row): one SyncItem per open
`- [ ]` top-level bullet. ROADMAP.md's checkbox state IS actively
maintained in this repo (unlike numbered plan docs - see sources_plan.py
and spec §5), so a direct 1:1 parse is safe here.
"""
from __future__ import annotations

import re

from tools.kanban_sync import labels
from tools.kanban_sync.markers import extract_roadmap_title, slugify
from tools.kanban_sync.models import SyncItem

_BULLET_RE = re.compile(
    r"^- \[(?P<mark>[ x])\] (?P<first_line>.+)$", re.MULTILINE,
)


def _iter_bullets(text: str) -> list[tuple[bool, str]]:
    """Returns (checked, full_bullet_text) for every top-level `- [ ]`/
    `- [x]` bullet, where full_bullet_text includes indented continuation
    lines up to the next top-level bullet or blank-line-delimited block."""
    lines = text.splitlines()
    bullets: list[tuple[bool, str]] = []
    current_lines: list[str] | None = None
    current_checked = False

    for line in lines:
        match = re.match(r"^- \[([ x])\] (.+)$", line)
        if match:
            if current_lines is not None:
                bullets.append((current_checked, "\n".join(current_lines)))
            current_checked = match.group(1) == "x"
            current_lines = [match.group(2)]
            continue
        if current_lines is not None and line.startswith("      "):
            current_lines.append(line.strip())
            continue
        if current_lines is not None:
            bullets.append((current_checked, "\n".join(current_lines)))
            current_lines = None

    if current_lines is not None:
        bullets.append((current_checked, "\n".join(current_lines)))

    return bullets


def parse_roadmap_items(text: str) -> list[SyncItem]:
    """A checked (`- [x]`) bullet still produces a SyncItem (done=True),
    rather than being skipped outright - sync_pass_one's own create/close
    logic already handles both ends of this correctly (no existing issue +
    done => nothing created; existing open issue + done => closed), so
    skipping it here would only lose the second case: a bullet open when its
    tracking issue was first created, then later checked off, would never
    get that issue auto-closed. Same bug shape as sources_plan.py's
    build_plan_items - found and fixed together 2026-08-27 (that module's
    own docstring has the fuller incident writeup)."""
    items: list[SyncItem] = []
    for checked, bullet_text in _iter_bullets(text):
        title = extract_roadmap_title(bullet_text)
        key = slugify(title)
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_ROADMAP,
            key=key,
            title=title,
            status_label=labels.STATUS_DONE if checked else labels.STATUS_CLAIMABLE,
            type_label=labels.TYPE_FEATURE,
            context_body=f"## Context\n{bullet_text}",
            acceptance_criteria=(
                "This bullet is checked off (`- [x]`) in ROADMAP.md with a "
                "factual note on what shipped, per the close-roadmap-item "
                "skill's convention.",
            ),
            done=checked,
        ))
    return items
