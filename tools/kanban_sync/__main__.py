"""CLI entrypoint for the kanban board sync (docs/superpowers/specs/
2026-08-26-kanban-board-sync-design.md). `python -m tools.kanban_sync sync
--sources worktree,roadmap,track` runs the fully mechanical sources (used
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

from tools.kanban_sync.github_client import GithubClient
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync.sources_plan import build_plan_items, list_plan_candidates
from tools.kanban_sync.sources_roadmap import parse_roadmap_items
from tools.kanban_sync.sources_tracks import parse_track_items
from tools.kanban_sync.sources_worktree import collect_worktree_items
from tools.kanban_sync.sync import reconcile

REPO = "thesneakattack/kalshi-whale-poc"
ROADMAP_PATH = Path("ROADMAP.md")
ACTIVE_TRACKS_BOARD_PATH = Path("docs/superpowers/plans/2026-08-26-active-tracks-board.md")
PLANS_DIR = Path("docs/superpowers/plans")
KNOWN_SOURCES = frozenset({"worktree", "roadmap", "track", "plan"})


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
# ~4 calls/item: sync_pass_one's find_by_marker + create_issue/set_labels, plus
# sync_pass_two's own find_by_marker + set_labels for depends-on reconciliation.
# A --dry-run never writes, only sync_pass_one's find_by_marker runs, so 1/item.
# Found live 2026-08-27: a 33-item real sync exhausted the 5000/5000 GraphQL
# quota partway through with zero advance warning, leaving fields/labels
# half-applied - and the failure surfaced as a misleading gh CLI error ("unknown
# owner type") rather than anything rate-limit-shaped, only identifiable via
# GH_DEBUG=api. Refusing to start an under-budget run is safer than a partial one.
_ESTIMATED_CALLS_PER_ITEM_WRITE = 4
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


def _collect_items(sources: list[str], plan_classifications: Path | None) -> list[SyncItem]:
    # Checked first, before any source-specific work (including real
    # subprocess calls for worktree/track), so a missing flag fails fast
    # rather than after wasting real `git`/`gh` calls (Task 10 review
    # finding #2).
    if "plan" in sources and plan_classifications is None:
        print("error: --sources plan requires --plan-classifications <path>", file=sys.stderr)
        sys.exit(1)

    items: list[SyncItem] = []
    client = GithubClient(REPO)

    if "worktree" in sources:
        porcelain = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout
        items += collect_worktree_items(porcelain, client)
    if "roadmap" in sources:
        items += parse_roadmap_items(ROADMAP_PATH.read_text())
    if "track" in sources:
        items += parse_track_items(ACTIVE_TRACKS_BOARD_PATH.read_text())
    if "plan" in sources:
        items += build_plan_items(json.loads(plan_classifications.read_text()))

    return items


def _cmd_sync(args: argparse.Namespace) -> None:
    _check_project_scope()
    sources = _parse_sources(args.sources)
    items = _collect_items(sources, args.plan_classifications)
    client = GithubClient(REPO)
    _check_rate_limit_budget(client, len(items), args.dry_run)
    report = reconcile(items, client, dry_run=args.dry_run)

    print(f"created: {len(report.created)}")
    for line in report.created:
        print(f"  + {line}")
    print(f"updated: {len(report.updated)}")
    print(f"closed: {len(report.closed)}")
    print(f"flagged mismatches: {len(report.flagged_mismatches)}")
    for line in report.flagged_mismatches:
        print(f"  ! {line}")


def _cmd_plan_candidates(_args: argparse.Namespace) -> None:
    for path in list_plan_candidates(PLANS_DIR, ACTIVE_TRACKS_BOARD_PATH.read_text()):
        print(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.kanban_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync", help="reconcile sources onto GitHub Issues")
    sync_parser.add_argument("--sources", required=True, help="comma-separated: worktree,roadmap,track,plan")
    sync_parser.add_argument("--dry-run", action="store_true")
    sync_parser.add_argument("--plan-classifications", type=Path, default=None)
    sync_parser.set_defaults(func=_cmd_sync)

    candidates_parser = sub.add_parser("plan-candidates", help="list plan docs needing classification")
    candidates_parser.set_defaults(func=_cmd_plan_candidates)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
