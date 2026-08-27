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
from tools.kanban_sync import labels

_TASK_HEADING_RE = re.compile(r"^### Task (\d+):[ \t]*(.+)$", re.MULTILINE)
# Changed \s* to [ \t]* to match only same-line horizontal whitespace (space, tab),
# not newlines. This prevents capturing text from subsequent paragraphs when a
# heading has no inline title. A heading like "### Task 3:" with no title will
# not match, which is the correct behavior — we only extract tasks that have
# actual titles, not empty ones.


def parse_canonical_tasks(text: str) -> list[tuple[int, str]]:
    return [(int(m.group(1)), m.group(2).strip()) for m in _TASK_HEADING_RE.finditer(text)]


def decompose_plan(
    plan_filename: str,
    plan_text: str,
    parent_number: int,
    client,
    *,
    dry_run: bool,
) -> dict:
    """One-time action (spec §4.2): assigns a milestone (create-or-reuse,
    titled after the plan filename) to the plan's parent issue, and - only
    if the plan uses the canonical "### Task N:" heading convention -
    creates one sub-issue per task with depends-on chaining between
    consecutive tasks. Safe to call repeatedly: skips entirely if the
    parent already has sub-issues (subIssuesSummary.total > 0), since a
    plan's task list is fixed once approved (spec §4.2's own reasoning for
    why this is one-time, not ongoing reconciliation)."""
    completed, total = client.get_sub_issues_summary(parent_number)
    if total > 0:
        return {"skipped": "already decomposed", "existing_sub_issues": total}

    milestone_title = plan_filename
    milestone_number = client.find_milestone_by_title(milestone_title)
    if milestone_number is None and not dry_run:
        client.create_milestone(milestone_title)

    if not dry_run:
        client.set_milestone(parent_number, milestone_title)

    tasks = parse_canonical_tasks(plan_text)
    created_sub_issues: list[int] = []
    if tasks and not dry_run:
        previous_number: int | None = None
        for task_number, task_title in tasks:
            issue = client.create_issue(
                f"Task {task_number}: {task_title}",
                f"## Context\nPart of `docs/superpowers/plans/{plan_filename}`.",
                [labels.STATUS_CLAIMABLE, labels.TYPE_PLAN_TASK],
                parent=parent_number,
                milestone=milestone_title,
            )
            created_sub_issues.append(issue.number)
            if previous_number is not None:
                client.set_labels(issue.number, [f"depends-on:#{previous_number}"], [])
            previous_number = issue.number

    return {
        "milestone": milestone_title,
        "tasks_found": len(tasks),
        "sub_issues_created": created_sub_issues,
    }
