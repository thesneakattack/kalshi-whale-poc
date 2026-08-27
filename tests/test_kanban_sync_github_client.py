import json
import subprocess

from tests.support.fake_gh_runner import FakeRunner
from tools.kanban_sync.github_client import GithubCliError, GithubClient

REPO = "thesneakattack/kalshi-whale-poc"


def test_default_runner_captures_stdout_as_text(monkeypatch):
    """A GithubClient built without an explicit runner (the real path, never
    exercised by any FakeRunner-based test above) must still call
    subprocess.run with capture_output=True and text=True - otherwise
    result.stdout is None and every _run() caller's json.loads(stdout)
    crashes on the very first real invocation (found live via Task 13's
    dry-run verification, spec §13)."""
    calls = {}

    def fake_subprocess_run(args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    client = GithubClient(REPO)

    result = client.find_pr_state("some-branch")

    assert result is None
    assert calls["kwargs"].get("capture_output") is True
    assert calls["kwargs"].get("text") is True


def test_find_by_marker_returns_none_when_no_results():
    runner = FakeRunner()
    runner.queue(json.dumps([]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: track:A -->")

    assert result is None
    assert runner.calls[0][:3] == ["gh", "issue", "list"]
    assert "--repo" in runner.calls[0] and REPO in runner.calls[0]


def test_find_by_marker_parses_existing_issue():
    runner = FakeRunner()
    runner.queue(json.dumps([
        {"number": 17, "state": "OPEN", "labels": [{"name": "status:claimable"}, {"name": "type:tracking"}]}
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: worktree:feat/x -->")

    assert result.number == 17
    assert result.open is True
    assert result.labels == frozenset({"status:claimable", "type:tracking"})


def test_create_issue_parses_number_from_returned_url():
    runner = FakeRunner()
    runner.queue("https://github.com/thesneakattack/kalshi-whale-poc/issues/42\n")
    client = GithubClient(REPO, runner=runner)

    result = client.create_issue("Title", "Body", ["status:claimable", "type:tracking"])

    assert result.number == 42
    assert result.open is True
    create_call = runner.calls[0]
    assert create_call[:3] == ["gh", "issue", "create"]
    assert create_call.count("--label") == 2


def test_set_labels_is_a_no_op_when_nothing_to_change():
    runner = FakeRunner()
    client = GithubClient(REPO, runner=runner)

    client.set_labels(5, add=[], remove=[])

    assert runner.calls == []


def test_set_labels_sends_add_and_remove_flags():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.set_labels(5, add=["status:done"], remove=["status:claimable"])

    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "edit"]
    assert "--add-label" in call and "status:done" in call
    assert "--remove-label" in call and "status:claimable" in call


def test_set_labels_creates_a_missing_dynamic_label_then_retries():
    """depends-on:#N labels are created per-dependency, on demand - unlike the fixed
    status:*/type:* set, gh never has them pre-created. Found live (2026-08-27): the
    first real sync against a fresh repo failed outright on the very first depends-on
    edge with `'depends-on:#75' not found`, because gh issue edit --add-label never
    auto-creates a missing label the way gh issue create's --label implicitly can for
    some hosts. set_labels must create the missing label and retry, not just propagate
    the error."""
    runner = FakeRunner()
    runner.queue(
        "",
        returncode=1,
        stderr="failed to update https://github.com/thesneakattack/kalshi-whale-poc/issues/77: 'depends-on:#75' not found",
    )
    runner.queue("")  # gh label create
    runner.queue("")  # retried gh issue edit, now succeeds
    client = GithubClient(REPO, runner=runner)

    client.set_labels(77, add=["depends-on:#75"], remove=[])

    assert runner.calls[0][:3] == ["gh", "issue", "edit"]
    assert runner.calls[1][:3] == ["gh", "label", "create"]
    assert "depends-on:#75" in runner.calls[1]
    assert runner.calls[2][:3] == ["gh", "issue", "edit"]


def test_set_labels_creates_each_missing_label_across_multiple_retries():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="'depends-on:#75' not found")
    runner.queue("")  # create depends-on:#75
    runner.queue("", returncode=1, stderr="'depends-on:#76' not found")
    runner.queue("")  # create depends-on:#76
    runner.queue("")  # retried edit, now succeeds
    client = GithubClient(REPO, runner=runner)

    client.set_labels(77, add=["depends-on:#75", "depends-on:#76"], remove=[])

    label_create_calls = [c for c in runner.calls if c[:3] == ["gh", "label", "create"]]
    assert len(label_create_calls) == 2
    assert any("depends-on:#75" in c for c in label_create_calls)
    assert any("depends-on:#76" in c for c in label_create_calls)


def test_set_labels_propagates_a_real_error_unrelated_to_a_missing_label():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 500: Internal Server Error")
    client = GithubClient(REPO, runner=runner)

    try:
        client.set_labels(5, add=["status:done"], remove=[])
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "500" in str(exc)


def test_close_issue_calls_gh_issue_close():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.close_issue(9)

    assert runner.calls[0][:3] == ["gh", "issue", "close"]
    assert "9" in runner.calls[0]


def test_post_comment_calls_gh_issue_comment_with_body():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.post_comment(9, "hello world")

    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "comment"]
    assert "--body" in call and "hello world" in call


def test_find_pr_state_returns_none_when_no_pr():
    runner = FakeRunner()
    runner.queue(json.dumps([]))
    client = GithubClient(REPO, runner=runner)

    assert client.find_pr_state("feat/no-pr-yet") is None


def test_find_pr_state_returns_state_string():
    runner = FakeRunner()
    runner.queue(json.dumps([{"state": "MERGED"}]))
    client = GithubClient(REPO, runner=runner)

    assert client.find_pr_state("feat/done") == "MERGED"


def test_nonzero_returncode_raises_githubcliierror():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)

    try:
        client.close_issue(999)
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "404" in str(exc)
