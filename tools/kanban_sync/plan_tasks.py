"""Canonical `### Task N: <title>` heading parser for numbered plan docs,
and the one-time milestone/sub-issue decomposition action for
plan-tracked kanban_sync issues (spec: docs/superpowers/specs/
2026-08-27-kanban-sync-milestones-and-subissues-design.md).

Only the canonical writing-plans template heading
(`### Task N: <title>`) is parsed - this repo's own plan docs are
confirmed (via direct grep, not assumption) to also use two other
conventions (`## Task N:`, one level shallower; `## T1a -`, a lettered
PR-group scheme with no "Task N" text at all). Both are deliberately out
of scope (spec §2, §4.1) - zero matches is a valid, non-error outcome,
not a signal to try a looser pattern.
"""
from __future__ import annotations

import re

_TASK_HEADING_RE = re.compile(r"^### Task (\d+):\s*(.+)$", re.MULTILINE)


def parse_canonical_tasks(text: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2).strip()) for m in _TASK_HEADING_RE.finditer(text)]
