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


def test_check_rate_limit_budget_uses_a_lower_estimate_for_dry_run():
    """A dry run only ever calls find_by_marker (one read per item) - never
    create/edit/close - so it needs far less budget than a real write run for the
    same item count."""
    client = _FakeRateLimitClient(remaining=40)

    cli._check_rate_limit_budget(client, item_count=33, dry_run=True)  # must not raise

    with pytest.raises(SystemExit):
        cli._check_rate_limit_budget(client, item_count=33, dry_run=False)
