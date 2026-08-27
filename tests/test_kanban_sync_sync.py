from tools.kanban_sync import labels
from tools.kanban_sync.github_client import IssueState
from tools.kanban_sync.models import SyncItem
from tools.kanban_sync.sync import sync_pass_one


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
