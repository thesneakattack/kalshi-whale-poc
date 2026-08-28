from tools.kanban_sync import labels
from tools.kanban_sync.github_client import IssueState
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync import project_status
from tools.kanban_sync.sync import close_completed_plan_parents, close_stale_worktree_issues, reconcile, sync_pass_one, _mismatch_comment


class FakeGithubClient:
    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self._next_number = 1
        self.comments: list[tuple[int, str]] = []
        self.project_items: dict[int, str] = {}   # issue number -> fake project item id
        self.project_status: dict[int, str] = {}  # issue number -> current Status option name
        self.project_status_calls = 0             # call counter, for "was it called again" assertions
        self.sub_issues_summary: dict[int, tuple[int, int]] = {}

    def find_by_marker(self, marker):
        for number, issue in self.issues.items():
            if marker in issue["body"]:
                return IssueState(number=number, open=issue["open"], labels=frozenset(issue["labels"]))
        return None

    def list_open_by_label(self, label):
        return [
            IssueState(number=number, open=True, labels=frozenset(issue["labels"]), body=issue["body"])
            for number, issue in self.issues.items()
            if issue["open"] and label in issue["labels"]
        ]

    def create_issue(self, title, body, labels_):
        number = self._next_number
        self._next_number += 1
        self.issues[number] = {"title": title, "body": body, "labels": set(labels_), "open": True}
        return IssueState(number=number, open=True, labels=frozenset(labels_))

    def set_labels(self, number, add, remove):
        issue = self.issues[number]
        issue["labels"] = (issue["labels"] | set(add)) - set(remove)

    def close_issue(self, number):
        self.issues[number]["open"] = False

    def post_comment(self, number, body):
        self.comments.append((number, body))

    def ensure_on_project(self, issue_number):
        self.project_items.setdefault(issue_number, str(issue_number))
        return self.project_items[issue_number]

    def set_project_status(self, item_id, status):
        self.project_status[int(item_id)] = status
        self.project_status_calls += 1

    def get_sub_issues_summary(self, issue_number):
        return self.sub_issues_summary.get(issue_number, (0, 0))


def _item(kind="track", key="A", *, title="Track A", status=labels.STATUS_CLAIMABLE,
          done=False, depends_on=(), phase=None, type_label=labels.TYPE_TRACKING):
    return SyncItem(
        kind=kind, key=key, title=title, status_label=status,
        type_label=type_label, context_body="## Context\nx",
        acceptance_criteria=("done when x happens",), done=done,
        depends_on_keys=depends_on, phase_label=phase,
    )


def test_sync_pass_one_creates_new_issue_for_new_item():
    client = FakeGithubClient()

    _, report = sync_pass_one([_item()], client, dry_run=False)

    assert len(client.issues) == 1
    assert report.created and "Track A" in report.created[0]


def test_sync_pass_one_is_idempotent_no_duplicate_on_second_run():
    client = FakeGithubClient()
    sync_pass_one([_item()], client, dry_run=False)

    _, report = sync_pass_one([_item()], client, dry_run=False)

    assert len(client.issues) == 1
    assert report.created == []


def test_sync_pass_one_closes_issue_when_item_becomes_done():
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)

    _, report = sync_pass_one([_item(done=True)], client, dry_run=False)

    (issue,) = client.issues.values()
    assert issue["open"] is False
    assert report.closed


def test_sync_pass_one_does_not_reopen_manually_closed_issue():
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)
    (number,) = client.issues.keys()
    client.close_issue(number)  # simulate a human closing it by hand

    _, report = sync_pass_one([_item(done=False)], client, dry_run=False)  # source still open

    assert client.issues[number]["open"] is False
    assert report.flagged_mismatches
    assert client.comments and "sync-mismatch" in client.comments[0][1]


def test_sync_pass_one_skips_creating_issue_for_item_already_done():
    client = FakeGithubClient()

    _, report = sync_pass_one([_item(done=True)], client, dry_run=False)

    assert client.issues == {}
    assert report.created == []


def test_sync_pass_one_dry_run_makes_no_mutating_calls():
    client = FakeGithubClient()

    _, report = sync_pass_one([_item()], client, dry_run=True)

    assert client.issues == {}
    assert report.dry_run is True
    assert report.created


def test_sync_pass_one_new_issue_includes_phase_label():
    client = FakeGithubClient()

    sync_pass_one([_item(phase=labels.PHASE_RESEARCH)], client, dry_run=False)

    (issue,) = client.issues.values()
    assert labels.PHASE_RESEARCH in issue["labels"]


def test_sync_pass_one_no_phase_label_added_when_item_has_none():
    client = FakeGithubClient()

    sync_pass_one([_item(phase=None)], client, dry_run=False)

    (issue,) = client.issues.values()
    assert not (issue["labels"] & labels.ALL_PHASE_LABELS)


def test_sync_pass_one_replaces_stale_phase_label_on_update():
    client = FakeGithubClient()
    sync_pass_one([_item(phase=labels.PHASE_RESEARCH)], client, dry_run=False)

    _, report = sync_pass_one([_item(phase=labels.PHASE_SPEC)], client, dry_run=False)

    (issue,) = client.issues.values()
    assert issue["labels"] & labels.ALL_PHASE_LABELS == {labels.PHASE_SPEC}
    assert report.updated


def test_sync_pass_one_leaves_phase_label_alone_when_unchanged():
    """No spurious update report when the phase hasn't actually changed -
    same idempotency guarantee this file's own status/depends-on tests
    already prove for those two label families."""
    client = FakeGithubClient()
    sync_pass_one([_item(phase=labels.PHASE_SPEC)], client, dry_run=False)

    _, report = sync_pass_one([_item(phase=labels.PHASE_SPEC)], client, dry_run=False)

    assert report.updated == []


def test_sync_pass_one_sets_project_status_for_newly_created_item():
    client = FakeGithubClient()

    sync_pass_one([_item(status=labels.STATUS_CLAIMABLE)], client, dry_run=False)

    (number,) = client.issues.keys()
    assert client.project_status[number] == project_status.STATUS_NEXT


def test_sync_pass_one_maps_status_claimed_to_doing():
    client = FakeGithubClient()

    sync_pass_one([_item(status=labels.STATUS_CLAIMED)], client, dry_run=False)

    (number,) = client.issues.keys()
    assert client.project_status[number] == project_status.STATUS_DOING


def test_sync_pass_one_maps_status_in_progress_to_doing():
    client = FakeGithubClient()

    sync_pass_one([_item(status=labels.STATUS_IN_PROGRESS)], client, dry_run=False)

    (number,) = client.issues.keys()
    assert client.project_status[number] == project_status.STATUS_DOING


def test_sync_pass_one_maps_status_ready_for_review_to_waiting():
    client = FakeGithubClient()

    sync_pass_one([_item(status=labels.STATUS_READY_FOR_REVIEW)], client, dry_run=False)

    (number,) = client.issues.keys()
    assert client.project_status[number] == project_status.STATUS_WAITING


def test_sync_pass_one_maps_status_blocked_to_waiting():
    client = FakeGithubClient()

    sync_pass_one([_item(status=labels.STATUS_BLOCKED)], client, dry_run=False)

    (number,) = client.issues.keys()
    assert client.project_status[number] == project_status.STATUS_WAITING


def test_sync_pass_one_sets_project_status_to_done_when_item_becomes_done_and_issue_closes():
    """item.done is checked BEFORE item.status_label - necessary because
    sources_tracks.py always sets status_label=STATUS_CLAIMABLE regardless
    of done (unlike sources_roadmap.py/sources_plan.py). Mapping via
    status_label alone would land a just-finished track on "Next" instead
    of "Done"."""
    client = FakeGithubClient()
    sync_pass_one([_item(status=labels.STATUS_CLAIMABLE, done=False)], client, dry_run=False)
    (number,) = client.issues.keys()

    # done=True but status_label still STATUS_CLAIMABLE, matching
    # sources_tracks.py's real (if odd) behavior.
    sync_pass_one([_item(status=labels.STATUS_CLAIMABLE, done=True)], client, dry_run=False)

    assert client.project_status[number] == project_status.STATUS_DONE


def test_sync_pass_one_does_not_touch_project_status_for_an_already_closed_done_item_on_a_later_run():
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)
    sync_pass_one([_item(done=True)], client, dry_run=False)  # closes it, sets project status
    calls_after_close = client.project_status_calls

    sync_pass_one([_item(done=True)], client, dry_run=False)  # already closed, still done

    assert client.project_status_calls == calls_after_close


def test_sync_pass_one_reapplies_project_status_every_run_even_when_labels_unchanged():
    """Proves the deliberate unconditional/self-healing choice: gating on
    "did the label change this run" would mean this never fires for any
    pre-existing item whose label already matches what's computed today."""
    client = FakeGithubClient()
    sync_pass_one([_item()], client, dry_run=False)
    calls_after_first = client.project_status_calls

    sync_pass_one([_item()], client, dry_run=False)  # identical input, no label diff

    assert client.project_status_calls > calls_after_first


def test_sync_pass_one_overwrites_a_manually_set_project_status_to_match_computed_status():
    client = FakeGithubClient()
    sync_pass_one([_item(status=labels.STATUS_CLAIMABLE)], client, dry_run=False)
    (number,) = client.issues.keys()
    client.project_status[number] = "Waiting"  # simulate a human dragging the card

    sync_pass_one([_item(status=labels.STATUS_CLAIMABLE)], client, dry_run=False)

    assert client.project_status[number] == project_status.STATUS_NEXT


def test_sync_pass_one_dry_run_never_touches_the_project():
    client = FakeGithubClient()

    sync_pass_one([_item()], client, dry_run=True)

    assert client.project_items == {}
    assert client.project_status == {}


def test_sync_pass_one_does_not_sync_project_status_for_a_manually_closed_issue_with_still_open_source():
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)
    (number,) = client.issues.keys()
    client.close_issue(number)  # simulate a human closing it by hand
    calls_before = client.project_status_calls

    sync_pass_one([_item(done=False)], client, dry_run=False)  # source still open

    assert client.project_status_calls == calls_before


def test_sync_pass_one_does_not_clobber_status_of_an_active_claim():
    """The github-issues-kanban plugin skill claims an issue by swapping
    status:claimable for status:claimed + claimed-by:<agent> +
    claim-expires:<iso-ts> (its lock-protocol.md). A later sync must not
    silently revert that back to whatever the source's own status_label
    computes, or it fights the plugin instead of coexisting with it."""
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)  # status:claimable
    (number,) = client.issues.keys()
    client.set_labels(
        number,
        ["status:claimed", "claimed-by:test-agent", "claim-expires:2099-01-01T00:00:00Z"],
        ["status:claimable"],
    )

    sync_pass_one([_item(done=False)], client, dry_run=False)  # source still says claimable

    assert "status:claimed" in client.issues[number]["labels"]
    assert "status:claimable" not in client.issues[number]["labels"]
    assert "claimed-by:test-agent" in client.issues[number]["labels"]


def test_sync_pass_one_does_not_touch_project_status_during_an_active_claim():
    """The plugin never touches Project fields itself (labels + comments
    only) - once sync defers to a claim, the safest behavior is to leave
    the Project Status column exactly as it was, not guess a new value."""
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)
    (number,) = client.issues.keys()
    client.set_labels(
        number,
        ["status:claimed", "claimed-by:test-agent", "claim-expires:2099-01-01T00:00:00Z"],
        ["status:claimable"],
    )
    calls_before = client.project_status_calls

    sync_pass_one([_item(done=False)], client, dry_run=False)

    assert client.project_status_calls == calls_before


def test_sync_pass_one_still_reconciles_status_once_a_claim_has_expired():
    """A claim past its own TTL is stale by the plugin's own model
    ("stale claims auto-release on next dispatch cycle") - sync should
    not treat an expired claim as still protecting the status label."""
    client = FakeGithubClient()
    sync_pass_one([_item(done=False)], client, dry_run=False)
    (number,) = client.issues.keys()
    client.set_labels(
        number,
        ["status:claimed", "claimed-by:test-agent", "claim-expires:2020-01-01T00:00:00Z"],
        ["status:claimable"],
    )

    sync_pass_one([_item(done=False)], client, dry_run=False)

    assert "status:claimable" in client.issues[number]["labels"]
    assert "status:claimed" not in client.issues[number]["labels"]


def test_reconcile_sets_depends_on_label_using_real_issue_number():
    client = FakeGithubClient()
    track_a = _item(kind="track", key="A", title="Track A")
    track_c = _item(kind="track", key="C", title="Track C", depends_on=(("track", "A"),))

    reconcile([track_a, track_c], client)

    c_number = [n for n, i in client.issues.items() if i["title"] == "Track C"][0]
    a_number = [n for n, i in client.issues.items() if i["title"] == "Track A"][0]
    assert f"depends-on:#{a_number}" in client.issues[c_number]["labels"]


def test_reconcile_second_run_does_not_re_add_existing_depends_on_label():
    client = FakeGithubClient()
    track_a = _item(kind="track", key="A", title="Track A")
    track_c = _item(kind="track", key="C", title="Track C", depends_on=(("track", "A"),))
    reconcile([track_a, track_c], client)

    reconcile([track_a, track_c], client)  # second run, same input

    c_number = [n for n, i in client.issues.items() if i["title"] == "Track C"][0]
    depends_labels = [l for l in client.issues[c_number]["labels"] if l.startswith("depends-on:#")]
    assert len(depends_labels) == 1  # not duplicated


def test_reconcile_skips_depends_on_for_dependency_not_yet_created():
    client = FakeGithubClient()
    track_c = _item(kind="track", key="C", title="Track C", depends_on=(("track", "A"),))

    report = reconcile([track_c], client)  # Track A never in this run's item list

    (number,) = client.issues.keys()
    assert not [l for l in client.issues[number]["labels"] if l.startswith("depends-on:#")]


def _worktree_item(branch="feat/x", *, status=labels.STATUS_IN_PROGRESS):
    return _item(
        kind="worktree", key=branch, title=f"Worktree: {branch}",
        status=status,
    )


def test_close_stale_worktree_issues_closes_issue_whose_branch_is_no_longer_live():
    client = FakeGithubClient()
    sync_pass_one([_worktree_item()], client, dry_run=False)
    (number,) = client.issues.keys()

    report = close_stale_worktree_issues(live_branches=set(), client=client, dry_run=False)

    assert client.issues[number]["open"] is False
    assert report.closed
    assert client.comments and "feat/x" in client.comments[0][1]


def test_close_stale_worktree_issues_leaves_issue_open_when_branch_still_live():
    client = FakeGithubClient()
    sync_pass_one([_worktree_item()], client, dry_run=False)
    (number,) = client.issues.keys()

    report = close_stale_worktree_issues(live_branches={"feat/x"}, client=client, dry_run=False)

    assert client.issues[number]["open"] is True
    assert report.closed == []


def test_close_stale_worktree_issues_ignores_issue_whose_marker_kind_is_not_worktree():
    client = FakeGithubClient()
    sync_pass_one([_item(kind="track", key="A", title="Track A")], client, dry_run=False)

    report = close_stale_worktree_issues(live_branches=set(), client=client, dry_run=False)

    assert report.closed == []
    assert all(issue["open"] for issue in client.issues.values())


def test_close_stale_worktree_issues_ignores_issue_whose_body_has_no_marker():
    client = FakeGithubClient()
    client.issues[1] = {
        "title": "Manual", "body": "no marker here",
        "labels": {labels.TYPE_TRACKING}, "open": True,
    }

    report = close_stale_worktree_issues(live_branches=set(), client=client, dry_run=False)

    assert report.closed == []
    assert client.issues[1]["open"] is True


def test_close_stale_worktree_issues_dry_run_makes_no_mutating_calls():
    client = FakeGithubClient()
    sync_pass_one([_worktree_item()], client, dry_run=False)
    (number,) = client.issues.keys()

    report = close_stale_worktree_issues(live_branches=set(), client=client, dry_run=True)

    assert client.issues[number]["open"] is True
    assert client.comments == []
    assert report.closed  # still reported, matching sync_pass_one's own dry-run convention


def test_close_stale_worktree_issues_does_not_touch_a_manually_closed_tracking_issue():
    client = FakeGithubClient()
    sync_pass_one([_worktree_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.close_issue(number)  # simulate a human closing it by hand

    report = close_stale_worktree_issues(live_branches=set(), client=client, dry_run=False)

    assert client.issues[number]["open"] is False
    assert client.comments == []
    assert report.closed == []


def _plan_item(key="x.md", *, title="Plan: x.md"):
    return _item(kind="plan", key=key, title=title, status=labels.STATUS_CLAIMABLE,
                 type_label=labels.TYPE_PLAN_TASK)


def test_close_completed_plan_parents_closes_issue_whose_sub_issues_are_all_done():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (3, 3)

    report = close_completed_plan_parents(client, dry_run=False)

    assert client.issues[number]["open"] is False
    assert report.closed


def test_close_completed_plan_parents_leaves_issue_open_when_sub_issues_incomplete():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (2, 3)

    report = close_completed_plan_parents(client, dry_run=False)

    assert client.issues[number]["open"] is True
    assert report.closed == []


def test_close_completed_plan_parents_ignores_issue_with_no_sub_issues():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (0, 0)

    report = close_completed_plan_parents(client, dry_run=False)

    assert client.issues[number]["open"] is True
    assert report.closed == []


def test_close_completed_plan_parents_dry_run_makes_no_mutating_calls():
    client = FakeGithubClient()
    sync_pass_one([_plan_item()], client, dry_run=False)
    (number,) = client.issues.keys()
    client.sub_issues_summary[number] = (3, 3)

    report = close_completed_plan_parents(client, dry_run=True)

    assert client.issues[number]["open"] is True
    assert report.closed  # still reported, matching every other dry-run in this file


def test_close_completed_plan_parents_ignores_marker_less_issues_even_with_type_label():
    """Regression guard: decompose_plan labels sub-issues with type:plan-task
    (the same label plan parents carry). list_open_by_label returns both,
    but only plan parents have a sync marker. Sub-issues must be filtered out
    by marker check (kind==plan) rather than trusting the label alone, since
    sub-issues have no marker at all (parse_marker returns None)."""
    client = FakeGithubClient()
    # Create a real plan parent via sync_pass_one - it will have a marker
    sync_pass_one([_plan_item()], client, dry_run=False)
    (plan_number,) = client.issues.keys()
    # Seed the plan parent's sub-issue summary as complete
    client.sub_issues_summary[plan_number] = (3, 3)

    # Inject a sub-issue directly: same type:plan-task label but NO marker
    sub_issue_body = "## Context\nPart of docs/superpowers/plans/x.md"
    sub_issue_number = client.create_issue(
        "Task 1: subtitle",
        sub_issue_body,
        [labels.STATUS_CLAIMABLE, labels.TYPE_PLAN_TASK]
    ).number
    # Seed sub-issue summary as if it's complete, to test that marker filtering
    # prevents this issue from being closed despite looking "ready" via total==0
    # trick - we explicitly mark it complete so marker filter is the only thing
    # preventing a mis-close.
    client.sub_issues_summary[sub_issue_number] = (3, 3)

    report = close_completed_plan_parents(client, dry_run=False)

    # Plan parent should close (it has a marker + complete subs)
    assert client.issues[plan_number]["open"] is False
    assert f"#{plan_number}" in report.closed[0]

    # Sub-issue must NOT close (no marker, despite type:plan-task label and complete subs)
    assert client.issues[sub_issue_number]["open"] is True
    assert sub_issue_number not in [int(c.split()[0][1:]) for c in report.closed]


def test_mismatch_comment_for_plan_kind_names_the_classification():
    plan_item = SyncItem(
        kind=labels.SYNC_MARKER_KIND_PLAN, key="2026-08-27-x.md",
        title="Plan: 2026-08-27-x.md", status_label=labels.STATUS_CLAIMABLE,
        type_label=labels.TYPE_PLAN_TASK, context_body="",
        acceptance_criteria=(), classification="in-progress",
    )
    comment = _mismatch_comment(plan_item)
    assert "in-progress" in comment and "reclassify" in comment
    assert "sync --sources plan" in comment


def test_mismatch_comment_for_non_plan_kind_uses_generic_message():
    track_item = SyncItem(
        kind=labels.SYNC_MARKER_KIND_WORKTREE, key="feat/x",
        title="feat/x", status_label=labels.STATUS_CLAIMABLE,
        type_label=labels.TYPE_TRACKING, context_body="",
        acceptance_criteria=(),
    )
    comment = _mismatch_comment(track_item)
    assert "still open" in comment and "reclassify" not in comment
