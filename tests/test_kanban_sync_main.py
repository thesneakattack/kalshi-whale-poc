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
