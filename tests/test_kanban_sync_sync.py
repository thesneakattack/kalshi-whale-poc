from tools.kanban_sync import labels
from tools.kanban_sync.github_client import IssueState
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync.sync import reconcile, sync_pass_one


class FakeGithubClient:
    def __init__(self) -> None:
        self.issues: dict[int, dict] = {}
        self._next_number = 1
        self.comments: list[tuple[int, str]] = []

    def find_by_marker(self, marker):
        for number, issue in self.issues.items():
            if marker in issue["body"]:
                return IssueState(number=number, open=issue["open"], labels=frozenset(issue["labels"]))
        return None

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


def _item(kind="track", key="A", *, title="Track A", status=labels.STATUS_CLAIMABLE,
          done=False, depends_on=()):
    return SyncItem(
        kind=kind, key=key, title=title, status_label=status,
        type_label=labels.TYPE_TRACKING, context_body="## Context\nx",
        acceptance_criteria=("done when x happens",), done=done,
        depends_on_keys=depends_on,
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
