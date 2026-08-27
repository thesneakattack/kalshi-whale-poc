# tools/kanban_sync/sources_worktree.py
"""Worktree source (spec §5's first row): one SyncItem per active,
non-main git worktree. Status is derived mechanically from real branch/PR
state, never asked for as free text anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from tools.kanban_sync import labels
from tools.kanban_sync.github_client import GithubClient
from tools.kanban_sync.models import SyncItem

_ENTRY_RE = re.compile(
    r"worktree (?P<path>\S+)\n"
    r"HEAD (?P<sha>\S+)\n"
    r"branch refs/heads/(?P<branch>\S+)",
)


@dataclass(frozen=True)
class WorktreeInfo:
    path: str
    branch: str


def parse_worktree_list(porcelain_output: str) -> list[WorktreeInfo]:
    return [
        WorktreeInfo(path=m.group("path"), branch=m.group("branch"))
        for m in _ENTRY_RE.finditer(porcelain_output)
    ]


def live_worktree_branches(
    worktrees: Sequence[WorktreeInfo], *, main_branch: str = "main",
) -> set[str]:
    """The set of currently-live, non-main branch names - used by Part B's
    stale-tracking-issue closure (a branch no longer in this set has its
    worktree gone, so its tracking issue should close)."""
    return {wt.branch for wt in worktrees if wt.branch != main_branch}


def _status_for_pr_state(pr_state: str | None) -> tuple[str, bool, str]:
    """Returns (status_label, done, phase_label). phase_label is
    worktree-only - see labels.py's own docstring for why a plan/track item
    never gets PHASE_IMPLEMENTING/PHASE_VERIFICATION: a worktree already
    maps 1:1 to one branch, so this signal is reliable here in a way it
    isn't for those other sources."""
    if pr_state is None:
        return labels.STATUS_IN_PROGRESS, False, labels.PHASE_IMPLEMENTING
    if pr_state == "MERGED":
        return labels.STATUS_DONE, True, labels.PHASE_DONE
    if pr_state == "OPEN":
        return labels.STATUS_READY_FOR_REVIEW, False, labels.PHASE_VERIFICATION
    # CLOSED-not-merged: branch is still live work
    return labels.STATUS_IN_PROGRESS, False, labels.PHASE_IMPLEMENTING


def build_worktree_items(
    worktrees: Sequence[WorktreeInfo],
    pr_state_by_branch: dict[str, str | None],
    *,
    main_branch: str = "main",
) -> list[SyncItem]:
    items: list[SyncItem] = []
    for wt in worktrees:
        if wt.branch == main_branch:
            continue
        status_label, done, phase_label = _status_for_pr_state(pr_state_by_branch.get(wt.branch))
        items.append(SyncItem(
            kind=labels.SYNC_MARKER_KIND_WORKTREE,
            key=wt.branch,
            title=f"Worktree: {wt.branch}",
            status_label=status_label,
            type_label=labels.TYPE_TRACKING,
            context_body=(
                f"## Context\nTracking issue for the active git worktree at "
                f"`{wt.path}`, branch `{wt.branch}`. Generated and kept in "
                f"sync by `tools/kanban_sync` - labels here are overwritten "
                f"on the next sync run, don't hand-edit them.\n"
            ),
            acceptance_criteria=("Branch is merged to main and the worktree is removed.",),
            done=done,
            phase_label=phase_label,
        ))
    return items


def collect_worktree_items(
    porcelain_output: str,
    client: GithubClient,
    *,
    main_branch: str = "main",
) -> list[SyncItem]:
    worktrees = parse_worktree_list(porcelain_output)
    pr_state_by_branch = {
        wt.branch: client.find_pr_state(wt.branch)
        for wt in worktrees if wt.branch != main_branch
    }
    return build_worktree_items(worktrees, pr_state_by_branch, main_branch=main_branch)
