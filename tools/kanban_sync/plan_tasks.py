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

Fenced code blocks are stripped before the heading regex runs: a plan
that *documents* the convention inside a ``` or ~~~ fence (a bash
heredoc, a python test-fixture string) is showing an example, not
declaring a task. Issue #226: six such example lines in the milestones/
sub-issues plan became real sub-issues (#174, #175, #179, #180, #184,
#185).
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


# CommonMark fence: 3+ backticks or 3+ tildes, at most 3 leading spaces.
# group(1) is the whole run of fence characters, so its first char and its
# length identify the fence for the closing-fence check below.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _strip_fenced_code_blocks(text: str) -> str:
    """Drops every line inside a fenced code block, opening fence and
    closing fence included. Follows CommonMark's fence rules rather than a
    bare toggle: a block closes only on a fence of the *same character* with
    *at least as many* of them and nothing else on the line (so a four-
    backtick fence can wrap a three-backtick example, and a ```python line
    inside an open block is content, not a close), and a block that never
    closes runs to the end of the document. Line numbers are not preserved -
    parse_canonical_tasks returns none."""
    kept: list[str] = []
    fence: str | None = None  # the open block's fence run, e.g. "```" or "~~~~"
    for line in text.splitlines():
        match = _FENCE_RE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
            else:
                kept.append(line)
            continue
        if (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not line[match.end():].strip()
        ):
            fence = None
    return "\n".join(kept)


def parse_canonical_tasks(text: str) -> list[tuple[int, str]]:
    prose = _strip_fenced_code_blocks(text)
    return [(int(m.group(1)), m.group(2).strip()) for m in _TASK_HEADING_RE.finditer(prose)]


def decompose_plan(
    plan_filename: str,
    plan_text: str,
    parent_number: int,
    client,
    *,
    dry_run: bool,
    start_from_task: int = 1,
) -> dict:
    """One-time action (spec §4.2): assigns a milestone (create-or-reuse,
    titled after the plan filename) to the plan's parent issue, and - only
    if the plan uses the canonical "### Task N:" heading convention -
    creates one sub-issue per task with depends-on chaining between
    consecutive tasks. Safe to call repeatedly: skips entirely if the
    parent already has sub-issues (subIssuesSummary.total > 0), since a
    plan's task list is fixed once approved (spec §4.2's own reasoning for
    why this is one-time, not ongoing reconciliation).

    start_from_task (default 1, i.e. no skipping) excludes canonical tasks
    numbered below it from sub-issue creation entirely - for retroactively
    decomposing a plan that's already partway through execution (this
    repo has no reliable automated "is task N done" signal; a human/Claude
    judgment pass, same discipline as plan-doc classification, determines
    the cutoff). The first included task starts a fresh depends-on chain -
    it never references a skipped, never-created earlier task's issue."""
    completed, total = client.get_sub_issues_summary(parent_number)
    if total > 0:
        return {"skipped": "already decomposed", "existing_sub_issues": total}

    milestone_title = plan_filename
    milestone_number = client.find_milestone_by_title(milestone_title)
    if milestone_number is None and not dry_run:
        client.create_milestone(milestone_title)

    if not dry_run:
        client.set_milestone(parent_number, milestone_title)

    tasks = [
        (n, t) for n, t in parse_canonical_tasks(plan_text) if n >= start_from_task
    ]
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
