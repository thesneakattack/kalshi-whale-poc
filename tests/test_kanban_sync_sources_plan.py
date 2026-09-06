from tools.kanban_sync import labels
from tools.kanban_sync.sources_plan import build_plan_items, list_plan_candidates


def test_list_plan_candidates_excludes_readme(tmp_path):
    (tmp_path / "README.md").write_text("index")
    (tmp_path / "2026-08-25-frontend-modularization.md").write_text("x")

    result = list_plan_candidates(tmp_path)

    assert result == ["2026-08-25-frontend-modularization.md"]


def test_list_plan_candidates_excludes_active_tracks_board_itself(tmp_path):
    """Not moved by this change (a later migration step retires it), but it
    self-declares "not a plan in its own right"
    (docs/superpowers/lanes/step1-plans-classification.md) and must never
    surface as a candidate needing done/in-progress/not-started
    classification."""
    (tmp_path / "2026-08-26-active-tracks-board.md").write_text("board")
    (tmp_path / "2026-08-25-frontend-modularization.md").write_text("x")

    result = list_plan_candidates(tmp_path)

    assert result == ["2026-08-25-frontend-modularization.md"]


def test_list_plan_candidates_returns_sorted_list(tmp_path):
    (tmp_path / "b-plan.md").write_text("x")
    (tmp_path / "a-plan.md").write_text("x")

    result = list_plan_candidates(tmp_path)

    assert result == ["a-plan.md", "b-plan.md"]


def test_list_plan_candidates_excludes_review_companion_with_existing_parent(tmp_path):
    (tmp_path / "2026-09-03-x-implementation.md").write_text("plan")
    (tmp_path / "2026-09-03-x-implementation-review.md").write_text("review")
    (tmp_path / "2026-09-03-x-implementation-self-review.md").write_text("self-review")
    (tmp_path / "2026-09-03-x-implementation-consolidation.md").write_text("consolidation")

    result = list_plan_candidates(tmp_path)

    assert result == ["2026-09-03-x-implementation.md"]


def test_list_plan_candidates_excludes_compound_companion_suffixes_with_existing_parent(tmp_path):
    """Real filenames from docs/superpowers/plans/ - the companion suffix
    family is much wider than four bare words, and includes a qualifier
    (pr-/catchup-/plan-) stacked in front of the base review/consolidation
    word."""
    (tmp_path / "2026-09-03-y-implementation.md").write_text("plan")
    (tmp_path / "2026-09-03-y-implementation-pr-review.md").write_text("x")
    (tmp_path / "2026-09-03-y-implementation-pr-self-review.md").write_text("x")
    (tmp_path / "2026-09-03-y-implementation-pr-consolidation.md").write_text("x")
    (tmp_path / "2026-09-03-y-implementation-plan-review.md").write_text("x")
    (tmp_path / "2026-08-25-w.md").write_text("x")
    (tmp_path / "2026-08-25-w-catchup-consolidation.md").write_text("x")

    result = list_plan_candidates(tmp_path)

    assert result == ["2026-08-25-w.md", "2026-09-03-y-implementation.md"]


def test_list_plan_candidates_excludes_recheck_and_round_suffixes_with_existing_parent(tmp_path):
    (tmp_path / "2026-09-03-v.md").write_text("plan")
    (tmp_path / "2026-09-03-v-recheck.md").write_text("x")
    (tmp_path / "2026-09-03-v-recheck-2.md").write_text("x")
    (tmp_path / "2026-09-03-v-self-review-round2.md").write_text("x")

    result = list_plan_candidates(tmp_path)

    assert result == ["2026-09-03-v.md"]


def test_list_plan_candidates_keeps_companion_shaped_file_when_no_parent_exists(tmp_path):
    """A suffix match alone isn't enough - docs/superpowers/lanes/step1-
    plans-classification.md documents a real file of exactly this shape
    (...-persistence-layer-task8-candidate-ledger-self-review.md) that has
    no filename-obvious parent and is correctly left as its own candidate
    for the judgment-assisted kanban-board-sync skill to assign by hand."""
    (tmp_path / "2026-09-03-orphan-self-review.md").write_text("x")

    result = list_plan_candidates(tmp_path)

    assert result == ["2026-09-03-orphan-self-review.md"]


def test_list_plan_candidates_keeps_file_that_is_companion_shaped_only_in_content(tmp_path):
    """A real, documented gap (docs/superpowers/lanes/step1-plans-
    classification.md's finding G2): a file that IS a review companion by
    content can carry a suffix this mechanical filename check doesn't
    recognize (e.g. "-freshness-check"). That's the judgment-assisted
    kanban-board-sync skill's job, not this function's - see this module's
    own docstring."""
    (tmp_path / "2026-08-25-z.md").write_text("x")
    (tmp_path / "2026-09-03-z-freshness-check.md").write_text("x")

    result = list_plan_candidates(tmp_path)

    assert result == ["2026-08-25-z.md", "2026-09-03-z-freshness-check.md"]


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
    assert items[0].phase_label == labels.PHASE_DONE


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


def test_build_plan_items_not_started_and_in_progress_are_phase_plan():
    """Every not-started/in-progress item reaching build_plan_items already
    has a real plan doc - that's how it became a candidate at all
    (list_plan_candidates only scans docs/superpowers/plans/*.md) - so phase
    is always plan, never a lower phase. "done" classifications get
    phase:done instead - see
    test_build_plan_items_emits_done_item_for_done_classification."""
    items = build_plan_items({
        "not-started.md": {"status": "not-started", "note": ""},
        "in-progress.md": {"status": "in-progress", "note": ""},
    })

    assert all(i.phase_label == labels.PHASE_PLAN for i in items)


def test_build_plan_items_never_emits_implementing_or_verification_phase():
    """phase:implementing/phase:verification are worktree-only (a worktree
    maps 1:1 to one branch; a plan doc does not reliably correlate to a
    branch by name - see the design doc's empirical evidence). Regression
    guard against future drift on that decision."""
    items = build_plan_items({
        "a.md": {"status": "not-started", "note": ""},
        "b.md": {"status": "in-progress", "note": ""},
        "c.md": {"status": "done", "note": ""},
    })

    assert all(
        i.phase_label in {labels.PHASE_PLAN, labels.PHASE_DONE} for i in items
    )


def test_build_plan_items_sets_classification_from_status():
    items = build_plan_items({
        "x.md": {"status": "in-progress", "note": "partial"},
        "y.md": {"status": "done", "note": ""},
    })
    x = next(i for i in items if i.key == "x.md")
    y = next(i for i in items if i.key == "y.md")
    assert x.classification == "in-progress"
    assert y.classification == "done"
