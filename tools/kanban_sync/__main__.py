"""CLI entrypoint for the kanban board sync (docs/archive/lane-9-tooling-ci-process-governance/specs/2026-08-26-kanban-board-sync-design.md). `python -m tools.kanban_sync sync
--sources worktree,roadmap` runs the fully mechanical sources (used
by the /checkpoint skill integration, Task 12); `--sources plan`
additionally needs `--plan-classifications <path-to-json>`, produced by
the on-demand kanban-board-sync skill's judgment-assisted classification
step (Task 11).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.kanban_sync import labels
from tools.kanban_sync.github_client import GithubClient, GithubCliError
from tools.kanban_sync.review_tier import count_review_artifacts, review_tier
from tools.kanban_sync.live_status import resolve_project_status
from tools.kanban_sync.markers import build_marker
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync.plan_tasks import decompose_plan
from tools.kanban_sync.sources_plan import build_plan_items, list_plan_candidates, resolve_plan_path
from tools.kanban_sync.sources_roadmap import parse_roadmap_items
from tools.kanban_sync.sources_worktree import (
    collect_worktree_items, live_worktree_branches, parse_worktree_list,
)
from tools.kanban_sync.sync import (
    backfill_closed_status, close_completed_plan_parents, close_stale_roadmap_issues,
    close_stale_worktree_issues, reconcile,
)

REPO = "thesneakattack/kalshi-whale-poc"
ROADMAP_PATH = Path("ROADMAP.md")
PLANS_DIR = Path("docs/superpowers/plans")
# 2026-09-06 planning-lanes migration target (docs/archive/lane-9-tooling-ci-process-governance/specs/2026-09-06-planning-lanes-design.md): decompose-plan (_cmd_decompose_plan) must keep finding
# a named plan doc's content via resolve_plan_path() even after it moves to
# ARCHIVE_ROOT/lane-<N>-<slug>/plans/ - see resolve_plan_path's own docstring for
# why this differs from list_plan_candidates()'s "no change needed" finding.
ARCHIVE_ROOT = Path("docs/archive")
KNOWN_SOURCES = frozenset({"worktree", "roadmap", "plan"})


def _check_project_scope() -> None:
    result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    combined = f"{result.stdout}\n{result.stderr}"
    if "project" not in combined:
        print(
            "error: gh CLI is missing the 'project' OAuth scope needed for "
            "Projects V2 board access.\nRun: gh auth refresh -s project",
            file=sys.stderr,
        )
        sys.exit(1)


# Conservative per-item GraphQL call estimates. A real (write) run costs up to
# ~6 calls/item: sync_pass_one's find_by_marker + create_issue/set_labels +
# ensure_on_project/set_project_status (2026-08-27, Project Status field sync -
# both are GraphQL-backed under the hood despite looking like plain CLI flags,
# same as the rest), plus sync_pass_two's own find_by_marker + set_labels for
# depends-on reconciliation. A --dry-run never writes and never touches the
# project - only sync_pass_one's find_by_marker runs - so 1/item, unchanged.
# Found live 2026-08-27: a 33-item real sync exhausted the 5000/5000 GraphQL
# quota partway through with zero advance warning, leaving fields/labels
# half-applied - and the failure surfaced as a misleading gh CLI error ("unknown
# owner type") rather than anything rate-limit-shaped, only identifiable via
# GH_DEBUG=api. Refusing to start an under-budget run is safer than a partial one.
_ESTIMATED_CALLS_PER_ITEM_WRITE = 6
_ESTIMATED_CALLS_PER_ITEM_DRY_RUN = 1


def _check_rate_limit_budget(client, item_count: int, dry_run: bool) -> None:
    remaining, reset_epoch = client.graphql_rate_limit()
    per_item = _ESTIMATED_CALLS_PER_ITEM_DRY_RUN if dry_run else _ESTIMATED_CALLS_PER_ITEM_WRITE
    estimated = item_count * per_item
    if remaining < estimated:
        reset_str = datetime.fromtimestamp(reset_epoch, tz=timezone.utc).isoformat()
        print(
            f"error: GitHub GraphQL rate limit too low to safely run this sync.\n"
            f"  remaining: {remaining}, estimated need: ~{estimated} (for {item_count} items)\n"
            f"  resets at: {reset_str}\n"
            f"Refusing to start rather than fail partway through a bulk sync and "
            f"leave issues/fields/labels half-applied.",
            file=sys.stderr,
        )
        sys.exit(1)


def _parse_sources(raw: str) -> list[str]:
    """Split, whitespace-strip, and validate a `--sources` value. Exits 1
    on any unrecognized name rather than silently dropping it - a typo or
    stray space must not produce a CLI that exits 0 while quietly skipping
    a source (see Task 10 review finding #1)."""
    sources = [token.strip() for token in raw.split(",")]
    unknown = [s for s in sources if s not in KNOWN_SOURCES]
    if unknown:
        print(
            f"error: unknown --sources value(s): {', '.join(unknown)}. "
            f"Valid sources: {', '.join(sorted(KNOWN_SOURCES))}",
            file=sys.stderr,
        )
        sys.exit(1)
    return sources


def _collect_items(
    sources: list[str], plan_classifications: Path | None,
) -> tuple[list[SyncItem], set[str] | None]:
    """Returns (items, live_worktree_branches). live_worktree_branches is
    None when "worktree" isn't among `sources` - that's also the signal
    _cmd_sync uses to decide whether to run the stale-worktree-issue
    closure pass (Part B), since that pass only makes sense when this run
    actually has fresh worktree state to check against."""
    # Checked first, before any source-specific work (including real
    # subprocess calls for worktree), so a missing flag fails fast
    # rather than after wasting real `git`/`gh` calls (Task 10 review
    # finding #2).
    if "plan" in sources and plan_classifications is None:
        print("error: --sources plan requires --plan-classifications <path>", file=sys.stderr)
        sys.exit(1)

    items: list[SyncItem] = []
    live_branches: set[str] | None = None
    client = GithubClient(REPO)

    if "worktree" in sources:
        porcelain = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        items += collect_worktree_items(porcelain, client)
        live_branches = live_worktree_branches(parse_worktree_list(porcelain))
    if "roadmap" in sources:
        items += parse_roadmap_items(ROADMAP_PATH.read_text())
    if "plan" in sources:
        items += build_plan_items(json.loads(plan_classifications.read_text()))

    return items, live_branches


def _cmd_sync(args: argparse.Namespace) -> None:
    _check_project_scope()
    sources = _parse_sources(args.sources)
    items, live_branches = _collect_items(sources, args.plan_classifications)
    client = GithubClient(REPO)
    _check_rate_limit_budget(client, len(items), args.dry_run)
    report = reconcile(items, client, dry_run=args.dry_run)

    if live_branches is not None:
        stale_report = close_stale_worktree_issues(live_branches, client, dry_run=args.dry_run)
        report.closed += stale_report.closed

    if "roadmap" in sources:
        # Every bullet this run parsed, checked or not - the stale pass
        # closes on absence from this set, never on checkbox state.
        roadmap_keys = {
            item.key for item in items if item.kind == labels.SYNC_MARKER_KIND_ROADMAP
        }
        stale_roadmap_report = close_stale_roadmap_issues(
            roadmap_keys, client, dry_run=args.dry_run,
        )
        report.closed += stale_roadmap_report.closed

    # Unconditional, unlike the "plan" source above: this is a purely
    # mechanical sub-issue-count check with no dependency on `items` or on
    # --plan-classifications, so it must not be gated behind the
    # judgment-assisted plan-doc classification path that "plan" also
    # requires - see close_completed_plan_parents's own docstring, and
    # test_cmd_sync_runs_close_completed_plan_parents_even_when_plan_not_in_sources
    # (root cause B, confirmed live 2026-08-31: the routine /checkpoint-wired
    # invocation never included "plan", so a plan whose sub-issues all
    # finished sat open indefinitely).
    plan_close_report = close_completed_plan_parents(client, dry_run=args.dry_run)
    report.closed += plan_close_report.closed

    print(f"created: {len(report.created)}")
    for line in report.created:
        print(f"  + {line}")
    print(f"updated: {len(report.updated)}")
    print(f"closed: {len(report.closed)}")
    print(f"flagged mismatches: {len(report.flagged_mismatches)}")
    for line in report.flagged_mismatches:
        print(f"  ! {line}")


def _cmd_backfill_status(args: argparse.Namespace) -> None:
    """One-time repair (root cause A, confirmed live 2026-08-31): sets
    Status=Done on every already-closed issue that predates the fix to the
    three mechanical close paths, which used to close an issue without
    ever touching the Project's Status field. Safe to re-run - idempotent,
    same as the fix itself."""
    _check_project_scope()
    client = GithubClient(REPO)
    report = backfill_closed_status(client, dry_run=args.dry_run)
    print(f"backfilled: {len(report.updated)}")
    for line in report.updated:
        print(f"  + {line}")


def _cmd_push_status(args: argparse.Namespace) -> None:
    """Pushes one issue's current open/closed state + status:* label onto
    the Project's Status field right now - the one-issue counterpart to
    the batch `sync` command's per-item _sync_project_status, for the
    kanban-live-status skill to call immediately after a claim/report-
    result/block transition instead of waiting for the next batch run."""
    _check_project_scope()
    client = GithubClient(REPO)
    issue = client.get_issue(args.issue)
    if issue is None:
        print(f"error: issue #{args.issue} not found", file=sys.stderr)
        sys.exit(1)
    try:
        status = resolve_project_status(issue)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not args.dry_run:
        item_id = client.ensure_on_project(issue.number)
        client.set_project_status(item_id, status)
    print(f"#{issue.number} -> {status}")


def _cmd_plan_candidates(_args: argparse.Namespace) -> None:
    for path in list_plan_candidates(PLANS_DIR):
        print(path)


_REVIEW_TIER_REQUIREMENT = {"A": 3, "B": 1}


def _cmd_review_tier(args: argparse.Namespace) -> None:
    """The merge-time check (the AI-assisted engineering principles design §4).

    Decides Tier A/B from the PR's paths, diff, and labels, counts its
    persisted review artifacts, and prints PASS / FAIL / EXEMPT. The exit code
    is the machine-readable answer: 0 pass or exempt, 1 fail, 2 could-not-read.
    Never prints PASS on a fetch failure - a silent pass is worse than no
    check. Touches nothing on the Projects board, so no project scope needed.
    """
    if args.exempt is not None:
        reason = args.exempt.strip()
        if not reason:
            print("error: --exempt needs a non-empty reason", file=sys.stderr)
            sys.exit(1)
        _emit_review_tier(
            args, tier="exempt", reasons=[f"mechanical/trivial: {reason}"],
            artifacts=0, matched=[], required=0, verdict="EXEMPT",
        )
        return

    client = GithubClient(REPO)
    try:
        files = client.get_pr_files(args.pr)
        diff_text = client.get_pr_diff(args.pr)
        pr_labels = sorted(client.get_pr_labels(args.pr))
        comments = client.list_pr_comments(args.pr)
    except GithubCliError as exc:
        print(f"error: could not read PR #{args.pr}: {exc}", file=sys.stderr)
        sys.exit(2)
    except (ValueError, KeyError, TypeError) as exc:
        # A malformed or unexpected gh payload is still "could not read", not
        # "review missing": without this it would surface as a bare traceback
        # and exit 1, the documented FAIL code (2026-09-07 PR-stage review, N4).
        print(f"error: unreadable response for PR #{args.pr}: {exc!r}", file=sys.stderr)
        sys.exit(2)
    if not files:
        print(f"error: PR #{args.pr} reported no changed files - refusing to "
              f"classify it as Tier B on an empty list", file=sys.stderr)
        sys.exit(2)

    tier, reasons = review_tier(
        files, diff_text=diff_text, pr_labels=pr_labels, escalate=args.tier == "A",
    )
    artifacts, matched = count_review_artifacts(comments)
    required = _REVIEW_TIER_REQUIREMENT[tier]
    verdict = "PASS" if artifacts >= required else "FAIL"
    _emit_review_tier(
        args, tier=tier, reasons=reasons, artifacts=artifacts,
        matched=matched, required=required, verdict=verdict,
    )
    if verdict == "FAIL":
        sys.exit(1)


def _emit_review_tier(args, *, tier, reasons, artifacts, matched, required, verdict):
    if args.json:
        print(json.dumps({
            "pr": args.pr, "tier": tier, "reasons": reasons,
            "artifacts": artifacts, "matched": matched,
            "required": required, "verdict": verdict,
        }, indent=2))
        return
    label = "EXEMPT" if tier == "exempt" else f"Tier {tier}"
    print(f"PR #{args.pr}: {label} — {verdict}")
    for reason in reasons:
        print(f"  why: {reason}")
    if tier != "exempt":
        print(f"  artifacts: {artifacts} of {required} required")
        for item in matched:
            print(f"    - {item}")
        if verdict == "FAIL":
            print(
                "  do not merge. Supply what is missing: a fresh Agent for an "
                "adversarial pass, the author for a Tier B self-review comment."
            )


def _cmd_decompose_plan(args: argparse.Namespace) -> None:
    _check_project_scope()
    client = GithubClient(REPO)
    if args.parent_issue is not None:
        # Bypasses find_by_marker's plan:* marker lookup entirely (not just
        # overriding its result), for a plan whose tracking issue doesn't
        # carry that marker at all - e.g. an issue created by hand, or
        # tracked under a different marker kind entirely. Works for any
        # existing issue, present or future, without this tool needing to
        # learn every possible marker kind a parent issue might carry.
        parent_number = args.parent_issue
    else:
        marker = build_marker(labels.SYNC_MARKER_KIND_PLAN, args.plan)
        existing = client.find_by_marker(marker)
        if existing is None:
            print(
                f"error: no tracked issue found for plan {args.plan!r} - "
                f"run `sync --sources plan` first",
                file=sys.stderr,
            )
            sys.exit(1)
        parent_number = existing.number
    parent_issue_state = client.get_issue(parent_number)
    if parent_issue_state is not None and not parent_issue_state.open:
        print(
            f"error: parent issue #{parent_number} is closed.\n"
            f"If the plan is done, reclassify it as 'done' in your classification JSON\n"
            f"and re-run 'sync --sources plan' — that is the correct resolution path,\n"
            f"not decompose.",
            file=sys.stderr,
        )
        sys.exit(1)
    plan_path = resolve_plan_path(PLANS_DIR, ARCHIVE_ROOT, args.plan)
    if not plan_path.exists():
        print(f"error: plan doc not found: {plan_path}", file=sys.stderr)
        sys.exit(1)
    result = decompose_plan(
        args.plan, plan_path.read_text(), parent_number, client,
        dry_run=args.dry_run, start_from_task=args.start_from_task,
    )
    print(json.dumps(result, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.kanban_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync", help="reconcile sources onto GitHub Issues")
    sync_parser.add_argument("--sources", required=True, help="comma-separated: worktree,roadmap,plan")
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.add_argument("--plan-classifications", type=Path, default=None)
    sync_parser.set_defaults(func=_cmd_sync)

    backfill_status_parser = sub.add_parser(
        "backfill-status",
        help="one-time: set Project Status=Done on every already-closed issue",
    )
    backfill_status_parser.add_argument("--dry-run", action="store_true")
    backfill_status_parser.set_defaults(func=_cmd_backfill_status)

    push_status_parser = sub.add_parser(
        "push-status",
        help="push one issue's current status onto the Project board right now",
    )
    push_status_parser.add_argument("--issue", type=int, required=True)
    push_status_parser.add_argument("--dry-run", action="store_true")
    push_status_parser.set_defaults(func=_cmd_push_status)

    candidates_parser = sub.add_parser("plan-candidates", help="list plan docs needing classification")
    candidates_parser.set_defaults(func=_cmd_plan_candidates)

    review_tier_parser = sub.add_parser(
        "review-tier",
        help="decide a PR's review tier and count its persisted review artifacts",
    )
    review_tier_parser.add_argument("--pr", type=int, required=True)
    review_tier_parser.add_argument(
        "--exempt", default=None,
        help="mechanical/trivial change (typo, CI re-trigger, a config value "
             "edited exactly as dictated, a revert): records the reason, "
             "requires no artifact",
    )
    review_tier_parser.add_argument(
        "--tier", choices=["A"], default=None,
        help="escalate to Tier A; there is no de-escalation flag by design",
    )
    review_tier_parser.add_argument("--json", action="store_true")
    review_tier_parser.set_defaults(func=_cmd_review_tier)

    decompose_parser = sub.add_parser("decompose-plan", help="create milestone + task sub-issues for one plan")
    decompose_parser.add_argument("--plan", required=True, help="plan doc filename, e.g. 2026-08-27-x.md")
    decompose_parser.add_argument(
        "--parent-issue", type=int, default=None,
        help="target this issue number directly, bypassing plan: marker lookup "
             "(e.g. for a plan whose tracking issue doesn't carry the plan: "
             "marker find_by_marker looks for)",
    )
    decompose_parser.add_argument(
        "--start-from-task", type=int, default=1,
        help="skip creating sub-issues for canonical tasks numbered below this "
             "(for retroactively decomposing a plan already partway through execution)",
    )
    decompose_parser.add_argument("--dry-run", action="store_true")
    decompose_parser.set_defaults(func=_cmd_decompose_plan)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
