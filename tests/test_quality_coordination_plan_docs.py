"""AQC spec §6.2 names a numbered-plan file path as a ledger-domain identity, but
only .superpowers/sdd/*/progress.md ledgers were ever read - files this repo's
own workflow never creates. These tests cover the plan-doc source: a plan under
docs/superpowers/plans/ with unchecked `- [ ]` tasks is a still-present ledger
signal carrying its unchecked/checked counts and the age of its last commit."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tools.quality_coordination import _discover_plan_doc_paths, collect_plan_doc_signals

AT = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def _git_runner_last_commit_days_ago(days: int):
    ts = int(AT.timestamp()) - days * 86400

    def runner(cmd):
        assert cmd[:3] == ["git", "log", "-1"], cmd
        return SimpleNamespace(stdout=f"{ts}\n", returncode=0)

    return runner


def test_plan_with_unchecked_tasks_is_a_still_present_ledger_signal(tmp_path):
    plan = tmp_path / "2026-08-01-example.md"
    plan.write_text("# Plan\n\n- [x] Task 1 done\n- [ ] Task 2 pending\n- [ ] Task 3 pending\n")

    signals = collect_plan_doc_signals(
        [plan], repo_root=tmp_path, git_runner=_git_runner_last_commit_days_ago(9), at=AT,
    )

    assert len(signals) == 1
    s = signals[0]
    assert s.domain == "ledger"
    assert s.identity == "ledger:plan:2026-08-01-example.md"
    assert s.still_present is True
    assert s.payload["unchecked"] == 2 and s.payload["checked"] == 1
    assert s.payload["last_commit_days"] == 9
    assert "Task 2 pending" in s.payload["first_unchecked"]


def test_fully_checked_plan_yields_no_signal(tmp_path):
    plan = tmp_path / "2026-08-01-done.md"
    plan.write_text("# Plan\n\n- [x] Task 1\n- [x] Task 2\n")

    signals = collect_plan_doc_signals(
        [plan], repo_root=tmp_path, git_runner=_git_runner_last_commit_days_ago(1), at=AT,
    )

    assert signals == []


def test_plan_without_any_checkboxes_yields_no_signal(tmp_path):
    plan = tmp_path / "2026-08-01-prose.md"
    plan.write_text("# Just a design note\n\nNo task list here.\n")

    signals = collect_plan_doc_signals(
        [plan], repo_root=tmp_path, git_runner=_git_runner_last_commit_days_ago(1), at=AT,
    )

    assert signals == []


def test_uncommitted_plan_reports_no_commit_age(tmp_path):
    plan = tmp_path / "2026-08-01-new.md"
    plan.write_text("- [ ] Task 1\n")

    def never_committed(cmd):
        return SimpleNamespace(stdout="", returncode=0)

    signals = collect_plan_doc_signals([plan], repo_root=tmp_path, git_runner=never_committed, at=AT)

    assert len(signals) == 1
    assert signals[0].payload["last_commit_days"] is None


"""_discover_plan_doc_paths tests: the 2026-09-06 planning-lanes migration
(docs/superpowers/specs/2026-09-06-planning-lanes-design.md,
docs/superpowers/lanes/step4-file-move-plan.md §4/§6) moves ~246 plan/spec/
research docs out of docs/superpowers/plans/ into
docs/archive/lane-N-<slug>/{plans,specs,research}/, in lane-sized batches
over multiple separate commits - not all at once, and Lane 7 has zero plan
docs so its directory is never created at all. Before this fix,
run_detect_cycle's hardcoded `repo_root / "docs" / "superpowers" / "plans"`
glob (tools/quality_coordination.py:597-598) would silently stop monitoring
any plan doc's staleness/activity signal the moment it moved - a real
regression for the docs still `active` per
docs/superpowers/lanes/step1-plans-classification.md, since collect_plan_doc_
signals (AQC's ongoing-monitoring job, unlike kanban_sync's one-time-
discovery list_plan_candidates - see test_kanban_sync_sources_plan.py) is
specifically meant to keep watching an active plan's checkbox/staleness
state regardless of where the file physically lives."""


def test_discover_finds_plans_in_the_current_superpowers_directory(tmp_path):
    plans_dir = tmp_path / "docs" / "superpowers" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "2026-09-01-x.md").write_text("- [ ] task\n")

    result = _discover_plan_doc_paths(tmp_path)

    assert result == [plans_dir / "2026-09-01-x.md"]


def test_discover_finds_plans_already_moved_into_an_archived_lane(tmp_path):
    lane_plans = tmp_path / "docs" / "archive" / "lane-2-whale-signal-calibration" / "plans"
    lane_plans.mkdir(parents=True)
    (lane_plans / "2026-08-30-whale-confidence-scoring-remediation-implementation.md").write_text(
        "- [ ] task\n"
    )

    result = _discover_plan_doc_paths(tmp_path)

    assert result == [
        lane_plans / "2026-08-30-whale-confidence-scoring-remediation-implementation.md"
    ]


def test_discover_excludes_specs_and_research_subdirectories_in_a_lane(tmp_path):
    """A lane directory keeps the plans/specs/research split (step4 plan §2)
    specifically so plan-doc-ness stays a path segment - specs/research docs
    don't use this domain's `- [ ]` checkbox convention and must never be
    mis-monitored as plan docs just because they sit in the same lane."""
    lane_dir = tmp_path / "docs" / "archive" / "lane-4-analytics-advisory-research"
    (lane_dir / "plans").mkdir(parents=True)
    (lane_dir / "specs").mkdir(parents=True)
    (lane_dir / "research").mkdir(parents=True)
    (lane_dir / "plans" / "2026-08-20-real-plan.md").write_text("- [ ] task\n")
    (lane_dir / "specs" / "2026-08-20-a-spec.md").write_text("- [ ] not a plan\n")
    (lane_dir / "research" / "2026-08-20-research.md").write_text("- [ ] not a plan\n")

    result = _discover_plan_doc_paths(tmp_path)

    assert result == [lane_dir / "plans" / "2026-08-20-real-plan.md"]


def test_discover_combines_and_sorts_across_multiple_lanes_and_the_live_directory(tmp_path):
    live_plans = tmp_path / "docs" / "superpowers" / "plans"
    live_plans.mkdir(parents=True)
    (live_plans / "2026-09-10-new-plan.md").write_text("- [ ] task\n")

    lane1_plans = tmp_path / "docs" / "archive" / "lane-1-kalshi-ingestion" / "plans"
    lane1_plans.mkdir(parents=True)
    (lane1_plans / "2026-08-25-realtime-data-plane-remediation.md").write_text("- [ ] task\n")

    lane6_plans = tmp_path / "docs" / "archive" / "lane-6-observability-quality-safety" / "plans"
    lane6_plans.mkdir(parents=True)
    (lane6_plans / "2026-09-03-tier0-live-incident-remediation.md").write_text("- [ ] task\n")

    result = _discover_plan_doc_paths(tmp_path)

    assert result == sorted([
        live_plans / "2026-09-10-new-plan.md",
        lane1_plans / "2026-08-25-realtime-data-plane-remediation.md",
        lane6_plans / "2026-09-03-tier0-live-incident-remediation.md",
    ])


def test_discover_tolerates_docs_archive_not_existing_yet(tmp_path):
    """The migration hasn't executed at all yet as of this fix - docs/archive/
    doesn't exist in this repo today. Must not crash."""
    live_plans = tmp_path / "docs" / "superpowers" / "plans"
    live_plans.mkdir(parents=True)
    (live_plans / "2026-09-01-x.md").write_text("- [ ] task\n")

    result = _discover_plan_doc_paths(tmp_path)

    assert result == [live_plans / "2026-09-01-x.md"]


def test_discover_tolerates_a_specific_lane_having_no_plans_subdirectory(tmp_path):
    """Lane 7 (Config & control plane) has zero plan docs per step4-file-
    move-plan.md's own §6/Appendix A tally - its directory is deliberately
    never created ("an empty directory for a lane with no archived docs
    would be noise, not signal"). A glob over a lane directory that exists
    but has no plans/ subdirectory (or doesn't exist at all) must not crash
    and must not contribute any paths."""
    archive_root = tmp_path / "docs" / "archive"
    archive_root.mkdir(parents=True)
    # Lane 7's directory itself doesn't exist at all - only unrelated lanes do.
    lane_no_plans = archive_root / "lane-8-frontend-dashboard"
    (lane_no_plans / "specs").mkdir(parents=True)  # specs but no plans subdir

    result = _discover_plan_doc_paths(tmp_path)

    assert result == []


def test_discover_returns_empty_list_when_nothing_exists(tmp_path):
    assert _discover_plan_doc_paths(tmp_path) == []
