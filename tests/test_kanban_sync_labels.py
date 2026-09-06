from tools.kanban_sync import labels


def test_status_labels_match_kanban_skill_scheme():
    assert labels.STATUS_CLAIMABLE == "status:claimable"
    assert labels.STATUS_CLAIMED == "status:claimed"
    assert labels.STATUS_IN_PROGRESS == "status:in-progress"
    assert labels.STATUS_READY_FOR_REVIEW == "status:ready-for-review"
    assert labels.STATUS_BLOCKED == "status:blocked"
    assert labels.STATUS_DONE == "status:done"


def test_all_status_labels_contains_exactly_six_values():
    assert labels.ALL_STATUS_LABELS == {
        "status:claimable", "status:claimed", "status:in-progress",
        "status:ready-for-review", "status:blocked", "status:done",
    }


def test_type_labels_include_repo_local_tracking_extension():
    assert labels.TYPE_TRACKING == "type:tracking"
    assert labels.TYPE_PLAN_TASK == "type:plan-task"


def test_sync_marker_kinds_are_lowercase_single_words():
    for kind in (
        labels.SYNC_MARKER_KIND_WORKTREE, labels.SYNC_MARKER_KIND_ROADMAP,
        labels.SYNC_MARKER_KIND_PLAN,
    ):
        assert kind == kind.lower()
        assert " " not in kind


def test_phase_labels_use_superpowers_skill_vocabulary():
    assert labels.PHASE_RESEARCH == "phase:research"
    assert labels.PHASE_SPEC == "phase:spec"
    assert labels.PHASE_PLAN == "phase:plan"
    assert labels.PHASE_DONE == "phase:done"


def test_phase_labels_include_worktree_only_implementing_and_verification():
    assert labels.PHASE_IMPLEMENTING == "phase:implementing"
    assert labels.PHASE_VERIFICATION == "phase:verification"


def test_all_phase_labels_contains_exactly_six_values():
    assert labels.ALL_PHASE_LABELS == {
        "phase:research", "phase:spec", "phase:plan",
        "phase:implementing", "phase:verification", "phase:done",
    }
