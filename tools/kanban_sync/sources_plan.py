"""Numbered plan docs not already represented by a track (spec §5's last
row). Candidate listing is mechanical (which files exist, and which of
them active-tracks-board.md already references); classifying a candidate
as done/in-progress/not-started is a judgment call the on-demand
kanban-board-sync skill makes by reading git log/CLAUDE.md/ROADMAP.md, not
something this module infers from the plan doc's own checkboxes -
measured unreliable in spec §5.
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.kanban_sync import labels
from tools.kanban_sync.models import SyncItem

_PLAN_DOC_REF_RE = re.compile(r"docs/superpowers/plans/([\w.-]+\.md)")
_EXCLUDED_FILENAMES = frozenset({"2026-08-26-active-tracks-board.md"})


def list_plan_candidates(plans_dir: Path, active_tracks_board_text: str) -> list[str]:
    referenced = set(_PLAN_DOC_REF_RE.findall(active_tracks_board_text))
    all_plans = {p.name for p in plans_dir.glob("*.md")}
    return sorted(all_plans - referenced - _EXCLUDED_FILENAMES)


def build_plan_items(classifications: dict[str, dict]) -> list[SyncItem]:
    """`classifications` maps filename -> {"status": "done"|"in-progress"|
    "not-started", "note": str}, produced by the kanban-board-sync skill's
    judgment-assisted classification step."""
    items: list[SyncItem] = []
    for filename, info in classifications.items():
        if info["status"] == "done":
            continue
        note = info.get("note", "")
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_PLAN,
            key=filename,
            title=f"Plan: {filename}",
            status_label=labels.STATUS_CLAIMABLE,
            type_label=labels.TYPE_PLAN_TASK,
            context_body=(
                f"## Context\nTracks `docs/superpowers/plans/{filename}` "
                f"as a whole, not per-task (see kanban-board-sync-design.md "
                f"§5 on why plan-doc checkboxes aren't a reliable per-task "
                f"signal in this repo).\n\nClassification: {info['status']}."
                f"\n{note}"
            ),
            acceptance_criteria=(
                f"`docs/superpowers/plans/{filename}` is reclassified "
                f"'done' on a future sync run.",
            ),
            done=False,
        ))
    return items
