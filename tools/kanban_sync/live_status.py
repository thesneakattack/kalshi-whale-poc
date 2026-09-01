"""Resolves a single issue's GitHub Projects V2 board column right now,
from its current open/closed state and status:* label - the one-issue
counterpart to sync.py's _sync_project_status, used by the `push-status`
CLI subcommand and the kanban-live-status skill so a claim/report-result/
block transition (real-time on the installed github-issues-kanban plugin's
labels, per its own protocol docs) shows up on the board immediately
instead of waiting for the next batch `kanban-board-sync` run.
"""
from __future__ import annotations

from tools.kanban_sync import labels, project_status
from tools.kanban_sync.github_client import IssueState


def resolve_project_status(issue: IssueState) -> str:
    if not issue.open:
        return project_status.STATUS_DONE

    status_labels = issue.labels & labels.ALL_STATUS_LABELS
    if len(status_labels) != 1:
        raise ValueError(
            f"issue #{issue.number} has {len(status_labels)} status:* labels "
            f"({sorted(status_labels)}) - expected exactly 1"
        )
    return project_status.STATUS_LABEL_TO_PROJECT_STATUS[next(iter(status_labels))]
