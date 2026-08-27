from tools.kanban_sync import labels
from tools.kanban_sync.models import SyncItem, SyncReport


def test_sync_item_defaults():
    item = SyncItem(
        kind="track", key="A", title="Track A",
        status_label=labels.STATUS_CLAIMABLE, type_label=labels.TYPE_TRACKING,
        context_body="## Context\nx", acceptance_criteria=("x",),
    )
    assert item.scope_paths == ()
    assert item.depends_on_keys == ()
    assert item.done is False


def test_sync_item_is_frozen():
    item = SyncItem(
        kind="track", key="A", title="Track A",
        status_label=labels.STATUS_CLAIMABLE, type_label=labels.TYPE_TRACKING,
        context_body="x", acceptance_criteria=("x",),
    )
    try:
        item.title = "changed"
        assert False, "expected FrozenInstanceError"
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"


def test_sync_report_defaults_to_empty_lists_independently():
    a = SyncReport()
    b = SyncReport()
    a.created.append("x")
    assert b.created == []
