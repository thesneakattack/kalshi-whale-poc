import json
import logging
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


def test_find_by_marker_strips_html_comment_delimiters_before_searching():
    """Found live (2026-08-27): GitHub's issue search does not match the literal
    `<!--`/`-->` characters, so searching for the full wrapped marker string always
    returns zero results even for an issue that genuinely has that exact marker in
    its body - verified manually against the real API. Every sync re-run would
    therefore treat every already-created issue as new and create a duplicate. The
    search term must be the marker's inner content only."""
    runner = FakeRunner()
    runner.queue(json.dumps([{
        "number": 48, "state": "OPEN", "labels": [],
        "body": "## Context\n...\n\n<!-- autotrade-sync: roadmap:some-key -->",
    }]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: roadmap:some-key -->")

    assert result.number == 48
    search_call = runner.calls[0]
    search_term = search_call[search_call.index("--search") + 1]
    assert "<!--" not in search_term and "-->" not in search_term
    assert search_term == "autotrade-sync: roadmap:some-key"


def test_find_by_marker_parses_existing_issue():
    runner = FakeRunner()
    runner.queue(json.dumps([
        {
            "number": 17, "state": "OPEN",
            "labels": [{"name": "status:claimable"}, {"name": "type:tracking"}],
            "body": "## Context\n...\n\n<!-- autotrade-sync: worktree:feat/x -->",
        }
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: worktree:feat/x -->")

    assert result.number == 17
    assert result.open is True
    assert result.labels == frozenset({"status:claimable", "type:tracking"})


def test_find_by_marker_skips_a_fuzzy_false_positive_and_returns_the_real_match():
    """Found live (2026-08-30, issue #90): GitHub's issue search is fuzzy
    full-text, not literal - the top-ranked hit can be an unrelated issue
    that merely shares tokens with the marker. A search for one plan's
    marker fuzzy-matched an unrelated roadmap-tracked issue and, trusted
    blindly, caused sync_pass_one to wrongly close it (it was open and the
    plan was classified done). find_by_marker must verify each candidate's
    body actually contains the literal marker before trusting it, not just
    take gh's top result."""
    runner = FakeRunner()
    runner.queue(json.dumps([
        {
            "number": 74, "state": "OPEN", "labels": [],
            "body": "## Context\nAutonomous Quality Coordination (workflow-health)...\n\n"
                    "<!-- autotrade-sync: roadmap:autonomous-quality-coordination-workflow-health -->",
        },
        {
            "number": 278, "state": "OPEN", "labels": [],
            "body": "## Context\n...\n\n<!-- autotrade-sync: plan:2026-08-27-workflow-remediation.md -->",
        },
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: plan:2026-08-27-workflow-remediation.md -->")

    assert result.number == 278


def test_find_by_marker_returns_none_when_no_candidate_body_contains_the_marker():
    runner = FakeRunner()
    runner.queue(json.dumps([
        {"number": 74, "state": "OPEN", "labels": [], "body": "unrelated issue, no marker at all"},
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_by_marker("<!-- autotrade-sync: plan:2026-08-27-workflow-remediation.md -->")

    assert result is None


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


def test_create_issue_creates_a_missing_label_then_retries():
    """Same real risk set_labels already hit (2026-08-27, 'depends-on:#75'
    not found) but on the create path: a brand-new label family's first-ever
    item (e.g. phase:* before it has ever been used) could hit this exact
    failure on gh issue create --label too, not just gh issue edit
    --add-label."""
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="'phase:implementing' not found")
    runner.queue("")  # gh label create
    runner.queue("https://github.com/thesneakattack/kalshi-whale-poc/issues/99\n")
    client = GithubClient(REPO, runner=runner)

    result = client.create_issue("Title", "Body", ["status:claimable", "phase:implementing"])

    assert result.number == 99
    assert runner.calls[0][:3] == ["gh", "issue", "create"]
    assert runner.calls[1][:3] == ["gh", "label", "create"]
    assert "phase:implementing" in runner.calls[1]
    assert runner.calls[2][:3] == ["gh", "issue", "create"]


def test_create_issue_propagates_a_real_error_unrelated_to_a_missing_label():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.create_issue("Title", "Body", ["status:claimable"])
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


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
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.set_labels(5, add=["status:done"], remove=[])
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


def test_close_issue_calls_gh_issue_close():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.close_issue(9)

    assert runner.calls[0][:3] == ["gh", "issue", "close"]
    assert "9" in runner.calls[0]


def test_graphql_rate_limit_parses_remaining_and_reset():
    """Found live 2026-08-27: a 33-item board-population run (item-add + item-edit,
    both GraphQL-backed despite looking like plain CLI flags) silently exhausted the
    5000/5000 GraphQL quota partway through, leaving fields/labels half-applied - and
    surfaced as a misleading 'unknown owner type' error rather than anything
    rate-limit-shaped, confirmed only by re-running with GH_DEBUG=api. A tool that
    bulk-writes to GitHub must check its own budget before starting, not discover
    exhaustion mid-run."""
    runner = FakeRunner()
    runner.queue('{"limit": 5000, "used": 4990, "remaining": 10, "reset": 1787812121}')
    client = GithubClient(REPO, runner=runner)

    remaining, reset = client.graphql_rate_limit()

    assert remaining == 10
    assert reset == 1787812121
    # Not repo-scoped - rate_limit is a global endpoint, must not get --repo appended
    # the way every other _run-based call does.
    assert runner.calls[0] == ["gh", "api", "rate_limit", "--jq", ".resources.graphql"]


def test_graphql_rate_limit_raises_on_a_real_gh_failure():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.graphql_rate_limit()
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


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


def test_list_open_by_label_returns_matching_open_issues_with_body():
    runner = FakeRunner()
    runner.queue(json.dumps([
        {
            "number": 98, "state": "OPEN",
            "labels": [{"name": "type:tracking"}, {"name": "status:in-progress"}],
            "body": "some body <!-- autotrade-sync: worktree:feat/x -->",
        },
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.list_open_by_label("type:tracking")

    assert len(result) == 1
    assert result[0].number == 98
    assert result[0].open is True
    assert result[0].labels == frozenset({"type:tracking", "status:in-progress"})
    assert result[0].body == "some body <!-- autotrade-sync: worktree:feat/x -->"
    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "list"]
    assert "--label" in call and "type:tracking" in call
    assert "--state" in call and "open" in call
    assert "--json" in call and "number,state,labels,body" in call


def test_list_open_by_label_returns_empty_list_when_no_matches():
    runner = FakeRunner()
    runner.queue(json.dumps([]))
    client = GithubClient(REPO, runner=runner)

    result = client.list_open_by_label("type:tracking")

    assert result == []


def test_list_open_by_label_is_not_scoped_by_a_search_term_unlike_find_by_marker():
    runner = FakeRunner()
    runner.queue(json.dumps([]))
    client = GithubClient(REPO, runner=runner)

    client.list_open_by_label("type:tracking")

    assert "--search" not in runner.calls[0]


def test_list_open_by_label_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.list_open_by_label("type:tracking")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


def test_ensure_on_project_calls_gh_project_item_add_with_owner_and_url_not_repo():
    runner = FakeRunner()
    runner.queue(json.dumps({"id": "PVTI_abc123", "content": {"number": 42}}))
    client = GithubClient(REPO, runner=runner)

    result = client.ensure_on_project(42)

    call = runner.calls[0]
    assert call[:3] == ["gh", "project", "item-add"]
    assert "--owner" in call and "thesneakattack" in call
    assert "--url" in call and "https://github.com/thesneakattack/kalshi-whale-poc/issues/42" in call
    assert "--format" in call and "json" in call
    assert "--repo" not in call
    assert result == "PVTI_abc123"


def test_ensure_on_project_returns_the_projects_own_item_id_not_the_issue_id():
    runner = FakeRunner()
    runner.queue(json.dumps({"id": "PVTI_xyz789", "content": {"number": 999}}))
    client = GithubClient(REPO, runner=runner)

    result = client.ensure_on_project(999)

    assert result == "PVTI_xyz789"
    assert result != 999


def test_ensure_on_project_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.ensure_on_project(1)
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


def test_set_project_status_calls_gh_project_item_edit_with_node_ids_not_names():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.set_project_status("PVTI_abc123", "Doing")

    call = runner.calls[0]
    assert call[:3] == ["gh", "project", "item-edit"]
    assert "--id" in call and "PVTI_abc123" in call
    assert "--field-id" in call and "PVTSSF_lAHOAHYiPM4BhmnCzhghwaY" in call
    assert "--project-id" in call and "PVT_kwHOAHYiPM4BhmnC" in call
    assert "--single-select-option-id" in call and "3b0f9f3a" in call
    assert "--field" not in call and "--value" not in call
    assert "--repo" not in call


def test_set_project_status_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.set_project_status("PVTI_abc123", "Doing")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


def test_set_project_status_raises_a_clear_error_for_an_unknown_status_name():
    runner = FakeRunner()
    client = GithubClient(REPO, runner=runner)

    try:
        client.set_project_status("PVTI_abc123", "Bogus")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "Bogus" in str(exc)
    assert runner.calls == []  # never even attempted the gh call


def test_nonzero_returncode_raises_githubcliierror():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)

    try:
        client.close_issue(999)
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "404" in str(exc)


def test_create_milestone_posts_to_the_milestones_endpoint():
    runner = FakeRunner()
    runner.queue(json.dumps({"number": 7, "title": "2026-08-27-some-plan.md"}))
    client = GithubClient(REPO, runner=runner)

    result = client.create_milestone("2026-08-27-some-plan.md")

    assert result == 7
    call = runner.calls[0]
    assert call[:3] == ["gh", "api", "-X"]
    assert "POST" in call
    assert f"repos/{REPO}/milestones" in call
    assert "-f" in call and "title=2026-08-27-some-plan.md" in call


def test_create_milestone_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.create_milestone("x")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


def test_find_milestone_by_title_returns_matching_number():
    runner = FakeRunner()
    runner.queue(json.dumps([
        {"number": 3, "title": "other-plan.md"},
        {"number": 7, "title": "2026-08-27-some-plan.md"},
    ]))
    client = GithubClient(REPO, runner=runner)

    result = client.find_milestone_by_title("2026-08-27-some-plan.md")

    assert result == 7
    call = runner.calls[0]
    assert call[:2] == ["gh", "api"]
    assert f"repos/{REPO}/milestones" in call
    assert "-X" in call and "GET" in call
    assert "state=all" in call


def test_find_milestone_by_title_returns_none_when_no_match():
    runner = FakeRunner()
    runner.queue(json.dumps([{"number": 3, "title": "other-plan.md"}]))
    client = GithubClient(REPO, runner=runner)

    assert client.find_milestone_by_title("2026-08-27-some-plan.md") is None


def test_find_milestone_by_title_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 422: Validation Failed")
    client = GithubClient(REPO, runner=runner)

    try:
        client.find_milestone_by_title("x")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "422" in str(exc)


def test_create_issue_with_parent_and_milestone_adds_both_flags():
    runner = FakeRunner()
    runner.queue("https://github.com/thesneakattack/kalshi-whale-poc/issues/50\n")
    client = GithubClient(REPO, runner=runner)

    result = client.create_issue(
        "Task 1: Title", "Body", ["status:claimable"],
        parent=42, milestone="2026-08-27-some-plan.md",
    )

    assert result.number == 50
    call = runner.calls[0]
    assert "--parent" in call and "42" in call
    assert "--milestone" in call and "2026-08-27-some-plan.md" in call


def test_create_issue_without_parent_or_milestone_omits_both_flags():
    """Backward-compatibility guard: every existing call site (worktree/
    roadmap/track/plan item creation) never passes these - must produce
    the exact same args as before this change."""
    runner = FakeRunner()
    runner.queue("https://github.com/thesneakattack/kalshi-whale-poc/issues/50\n")
    client = GithubClient(REPO, runner=runner)

    client.create_issue("Title", "Body", ["status:claimable"])

    call = runner.calls[0]
    assert "--parent" not in call
    assert "--milestone" not in call


def test_get_sub_issues_summary_returns_completed_and_total():
    runner = FakeRunner()
    runner.queue(json.dumps({"subIssuesSummary": {"completed": 2, "total": 5, "percentCompleted": 40}}))
    client = GithubClient(REPO, runner=runner)

    result = client.get_sub_issues_summary(42)

    assert result == (2, 5)
    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "view"]
    assert "42" in call
    assert "--repo" in call and REPO in call
    assert "--json" in call and "subIssuesSummary" in call


def test_get_sub_issues_summary_propagates_a_real_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)

    try:
        client.get_sub_issues_summary(999)
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "404" in str(exc)


def test_set_milestone_adds_the_milestone_flag():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.set_milestone(42, "2026-08-27-some-plan.md")

    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "edit"]
    assert "--milestone" in call and "2026-08-27-some-plan.md" in call


def test_set_milestone_none_removes_the_milestone():
    runner = FakeRunner()
    runner.queue("")
    client = GithubClient(REPO, runner=runner)

    client.set_milestone(42, None)

    call = runner.calls[0]
    assert "--remove-milestone" in call


def test_get_issue_returns_issue_state_for_open_issue():
    runner = FakeRunner()
    runner.queue(json.dumps({
        "number": 96, "state": "OPEN",
        "labels": [{"name": "status:claimable"}, {"name": "type:plan-task"}],
    }))
    client = GithubClient(REPO, runner=runner)
    result = client.get_issue(96)
    assert result is not None and result.number == 96 and result.open is True
    assert result.labels == frozenset({"status:claimable", "type:plan-task"})
    call = runner.calls[0]
    assert call[:3] == ["gh", "issue", "view"] and "96" in call
    assert "--json" in call and "number,state,labels" in call


def test_get_issue_returns_none_on_cli_error():
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 404: Not Found")
    client = GithubClient(REPO, runner=runner)
    assert client.get_issue(9999) is None


# --- #240: transient upstream failures are retried, real ones are not -------

_GH_504 = "HTTP 504: 504 Gateway Timeout (https://api.github.com/graphql)"


def test_run_retries_a_transient_5xx_with_backoff_then_succeeds(caplog):
    """Found live 2026-08-30: `gh pr list --head feat/netting-decision-columns`
    died on the 504 above and the whole sync aborted half-applied (items
    before it reconciled, items after it not). A 5xx is GitHub's problem, not
    the sync's inputs, and the sync is idempotent (find-or-create by marker),
    so a bounded retry cannot double-create. Each retry is logged so a flaky
    network is visible rather than silent."""
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr=_GH_504)
    runner.queue("", returncode=1, stderr=_GH_504)
    runner.queue(json.dumps([{"state": "MERGED"}]))
    sleeps: list[float] = []
    client = GithubClient(REPO, runner=runner, sleep_fn=sleeps.append)

    with caplog.at_level(logging.WARNING, logger="tools.kanban_sync.github_client"):
        result = client.find_pr_state("feat/netting-decision-columns")

    assert result == "MERGED"
    assert len(runner.calls) == 3
    assert all(call[:3] == ["gh", "pr", "list"] for call in runner.calls)
    assert sleeps == [2.0, 4.0]
    retries = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(retries) == 2
    assert "504" in retries[0] and "retry" in retries[0].lower()


def test_run_does_not_retry_a_4xx_or_auth_error():
    """A 401/404/422 is a real answer about the request; retrying it would
    only delay the same failure by 14s and hide a broken token."""
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr="HTTP 401: Bad credentials (https://api.github.com/graphql)")
    sleeps: list[float] = []
    client = GithubClient(REPO, runner=runner, sleep_fn=sleeps.append)

    try:
        client.find_pr_state("feat/x")
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "401" in str(exc)
    assert len(runner.calls) == 1
    assert sleeps == []


def test_run_raises_the_final_5xx_once_the_retry_budget_is_spent():
    """Bounded, and the last failure is never swallowed: three retries
    (2s/4s/8s), then the error the caller would have seen on day one."""
    runner = FakeRunner()
    for _ in range(4):
        runner.queue("", returncode=1, stderr="HTTP 503: Service Unavailable")
    sleeps: list[float] = []
    client = GithubClient(REPO, runner=runner, sleep_fn=sleeps.append)

    try:
        client.close_issue(9)
        assert False, "expected GithubCliError"
    except GithubCliError as exc:
        assert "503" in str(exc)
    assert len(runner.calls) == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_run_retries_a_gh_network_timeout():
    """gh is a Go binary; a transport timeout surfaces as Go's net/http
    text rather than an HTTP status, and is just as transient."""
    runner = FakeRunner()
    runner.queue("", returncode=1,
                 stderr='Post "https://api.github.com/graphql": net/http: request canceled '
                        "(Client.Timeout exceeded while awaiting headers)")
    runner.queue("")
    sleeps: list[float] = []
    client = GithubClient(REPO, runner=runner, sleep_fn=sleeps.append)

    client.close_issue(9)

    assert len(runner.calls) == 2
    assert sleeps == [2.0]


def test_direct_runner_paths_get_the_same_transient_retry():
    """The project/milestone/rate-limit methods bypass _run to omit --repo;
    a 504 on `gh project item-edit` aborts a sync exactly like the pr-list
    one did, so they share the one retry primitive rather than each
    reinventing it (or, worse, none of them having it)."""
    runner = FakeRunner()
    runner.queue("", returncode=1, stderr=_GH_504)
    runner.queue("")
    sleeps: list[float] = []
    client = GithubClient(REPO, runner=runner, sleep_fn=sleeps.append)

    client.set_project_status("PVTI_abc123", "Doing")

    assert len(runner.calls) == 2
    assert all(call[:3] == ["gh", "project", "item-edit"] for call in runner.calls)
    assert sleeps == [2.0]


def test_default_sleep_is_the_real_clock():
    """No test may ever hit this, but the production default must be a real
    sleep, not a no-op that turns 'backoff' into a hot loop."""
    import time
    client = GithubClient(REPO, runner=FakeRunner())
    assert client._sleep is time.sleep
