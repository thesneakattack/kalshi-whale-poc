import pytest

from tools.kanban_sync import labels, project_status
from tools.kanban_sync.github_client import IssueState
from tools.kanban_sync.live_status import resolve_project_status


def test_resolve_project_status_returns_done_for_a_closed_issue_regardless_of_labels():
    issue = IssueState(number=1, open=False, labels=frozenset({labels.STATUS_CLAIMABLE}))

    assert resolve_project_status(issue) == project_status.STATUS_DONE


def test_resolve_project_status_maps_the_single_status_label_via_the_project_status_table():
    issue = IssueState(number=1, open=True, labels=frozenset({labels.STATUS_READY_FOR_REVIEW}))

    assert resolve_project_status(issue) == project_status.STATUS_WAITING


def test_resolve_project_status_ignores_non_status_labels_alongside_the_real_one():
    issue = IssueState(
        number=1, open=True,
        labels=frozenset({labels.STATUS_IN_PROGRESS, labels.TYPE_PLAN_TASK, "depends-on:#2"}),
    )

    assert resolve_project_status(issue) == project_status.STATUS_DOING


def test_resolve_project_status_raises_when_no_status_label_present():
    issue = IssueState(number=7, open=True, labels=frozenset({labels.TYPE_FEATURE}))

    with pytest.raises(ValueError, match="#7"):
        resolve_project_status(issue)


def test_resolve_project_status_raises_when_multiple_status_labels_present():
    issue = IssueState(
        number=9, open=True,
        labels=frozenset({labels.STATUS_CLAIMED, labels.STATUS_BLOCKED}),
    )

    with pytest.raises(ValueError, match="#9"):
        resolve_project_status(issue)
