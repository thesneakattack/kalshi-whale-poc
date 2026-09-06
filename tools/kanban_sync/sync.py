"""Reconciliation engine (spec §6-9): given a list of SyncItems from any
source, find-or-create the matching GitHub issue by its sync marker,
reconcile labels, and close issues whose source item is now done -
without ever reopening one a human closed by hand. Depends-on resolution
(sync_pass_two, reconcile) is added in Task 8. The close_stale_* passes
cover the case the per-item loop structurally cannot: a source item that
has *vanished* from the scan (removed worktree, deleted ROADMAP bullet)
rather than been marked done.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, Sequence

from tools.kanban_sync import labels, project_status
from tools.kanban_sync.markers import build_marker, parse_marker
from tools.kanban_sync.models import SyncItem, SyncReport


class SyncGithubClient(Protocol):
    def find_by_marker(self, marker: str): ...
    def create_issue(self, title: str, body: str, labels_: Sequence[str]): ...
    def set_labels(self, number: int, add: Sequence[str], remove: Sequence[str]) -> None: ...
    def close_issue(self, number: int) -> None: ...
    def post_comment(self, number: int, body: str) -> None: ...
    def list_open_by_label(self, label: str): ...
    def list_closed_issues(self): ...
    def ensure_on_project(self, issue_number: int) -> str: ...
    def set_project_status(self, item_id: str, status: str) -> None: ...
    def get_sub_issues_summary(self, issue_number: int) -> tuple[int, int]: ...


_CLAIMED_BY_PREFIX = "claimed-by:"
_CLAIM_EXPIRES_PREFIX = "claim-expires:"


def _has_active_claim(issue_labels: frozenset[str]) -> bool:
    """True when the installed github-issues-kanban plugin skill currently
    holds a live, unexpired lock on this issue (claimed-by:<agent> +
    claim-expires:<iso-ts>, per its lock-protocol.md). status:* label
    reconciliation below must defer to this rather than silently reverting
    a claim back to whatever the source's own status_label computes - that
    would fight the plugin's claim instead of coexisting with it. A claim
    past its own TTL is stale by the plugin's own model ("stale claims
    auto-release on next dispatch cycle") - only a still-unexpired claim is
    protected here."""
    claimed_by = [l for l in issue_labels if l.startswith(_CLAIMED_BY_PREFIX)]
    expires = [l for l in issue_labels if l.startswith(_CLAIM_EXPIRES_PREFIX)]
    if not claimed_by or not expires:
        return False
    try:
        expires_at = datetime.fromisoformat(expires[0][len(_CLAIM_EXPIRES_PREFIX):])
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < expires_at


def _desired_base_labels(item: SyncItem) -> set[str]:
    """status:*/type:* always apply; phase:* only when the source could
    determine one (see SyncItem.phase_label's own docstring)."""
    result = {item.status_label, item.type_label}
    if item.phase_label:
        result.add(item.phase_label)
    return result


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
    if item.kind == labels.SYNC_MARKER_KIND_PLAN and item.classification:
        return (
            f"<!-- event: sync-mismatch | agent: kanban-board-sync | ts: {ts} -->\n"
            f"This issue is closed on GitHub, but this sync run classified its plan as "
            f"`{item.classification}` (not `done`). To resolve: reclassify the plan as "
            f"`done` in your classification JSON and re-run `sync --sources plan` — the "
            f"sync will then leave this issue closed correctly. Not reopening automatically."
        )
    return (
        f"<!-- event: sync-mismatch | agent: kanban-board-sync | ts: {ts} -->\n"
        f"This issue is closed on GitHub, but its source (`{item.kind}:{item.key}`) "
        f"is still open. Not reopening automatically - please reconcile manually."
    )


def _stale_worktree_comment(branch: str) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        f"<!-- event: sync-stale-worktree | agent: kanban-board-sync | ts: {ts} -->\n"
        f"Closing automatically: the worktree/branch `{branch}` this issue tracks "
        f"is no longer present in `git worktree list` (merged and removed, or "
        f"the worktree/branch was deleted). If this is wrong, reopen manually."
    )


def _mark_project_status_done(number: int, client: SyncGithubClient, *, dry_run: bool) -> None:
    """Shared by every close path in this module other than sync_pass_one's
    own item.done transition (which already calls _sync_project_status via
    a full SyncItem). Each of the mechanical closers below (stale worktree,
    stale roadmap, completed plan parent) previously called
    client.close_issue() directly and never touched the Project's Status
    field - root cause A of the board showing "Closed" issues outside the
    Done column (confirmed live 2026-08-31: 122 of 130 closed issues had a
    stale, missing, or absent Status). The target is always STATUS_DONE
    here - every caller reaches this only once it has decided to close the
    issue."""
    if dry_run:
        return
    item_id = client.ensure_on_project(number)
    client.set_project_status(item_id, project_status.STATUS_DONE)


def close_stale_worktree_issues(
    live_branches: set[str],
    client: SyncGithubClient,
    *,
    dry_run: bool,
) -> SyncReport:
    """Closes every open type:tracking issue whose worktree branch (parsed
    from its body's sync marker) is no longer in `live_branches`. Needed
    because build_worktree_items only ever iterates *currently-existing*
    worktrees (git worktree list) - unlike sources_plan.py/sources_roadmap.py,
    which iterate the full candidate set and correctly emit done=True items
    for anything they can still see, a removed worktree's item is simply
    absent from every future run's item list, so sync_pass_one's per-item
    loop never sees it and never closes it (issue #98, confirmed live
    2026-08-27). A *deleted* roadmap bullet has the same shape - see
    close_stale_roadmap_issues (issue #227).

    Defensive by construction: client.list_open_by_label already scopes to
    type:tracking and to open issues only (so a manually-closed issue for a
    dead branch is never even in the candidate set - no reopen risk). On top
    of that, any issue whose body doesn't parse as a marker, or whose parsed
    kind isn't "worktree", is skipped outright rather than trusting the
    label alone."""
    report = SyncReport(dry_run=dry_run)
    for issue in client.list_open_by_label(labels.TYPE_TRACKING):
        parsed = parse_marker(issue.body)
        if parsed is None:
            continue
        kind, key = parsed
        if kind != labels.SYNC_MARKER_KIND_WORKTREE or key in live_branches:
            continue
        if not dry_run:
            client.post_comment(issue.number, _stale_worktree_comment(key))
            client.close_issue(issue.number)
        _mark_project_status_done(issue.number, client, dry_run=dry_run)
        report.closed.append(f"#{issue.number} worktree:{key} (branch no longer live)")
    return report


def _stale_roadmap_comment(key: str) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return (
        f"<!-- event: sync-stale-roadmap | agent: kanban-board-sync | ts: {ts} -->\n"
        f"Closing automatically: the ROADMAP.md bullet this issue tracks "
        f"(`roadmap:{key}`) is no longer present in ROADMAP.md - deleted, or "
        f"reworded so its slug changed (in which case this sync run created a "
        f"fresh issue for the new wording). If this is wrong, reopen manually."
    )


def close_stale_roadmap_issues(
    current_keys: set[str],
    client: SyncGithubClient,
    *,
    dry_run: bool,
) -> SyncReport:
    """Closes every open type:feature issue whose roadmap bullet (parsed
    from its body's sync marker) is no longer among `current_keys` - the
    slugs of every bullet, checked or unchecked, in this run's ROADMAP.md
    parse. Needed because parse_roadmap_items can only emit done=True for
    a bullet it can still see; a *deleted* bullet's item is simply absent
    from the item list, so sync_pass_one never sees it and never closes it
    (issue #227; live instance #149, whose bullet commit 1cf6e61 deleted
    2026-08-28 and which stayed open until closed by hand 2026-08-30).
    Same class as #98, which close_stale_worktree_issues fixed for the
    worktree kind only.

    The close condition is presence, not checkbox state: a still-present
    `- [x]` bullet's key IS in current_keys, so this pass leaves it to
    sync_pass_one's done path (which closes without a stale comment).

    Same defensive construction as close_stale_worktree_issues:
    client.list_open_by_label scopes to open issues only (a hand-closed
    issue is never a candidate - no reopen risk), and a marker-less body or
    a non-roadmap kind is skipped rather than trusting the label. One extra
    guard: an empty current_keys makes no calls at all - a zero-bullet parse
    means a broken or missing source, not an empty roadmap, and must not
    mass-close every roadmap issue on the board."""
    report = SyncReport(dry_run=dry_run)
    if not current_keys:
        return report
    for issue in client.list_open_by_label(labels.TYPE_FEATURE):
        parsed = parse_marker(issue.body)
        if parsed is None:
            continue
        kind, key = parsed
        if kind != labels.SYNC_MARKER_KIND_ROADMAP or key in current_keys:
            continue
        if not dry_run:
            client.post_comment(issue.number, _stale_roadmap_comment(key))
            client.close_issue(issue.number)
        _mark_project_status_done(issue.number, client, dry_run=dry_run)
        report.closed.append(f"#{issue.number} roadmap:{key} (bullet no longer in ROADMAP.md)")
    return report


def close_completed_plan_parents(client: SyncGithubClient, *, dry_run: bool) -> SyncReport:
    """Closes an open type:plan-task issue whose sub-issues (created by
    plan_tasks.decompose_plan) are all complete. GitHub never auto-closes
    a parent when its sub-issues all close (confirmed live 2026-08-27) -
    this is the mechanical check that does it. Unlike decompose_plan
    (a one-time action, spec §4.2), this runs every sync - "did the last
    sub-issue just close" is exactly the kind of drift-over-time signal
    the rest of this module already reconciles (spec §4.4).

    Note: decompose_plan labels sub-issues with type:plan-task (the same label
    plan parents carry), but only plan parents have a sync marker with kind==plan.
    Sub-issues are filtered out by marker check, not label alone."""
    report = SyncReport(dry_run=dry_run)
    for issue in client.list_open_by_label(labels.TYPE_PLAN_TASK):
        parsed = parse_marker(issue.body)
        if parsed is None or parsed[0] != labels.SYNC_MARKER_KIND_PLAN:
            continue
        completed, total = client.get_sub_issues_summary(issue.number)
        if total == 0 or completed < total:
            continue
        if not dry_run:
            client.close_issue(issue.number)
        _mark_project_status_done(issue.number, client, dry_run=dry_run)
        report.closed.append(f"#{issue.number} all {total} sub-issues complete")
    return report


def backfill_closed_status(client: SyncGithubClient, *, dry_run: bool) -> SyncReport:
    """One-time pass (root cause A, confirmed live 2026-08-31: 122 of 130
    closed issues had a stale, missing, or absent board Status) to repair
    every closed issue that predates the fix to close_stale_worktree_issues/
    close_stale_roadmap_issues/close_completed_plan_parents above - those
    three used to call client.close_issue() without ever touching the
    Project's Status field. Sets Status=Done unconditionally for every
    currently-closed issue rather than reading the current value first,
    matching _sync_project_status's own "runs every time, self-healing"
    convention - idempotent, so re-running (or running after the fix is
    already in place) is always safe and a no-op in effect."""
    report = SyncReport(dry_run=dry_run)
    for issue in client.list_closed_issues():
        if not dry_run:
            item_id = client.ensure_on_project(issue.number)
            client.set_project_status(item_id, project_status.STATUS_DONE)
        report.updated.append(f"#{issue.number} backfilled Status=Done")
    return report


def _sync_project_status(
    item: SyncItem, number: int, client: SyncGithubClient, *, dry_run: bool,
) -> None:
    """Drives the Project's native "Status" single-select field - the only
    mechanism that actually produces the board's visible columns (Labels
    cannot drive Projects V2 board/table grouping at all). Runs
    unconditionally every time it's called, not gated on whether labels
    changed this run - see the design doc's §4.3 for why (gating would
    mean this never fires for a pre-existing item whose label already
    matches what's computed today). dry_run makes zero project calls,
    matching every other mutating operation in this module.

    item.done is checked BEFORE item.status_label, not the other way
    around: a source isn't guaranteed to flip status_label to reflect
    done=True (sources_roadmap.py/sources_plan.py do; a source need not) -
    mapping via status_label alone would land a just-finished item on
    "Next" instead of "Done"."""
    if dry_run:
        return
    status = (
        project_status.STATUS_DONE if item.done
        else project_status.STATUS_LABEL_TO_PROJECT_STATUS[item.status_label]
    )
    item_id = client.ensure_on_project(number)
    client.set_project_status(item_id, status)


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
            desired_labels = sorted(_desired_base_labels(item))
            issue = client.create_issue(item.title, _render_body(item), desired_labels)
            number_by_identity[identity] = issue.number
            report.created.append(f"#{issue.number} {item.title}")
            _sync_project_status(item, issue.number, client, dry_run=dry_run)
            continue

        number_by_identity[identity] = existing.number

        if item.done:
            if existing.open:
                if not dry_run:
                    client.close_issue(existing.number)
                report.closed.append(f"#{existing.number} {item.title}")
                _sync_project_status(item, existing.number, client, dry_run=dry_run)
            continue

        if not existing.open:
            if not dry_run:
                client.post_comment(existing.number, _mismatch_comment(item))
            report.flagged_mismatches.append(f"#{existing.number} {item.title}")
            continue

        active_claim = _has_active_claim(existing.labels)
        desired = _desired_base_labels(item)
        if active_claim:
            # Defer to the plugin's claim entirely for the status dimension -
            # neither remove its status:claimed/in-progress/etc. nor add the
            # item's own computed status:*, which would otherwise leave two
            # status:* labels on the issue at once.
            desired = desired - {item.status_label}
            stale_status: set[str] = set()
        else:
            existing_status_labels = existing.labels & labels.ALL_STATUS_LABELS
            stale_status = existing_status_labels - {item.status_label}
        existing_phase_labels = existing.labels & labels.ALL_PHASE_LABELS
        desired_phase = {item.phase_label} if item.phase_label else set()
        stale_phase = existing_phase_labels - desired_phase
        add = desired - existing.labels
        remove = stale_status | stale_phase
        if add or remove:
            if not dry_run:
                client.set_labels(existing.number, sorted(add), sorted(remove))
            report.updated.append(f"#{existing.number} {item.title}")
        if not active_claim:
            # The Project Status field has no analogue of its own to defer
            # to (the plugin never touches Project fields, only labels) -
            # leaving it exactly as it was is the safest "don't fight the
            # claim" behavior, matching the label-side deferral above.
            _sync_project_status(item, existing.number, client, dry_run=dry_run)

    return number_by_identity, report


def sync_pass_two(
    items: Sequence[SyncItem],
    number_by_identity: dict[tuple[str, str], int],
    client: SyncGithubClient,
    *,
    dry_run: bool,
) -> SyncReport:
    report = SyncReport(dry_run=dry_run)
    for item in items:
        identity = (item.kind, item.key)
        number = number_by_identity.get(identity)
        if number is None or item.done:
            continue  # not created this run, or already closed - no deps to set

        desired = {
            f"depends-on:#{number_by_identity[dep]}"
            for dep in item.depends_on_keys
            if dep in number_by_identity
        }
        existing = client.find_by_marker(build_marker(item.kind, item.key))
        current = {
            label for label in (existing.labels if existing else frozenset())
            if label.startswith("depends-on:#")
        }
        add = desired - current
        remove = current - desired
        if add or remove:
            if not dry_run:
                client.set_labels(number, sorted(add), sorted(remove))
            report.updated.append(f"#{number} depends-on updated")
    return report


def reconcile(
    items: Sequence[SyncItem],
    client: SyncGithubClient,
    *,
    dry_run: bool = False,
) -> SyncReport:
    number_by_identity, report = sync_pass_one(items, client, dry_run=dry_run)
    pass_two_report = sync_pass_two(items, number_by_identity, client, dry_run=dry_run)
    report.updated += pass_two_report.updated
    return report
