"""AQC spec §6.2 names a numbered-plan file path as a ledger-domain identity, but
only .superpowers/sdd/*/progress.md ledgers were ever read - files this repo's
own workflow never creates. These tests cover the plan-doc source: a plan under
docs/superpowers/plans/ with unchecked `- [ ]` tasks is a still-present ledger
signal carrying its unchecked/checked counts and the age of its last commit."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tools.quality_coordination import collect_plan_doc_signals

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
