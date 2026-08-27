from tools.kanban_sync import labels
from tools.kanban_sync.sources_plan import build_plan_items, list_plan_candidates


def test_list_plan_candidates_excludes_board_file_and_referenced_plans(tmp_path):
    (tmp_path / "2026-08-26-active-tracks-board.md").write_text("board")
    (tmp_path / "2026-08-26-subscription-churn-investigation.md").write_text("x")
    (tmp_path / "2026-08-25-frontend-modularization.md").write_text("x")
    board_text = (
        "Investigation plan: `docs/superpowers/plans/"
        "2026-08-26-subscription-churn-investigation.md`"
    )

    result = list_plan_candidates(tmp_path, board_text)

    assert result == ["2026-08-25-frontend-modularization.md"]


def test_list_plan_candidates_returns_sorted_list(tmp_path):
    (tmp_path / "2026-08-26-active-tracks-board.md").write_text("board")
    (tmp_path / "b-plan.md").write_text("x")
    (tmp_path / "a-plan.md").write_text("x")

    result = list_plan_candidates(tmp_path, "")

    assert result == ["a-plan.md", "b-plan.md"]


def test_build_plan_items_emits_done_item_for_done_classification():
    """A 'done' classification must still produce a SyncItem (with done=True)
    so sync_pass_one's existing close-on-done logic can close an already-open
    issue for a plan that finished after its issue was created. Skipping it
    entirely (the old behavior) meant a plan that shipped after its tracking
    issue existed could never be auto-closed - found live 2026-08-27 (issue
    #96, backend-services-modularization) and fixed here rather than closed
    by hand every time it recurs."""
    items = build_plan_items({"x.md": {"status": "done", "note": "shipped"}})

    assert len(items) == 1
    assert items[0].key == "x.md"
    assert items[0].done is True
    assert items[0].status_label == labels.STATUS_DONE
    assert items[0].phase_label == labels.PHASE_IMPLEMENTED


def test_build_plan_items_creates_item_for_in_progress():
    items = build_plan_items({
        "2026-08-25-frontend-modularization.md": {
            "status": "in-progress", "note": "several tasks still open",
        }
    })

    assert len(items) == 1
    assert items[0].key == "2026-08-25-frontend-modularization.md"
    assert items[0].done is False
    assert "several tasks still open" in items[0].context_body


def test_build_plan_items_has_acceptance_criteria():
    items = build_plan_items({"x.md": {"status": "not-started", "note": ""}})

    assert len(items[0].acceptance_criteria) >= 1


def test_build_plan_items_not_started_and_in_progress_are_phase_implementation_plan():
    """Every not-started/in-progress item reaching build_plan_items already
    has a real plan doc - that's how it became a candidate at all
    (list_plan_candidates only scans docs/superpowers/plans/*.md) - so phase
    is always implementation-plan, never a lower phase. "done" classifications
    get phase:implemented instead - see
    test_build_plan_items_emits_done_item_for_done_classification."""
    items = build_plan_items({
        "not-started.md": {"status": "not-started", "note": ""},
        "in-progress.md": {"status": "in-progress", "note": ""},
    })

    assert all(i.phase_label == labels.PHASE_IMPLEMENTATION_PLAN for i in items)
