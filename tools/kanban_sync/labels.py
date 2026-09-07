"""Canonical label constants (docs/archive/lane-9-tooling-ci-process-governance/specs/2026-08-26-kanban-board-sync-design.md §7). status:* values are copied verbatim from the
installed github-issues-kanban skill's assets/label-scheme.json - do not
invent new status values. type:* is this repo's own extension, originally
established by a since-scrapped autonomous-engineering-mode design (never
implemented, its docs removed 2026-09-07); type:tracking is this plan's own
addition to that same repo-local family, for issues that track a worktree
rather than represent claimable work.
"""
from __future__ import annotations

STATUS_CLAIMABLE = "status:claimable"
STATUS_CLAIMED = "status:claimed"
STATUS_IN_PROGRESS = "status:in-progress"
STATUS_READY_FOR_REVIEW = "status:ready-for-review"
STATUS_BLOCKED = "status:blocked"
STATUS_DONE = "status:done"

ALL_STATUS_LABELS = frozenset({
    STATUS_CLAIMABLE, STATUS_CLAIMED, STATUS_IN_PROGRESS,
    STATUS_READY_FOR_REVIEW, STATUS_BLOCKED, STATUS_DONE,
})

TYPE_BUG = "type:bug"
TYPE_INVESTIGATION = "type:investigation"
TYPE_PLAN_TASK = "type:plan-task"
TYPE_FEATURE = "type:feature"
TYPE_DESIGN = "type:design"
TYPE_TRACKING = "type:tracking"

# phase:* (2026-08-27, direct request: "kanban columns groupable by these
# plans/worktrees' phases") - a repo-local family, same status as type:*
# above, not part of the installed skill's own label-scheme.json. Optional
# on SyncItem (not every source can determine one - see each source's own
# phase-detection docstring for what it can and can't infer). Renamed
# 2026-08-27 to match superpowers' own lifecycle vocabulary (brainstorming/
# spec/plan/implementing/verification/done) rather than the original
# ad hoc wording - see docs/archive/lane-9-tooling-ci-process-governance/specs/2026-08-27-kanban-sync-project-status-field-design.md §4.1. Label-based, NOT because this
# repo's board can group its view by Labels - it can't: GitHub Projects V2
# board/table views can only be grouped by a single-select or iteration
# *field* on the Project itself, confirmed against GitHub's own current
# docs 2026-08-27 (a prior version of this comment claimed the opposite;
# that was wrong). phase:* exists as issue metadata/filtering, not as the
# mechanism that produces the board's visible columns - that's
# project_status.py's job instead. PHASE_IMPLEMENTING/PHASE_VERIFICATION
# are worktree-only, not general: a worktree already maps 1:1 to one
# branch, but a plan/track item does not reliably correlate to a branch by
# name (see the design doc's §4.1 for the empirical branch-name mismatches
# that ruled this out for plan/track items). No PHASE_BRAINSTORMING
# constant: an idea with neither a research doc nor a spec doc has nothing
# in the repo to detect it from, so this tool can only ever label an
# initiative once it exists as text somewhere.
PHASE_RESEARCH = "phase:research"
PHASE_SPEC = "phase:spec"
PHASE_PLAN = "phase:plan"
PHASE_IMPLEMENTING = "phase:implementing"
PHASE_VERIFICATION = "phase:verification"
PHASE_DONE = "phase:done"

ALL_PHASE_LABELS = frozenset({
    PHASE_RESEARCH, PHASE_SPEC, PHASE_PLAN,
    PHASE_IMPLEMENTING, PHASE_VERIFICATION, PHASE_DONE,
})

SYNC_MARKER_KIND_WORKTREE = "worktree"
SYNC_MARKER_KIND_ROADMAP = "roadmap"
SYNC_MARKER_KIND_PLAN = "plan"

# lane:* / concern:* (2026-09-06, docs/archive/lane-9-tooling-ci-process-governance/specs/2026-09-06-planning-lanes-design.md §3/§8 rule 5) - the planning-lanes migration's own
# vocabulary, replacing the retired area:* labels. LANES is the single
# source of truth for "closest primary fit" (lane number -> name -> primary
# package list, verbatim from the design's §3 table); CONCERNS mirrors it
# for cross-cutting properties that aren't lanes (currently just
# concern:hotpath - CLAUDE.md's data-plane HARD RULE class of defect,
# sync-on-event-loop / blocking I/O on a hot path). An issue/PR carries
# exactly one lane:N label plus zero or more concern:* labels (§3's own
# "cross-cutting concerns are not lanes" rule) - this module only defines
# the label strings and the lane->package map; which one applies to a given
# issue is a judgment call made against that issue's own content, same as
# every other label family here.
CONCERN_HOTPATH = "concern:hotpath"

CONCERNS = {
    "hotpath": CONCERN_HOTPATH,
}

LANES = {
    1: {
        "label": "lane:1",
        "name": "Kalshi & index data ingestion",
        "packages": [
            "services/kalshi/", "market_catalog/", "market_events/",
            "market_watch/", "whale_stream/whale_stream_handlers.py",
            "whale_stream/index_stream_handlers.py", "index_feed/ingestion.py",
            "index_feed/backfill.py", "series_cache.py", "title_cache.py",
            "series_evaluator.py", "game_state.py", "market_history.py",
            "series_watcher.py",
        ],
    },
    2: {
        "label": "lane:2",
        "name": "Whale signal detection & calibration",
        "packages": [
            "whalewatchers/", "whalewatchers/kalshi_trade_tape.py",
            "whale_simulator.py", "confidence_scoring.py",
            "whale_gate.py", "whale_calibration/", "signal_log.py",
            "whale_stream/decision_bridge.py", "candidate_retry.py",
        ],
    },
    3: {
        "label": "lane:3",
        "name": "Strategy, risk & execution",
        "packages": [
            "strategy_engine.py", "exits/", "risk_manager.py", "paper_broker.py",
            "execution.py", "shadow_mode.py", "position/", "mutual_exclusivity.py",
            "settlement_edge_entry.py", "settlement_resolver.py", "kalshi_fees.py",
        ],
    },
    4: {
        "label": "lane:4",
        "name": "Analytics, advisory & research",
        "packages": [
            "analytics/", "advisory/", "backtest/", "history/", "research/",
            "stats_power.py", "market_analyst_agent/", "settlement_edge.py",
            "candidate_log.py", "index_feed/settlement_algebra.py",
            "candidate_ledger.py", "trade_category.py", "services/config/config_performance.py",
            "ml_feed.py", "data_quarantine.py",
        ],
    },
    5: {
        "label": "lane:5",
        "name": "Runtime infrastructure",
        "packages": [
            "capture_writer.py", "task_supervisor.py", "loop_watchdog.py",
            "tick_executor.py", "http_client.py", "db.py", "pagination.py",
            "fault_log.py", "app_state.py", "state_view.py", "market_lookup.py",
            "auth.py", "accounts_store.py", "logging_config.py",
        ],
    },
    6: {
        "label": "lane:6",
        "name": "Observability, quality & safety infra",
        "packages": [
            "quality/", "observability/", "storage_health/", "diagnostics/",
            "alerting/", "backup/", "reset/", "latency_agg.py",
            "whale_pipeline_perf.py",
        ],
    },
    7: {
        "label": "lane:7",
        "name": "Config & control plane",
        "packages": [
            "services/config/", "services/config/routes.py",
            "services/config/config_paths.py", "services/config/config_store.py",
            "services/config/config_bounds.py", "services/config/config_overrides.py",
            "config/settings.yaml",
        ],
    },
    8: {
        "label": "lane:8",
        "name": "Frontend & dashboard",
        "packages": ["frontend/", "static/", "history_push.py", "ws_manager.py"],
    },
    9: {
        "label": "lane:9",
        "name": "Tooling, CI & process governance",
        "packages": [
            "tools/", ".claude/rules/", ".claude/skills/", ".claude/hooks/",
            ".github/workflows/", ".woodpecker/", "scripts/", "tests/",
            "bench/", "ui_samples/",
        ],
    },
}

ALL_LANE_LABELS = frozenset(lane["label"] for lane in LANES.values())
