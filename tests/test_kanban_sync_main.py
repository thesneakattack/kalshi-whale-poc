import argparse
import subprocess

import pytest

from tools.kanban_sync import __main__ as cli


def test_check_project_scope_exits_when_scope_missing(monkeypatch):
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="gist, read:org, repo", stderr=""),
    )

    with pytest.raises(SystemExit) as exc:
        cli._check_project_scope()

    assert exc.value.code == 1


def test_check_project_scope_passes_when_scope_present(monkeypatch):
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout="gist, project, read:org, repo", stderr=""),
    )

    cli._check_project_scope()  # must not raise


def test_collect_items_errors_when_plan_source_missing_classifications():
    with pytest.raises(SystemExit) as exc:
        cli._collect_items(["plan"], None)

    assert exc.value.code == 1


def test_collect_items_returns_none_live_worktree_branches_when_worktree_not_requested():
    items, live_branches = cli._collect_items([], None)

    assert items == []
    assert live_branches is None


def test_collect_items_returns_live_worktree_branches_when_worktree_requested(monkeypatch):
    # A single monkeypatch of subprocess.run covers both calls:
    # __main__.py's own direct `git worktree list` call, and (transitively,
    # since GithubClient's _default_runner also calls the real subprocess.run)
    # the `gh pr list` call collect_worktree_items makes per branch.
    def fake_run(args, **kwargs):
        if args[:3] == ["git", "worktree", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=(
                "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n"
                "worktree /repo/.claude/worktrees/x\nHEAD def\nbranch refs/heads/feat/x\n"
            ), stderr="")
        if args[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")
        raise AssertionError(f"unexpected call: {args}")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    items, live_branches = cli._collect_items(["worktree"], None)

    assert len(items) == 1
    assert live_branches == {"feat/x"}


def test_sync_subcommand_requires_sources_argument():
    with pytest.raises(SystemExit):
        cli.main(["sync"])


def test_plan_candidates_subcommand_is_registered(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_cmd_plan_candidates", lambda args: called.append(True))

    cli.main(["plan-candidates"])

    assert called == [True]


def test_parse_sources_strips_whitespace_and_rejects_unknown_names():
    # Whitespace after a comma must not silently drop a source.
    assert cli._parse_sources("worktree, roadmap") == ["worktree", "roadmap"]

    # A misspelled/unknown source name must fail loudly, not be ignored.
    with pytest.raises(SystemExit) as exc:
        cli._parse_sources("worktree,bogus")

    assert exc.value.code == 1


def test_collect_items_checks_plan_classifications_before_any_subprocess_call(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not be called before the plan-classifications check")

    monkeypatch.setattr(cli.subprocess, "run", _boom)

    with pytest.raises(SystemExit) as exc:
        cli._collect_items(["plan", "worktree"], None)

    assert exc.value.code == 1


class _FakeRateLimitClient:
    def __init__(self, remaining: int, reset: int = 1787812121) -> None:
        self._remaining = remaining
        self._reset = reset

    def graphql_rate_limit(self):
        return self._remaining, self._reset


def test_check_rate_limit_budget_exits_when_insufficient_for_a_real_run():
    """Found live 2026-08-27: a 33-item real sync (dry_run=False) exhausted the
    quota partway through - each item can cost up to ~4 GraphQL calls
    (find_by_marker + create/edit + a second find_by_marker/set_labels pass for
    depends-on reconciliation). 10 remaining is nowhere near enough for 33 items."""
    client = _FakeRateLimitClient(remaining=10)

    with pytest.raises(SystemExit) as exc:
        cli._check_rate_limit_budget(client, item_count=33, dry_run=False)

    assert exc.value.code == 1


def test_check_rate_limit_budget_passes_when_sufficient():
    client = _FakeRateLimitClient(remaining=5000)

    cli._check_rate_limit_budget(client, item_count=33, dry_run=False)  # must not raise


class _FakeReport:
    def __init__(self):
        self.created = []
        self.updated = []
        self.closed = []
        self.flagged_mismatches = []


def _patch_sync_pipeline(monkeypatch, *, items, live_branches):
    monkeypatch.setattr(cli, "_check_project_scope", lambda: None)
    monkeypatch.setattr(cli, "_collect_items", lambda sources, plan_classifications: (items, live_branches))
    monkeypatch.setattr(cli, "_check_rate_limit_budget", lambda client, item_count, dry_run: None)
    monkeypatch.setattr(cli, "reconcile", lambda items, client, dry_run: _FakeReport())
    monkeypatch.setattr(cli, "GithubClient", lambda repo: object())


def test_cmd_sync_runs_stale_worktree_check_when_worktree_in_sources(monkeypatch):
    calls = []
    _patch_sync_pipeline(monkeypatch, items=[], live_branches={"feat/x"})
    monkeypatch.setattr(
        cli, "close_stale_worktree_issues",
        lambda live_branches, client, dry_run: calls.append(live_branches) or _FakeReport(),
    )

    cli._cmd_sync(argparse.Namespace(sources="worktree", dry_run=False, plan_classifications=None))

    assert calls == [{"feat/x"}]


def test_cmd_sync_skips_stale_worktree_check_when_worktree_not_in_sources(monkeypatch):
    calls = []
    _patch_sync_pipeline(monkeypatch, items=[], live_branches=None)
    monkeypatch.setattr(
        cli, "close_stale_worktree_issues",
        lambda live_branches, client, dry_run: calls.append(live_branches) or _FakeReport(),
    )

    cli._cmd_sync(argparse.Namespace(sources="roadmap", dry_run=False, plan_classifications=None))

    assert calls == []


def test_estimated_calls_per_item_write_reflects_project_status_calls():
    assert cli._ESTIMATED_CALLS_PER_ITEM_WRITE == 6


def test_check_rate_limit_budget_exits_at_the_new_higher_estimate():
    """Proves the constant bump actually changed guard behavior, not just
    its docstring: 33 * 4 (the old estimate's boundary) is no longer
    enough headroom for 33 items at the new per-item cost."""
    client = _FakeRateLimitClient(remaining=33 * 4)

    with pytest.raises(SystemExit) as exc:
        cli._check_rate_limit_budget(client, item_count=33, dry_run=False)

    assert exc.value.code == 1


def test_check_rate_limit_budget_uses_a_lower_estimate_for_dry_run():
    """A dry run only ever calls find_by_marker (one read per item) - never
    create/edit/close - so it needs far less budget than a real write run for the
    same item count."""
    client = _FakeRateLimitClient(remaining=40)

    cli._check_rate_limit_budget(client, item_count=33, dry_run=True)  # must not raise

    with pytest.raises(SystemExit):
        cli._check_rate_limit_budget(client, item_count=33, dry_run=False)


class _FakeNoIssueClient:
    def find_by_marker(self, marker):
        return None


def test_decompose_plan_subcommand_errors_when_plan_issue_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_check_project_scope", lambda: None)
    monkeypatch.setattr(cli, "GithubClient", lambda repo: _FakeNoIssueClient())
    monkeypatch.setattr(cli, "PLANS_DIR", tmp_path)
    (tmp_path / "x.md").write_text("# X\n")

    with pytest.raises(SystemExit) as exc:
        cli.main(["decompose-plan", "--plan", "x.md"])

    assert exc.value.code == 1


def test_cmd_sync_runs_close_completed_plan_parents_when_plan_in_sources(monkeypatch):
    calls = []
    _patch_sync_pipeline(monkeypatch, items=[], live_branches=None)
    monkeypatch.setattr(
        cli, "close_completed_plan_parents",
        lambda client, dry_run: calls.append(True) or _FakeReport(),
    )

    cli._cmd_sync(argparse.Namespace(sources="plan", dry_run=False, plan_classifications=None))

    assert calls == [True]


def test_cmd_sync_skips_close_completed_plan_parents_when_plan_not_in_sources(monkeypatch):
    calls = []
    _patch_sync_pipeline(monkeypatch, items=[], live_branches=None)
    monkeypatch.setattr(
        cli, "close_completed_plan_parents",
        lambda client, dry_run: calls.append(True) or _FakeReport(),
    )

    cli._cmd_sync(argparse.Namespace(sources="roadmap", dry_run=False, plan_classifications=None))

    assert calls == []
