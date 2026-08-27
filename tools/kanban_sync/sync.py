"""Reconciliation engine (spec §6-9): given a list of SyncItems from any
source, find-or-create the matching GitHub issue by its sync marker,
reconcile labels, and close issues whose source item is now done -
without ever reopening one a human closed by hand. Depends-on resolution
(sync_pass_two, reconcile) is added in Task 8.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, Sequence

from tools.kanban_sync import labels
from tools.kanban_sync.markers import build_marker
from tools.kanban_sync.models import SyncItem, SyncReport


class SyncGithubClient(Protocol):
    def find_by_marker(self, marker: str): ...
    def create_issue(self, title: str, body: str, labels_: Sequence[str]): ...
    def set_labels(self, number: int, add: Sequence[str], remove: Sequence[str]) -> None: ...
    def close_issue(self, number: int) -> None: ...
    def post_comment(self, number: int, body: str) -> None: ...


def _render_body(item: SyncItem) -> str:
    parts = [item.context_body.rstrip(), "\n## Acceptance criteria"]
    parts += [f"- {c}" for c in item.acceptance_criteria]
    if item.scope_paths:
        parts.append("\n## Scope")
        parts += [f"- {p}" for p in item.scope_paths]
    parts.append(f"\n{build_marker(item.kind, item.key)}")
    return "\n".join(parts)


def _mismatch_comment(item: SyncItem) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        f"<!-- event: sync-mismatch | agent: kanban-board-sync | ts: {ts} -->\n"
        f"This issue is closed on GitHub, but its source (`{item.kind}:{item.key}`) "
        f"is still open. Not reopening automatically - please reconcile manually."
    )


def sync_pass_one(
    items: Sequence[SyncItem],
    client: SyncGithubClient,
    *,
    dry_run: bool,
) -> tuple[dict[tuple[str, str], int], SyncReport]:
    report = SyncReport(dry_run=dry_run)
    number_by_identity: dict[tuple[str, str], int] = {}

    for item in items:
        marker = build_marker(item.kind, item.key)
        existing = client.find_by_marker(marker)
        identity = (item.kind, item.key)

        if existing is None:
            if item.done:
                continue  # never existed and already done - nothing to create
            if dry_run:
                report.created.append(f"{identity}: {item.title}")
                continue
            desired_labels = sorted({item.status_label, item.type_label})
            issue = client.create_issue(item.title, _render_body(item), desired_labels)
            number_by_identity[identity] = issue.number
            report.created.append(f"#{issue.number} {item.title}")
            continue

        number_by_identity[identity] = existing.number

        if item.done:
            if existing.open:
                if not dry_run:
                    client.close_issue(existing.number)
                report.closed.append(f"#{existing.number} {item.title}")
            continue

        if not existing.open:
            if not dry_run:
                client.post_comment(existing.number, _mismatch_comment(item))
            report.flagged_mismatches.append(f"#{existing.number} {item.title}")
            continue

        existing_status_labels = existing.labels & labels.ALL_STATUS_LABELS
        stale_status = existing_status_labels - {item.status_label}
        add = ({item.status_label, item.type_label} - existing.labels)
        remove = stale_status
        if add or remove:
            if not dry_run:
                client.set_labels(existing.number, sorted(add), sorted(remove))
            report.updated.append(f"#{existing.number} {item.title}")

    return number_by_identity, report
