import subprocess
from datetime import datetime, timezone
from pathlib import Path

from tests.support.synthetic_git_repo import make_synthetic_repo
from tools import coordination_engine as ce
from tools.quality_coordination import (
    _CLEANUP_ACTION_FOR_DOMAIN,
    delete_merged_branch,
    prune_worktrees,
    run_cleanup_actions,
)

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def _real_runner(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return result


def test_delete_merged_branch_refuses_when_not_an_ancestor_of_main(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/unmerged"], cwd=repo)
    (repo / "x.txt").write_text("x")
    subprocess.run(["git", "add", "x.txt"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "unmerged work"], cwd=repo)
    subprocess.run(["git", "checkout", "main"], cwd=repo)

    outcome = delete_merged_branch(
        "feat/unmerged", git_runner=lambda a: _real_runner(a, repo), main_branch="main",
    )

    assert outcome.startswith("refused:")
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "feat/unmerged" in branches  # still there - refusal did not delete it


def test_delete_merged_branch_succeeds_when_truly_merged(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/merged"], cwd=repo)
    (repo / "y.txt").write_text("y")
    subprocess.run(["git", "add", "y.txt"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "merged work"], cwd=repo)
    subprocess.run(["git", "checkout", "main"], cwd=repo)
    subprocess.run(["git", "merge", "--no-ff", "feat/merged", "-m", "merge"], cwd=repo)

    outcome = delete_merged_branch(
        "feat/merged", git_runner=lambda a: _real_runner(a, repo), main_branch="main",
    )

    assert outcome == "succeeded"
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "feat/merged" not in branches


def test_delete_merged_branch_never_targets_main(tmp_path):
    repo = make_synthetic_repo(tmp_path)

    outcome = delete_merged_branch(
        "main", git_runner=lambda a: _real_runner(a, repo), main_branch="main",
    )

    assert outcome == "refused:protected-main"


def test_delete_merged_branch_is_idempotent(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feat/merged2"], cwd=repo)
    (repo / "z.txt").write_text("z")
    subprocess.run(["git", "add", "z.txt"], cwd=repo)
    subprocess.run(["git", "commit", "-m", "merged work 2"], cwd=repo)
    subprocess.run(["git", "checkout", "main"], cwd=repo)
    subprocess.run(["git", "merge", "--no-ff", "feat/merged2", "-m", "merge"], cwd=repo)
    runner = lambda a: _real_runner(a, repo)

    first = delete_merged_branch("feat/merged2", git_runner=runner, main_branch="main")
    second = delete_merged_branch("feat/merged2", git_runner=runner, main_branch="main")

    assert first == "succeeded"
    assert second.startswith("refused:")  # already gone - not an error, just a no-op refusal


def test_prune_worktrees_reconciles_bookkeeping_only(tmp_path):
    repo = make_synthetic_repo(tmp_path)
    runner = lambda a: _real_runner(a, repo)

    result = prune_worktrees(git_runner=runner, repo_root=repo)

    assert result == "succeeded"  # git worktree prune has no destructive precondition to refuse on


def test_run_cleanup_actions_logs_every_attempt_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "DB_PATH", tmp_path / "q.db")
    conn = ce._connect()
    repo = make_synthetic_repo(tmp_path)
    runner = lambda a: _real_runner(a, repo)

    run_cleanup_actions(
        [("branch:feat/x", "worktree_prune")], git_runner=runner, repo_root=repo,
        sdd_root=tmp_path / "sdd", conn=conn, at=AT, dry_run=True,
    )

    row = conn.execute("SELECT * FROM cleanup_actions").fetchone()
    assert row["action_type"] == "worktree_prune"
    assert bool(row["dry_run"]) is True


def test_branch_domain_no_longer_has_an_automated_cleanup_action():
    """Retired 2026-08-30 (issue #90's fix-out): this module's first-ever real --clean
    run found 0 cleanup_actions rows across 11 prior detect cycles and 20 total
    escalations - every branch signal that ever escalated had already been deleted
    through the normal `gh pr merge --delete-branch`/scripts/cleanup-worktrees.sh path
    first. main()'s eligible-action selection (`identity.split(":", 1)[0] in
    _CLEANUP_ACTION_FOR_DOMAIN`) is keyed off this dict, so an empty "branch" entry is
    what actually stops --clean from ever calling delete_merged_branch again -
    delete_merged_branch() itself stays defined and tested, just unreachable from main()."""
    assert "branch" not in _CLEANUP_ACTION_FOR_DOMAIN
