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


def test_build_plan_items_skips_done_classification():
    items = build_plan_items({"x.md": {"status": "done", "note": "shipped"}})

    assert items == []


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
