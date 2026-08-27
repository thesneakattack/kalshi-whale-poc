from tools.kanban_sync import labels
from tools.kanban_sync import project_status


def test_status_option_ids_cover_all_five_project_columns():
    assert set(project_status.STATUS_OPTION_IDS) == {
        project_status.STATUS_INBOX, project_status.STATUS_NEXT,
        project_status.STATUS_DOING, project_status.STATUS_WAITING,
        project_status.STATUS_DONE,
    }


def test_status_label_to_project_status_covers_all_six_status_labels():
    """Durable regression guard: if a 7th status:* label is ever added
    without updating this mapping, this test fails at CI time instead of
    a KeyError crashing mid-sync-run."""
    assert set(project_status.STATUS_LABEL_TO_PROJECT_STATUS) == labels.ALL_STATUS_LABELS


def test_status_label_to_project_status_matches_personal_todo_archetype():
    """Mirrors ~/.claude/plugins/mcpmarket-me/skills/github-issues-kanban/
    assets/template-personal-todo.json's own "columns" - this board's own
    stated design, not an invented mapping. Extended (not overridden) for
    status:claimed/status:ready-for-review, which the template predates."""
    assert project_status.STATUS_LABEL_TO_PROJECT_STATUS == {
        labels.STATUS_CLAIMABLE: project_status.STATUS_NEXT,
        labels.STATUS_CLAIMED: project_status.STATUS_DOING,
        labels.STATUS_IN_PROGRESS: project_status.STATUS_DOING,
        labels.STATUS_READY_FOR_REVIEW: project_status.STATUS_WAITING,
        labels.STATUS_BLOCKED: project_status.STATUS_WAITING,
        labels.STATUS_DONE: project_status.STATUS_DONE,
    }


def test_project_constants_match_the_confirmed_live_board():
    """Typo trip-wire, not a live-board proof - these are the real GraphQL
    node IDs confirmed live 2026-08-27 via both raw GraphQL and
    `gh project field-list 3 --owner thesneakattack --format json`."""
    assert project_status.PROJECT_OWNER == "thesneakattack"
    assert project_status.PROJECT_NUMBER == 3
    assert project_status.PROJECT_ID == "PVT_kwHOAHYiPM4BhmnC"
    assert project_status.STATUS_FIELD_ID == "PVTSSF_lAHOAHYiPM4BhmnCzhghwaY"
    assert project_status.STATUS_OPTION_IDS == {
        "Inbox": "02295626", "Next": "61df5cea", "Doing": "3b0f9f3a",
        "Waiting": "b7ff4b2e", "Done": "1bf7f74a",
    }
