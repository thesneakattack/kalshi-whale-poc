from pathlib import Path

from tools.kanban_sync import labels

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROOT_ANCHORED_PREFIXES = (
    "services/", ".claude/", ".github/", ".woodpecker/", "scripts/",
    "tests/", "bench/", "ui_samples/", "frontend/", "static/", "config/",
    "tools/", "docs/",
)


def _resolve_package_path(pkg: str) -> Path:
    """LANES entries are written the way the design doc's §3 table writes
    them: a handful (`services/kalshi/`, `services/config/*`) already carry
    a root-anchored prefix, everything else is a bare name implicitly under
    `services/` (that's where nearly every lane's code actually lives)."""
    if pkg.startswith(_ROOT_ANCHORED_PREFIXES):
        return _REPO_ROOT / pkg
    return _REPO_ROOT / "services" / pkg


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


def test_concern_hotpath_label_string():
    assert labels.CONCERN_HOTPATH == "concern:hotpath"


def test_concerns_dict_mirrors_concern_hotpath():
    assert labels.CONCERNS == {"hotpath": labels.CONCERN_HOTPATH}


def test_lanes_covers_exactly_nine_lanes_numbered_one_to_nine():
    assert set(labels.LANES.keys()) == set(range(1, 10))


def test_lanes_each_entry_has_a_label_matching_its_number():
    for number, lane in labels.LANES.items():
        assert lane["label"] == f"lane:{number}"


def test_lanes_names_match_the_merged_design_doc_section_3():
    assert labels.LANES[1]["name"] == "Kalshi & index data ingestion"
    assert labels.LANES[2]["name"] == "Whale signal detection & calibration"
    assert labels.LANES[3]["name"] == "Strategy, risk & execution"
    assert labels.LANES[4]["name"] == "Analytics, advisory & research"
    assert labels.LANES[5]["name"] == "Runtime infrastructure"
    assert labels.LANES[6]["name"] == "Observability, quality & safety infra"
    assert labels.LANES[7]["name"] == "Config & control plane"
    assert labels.LANES[8]["name"] == "Frontend & dashboard"
    assert labels.LANES[9]["name"] == "Tooling, CI & process governance"


def test_lanes_each_entry_has_a_nonempty_package_list():
    for number, lane in labels.LANES.items():
        assert isinstance(lane["packages"], (list, tuple))
        assert len(lane["packages"]) > 0, f"lane {number} has no packages"


def test_lanes_package_lists_spot_check_a_few_known_modules():
    # One representative package per lane, taken directly from the merged
    # design doc's §3 table - not exhaustive, just enough to catch a lane
    # whose list was transcribed against the wrong row.
    assert "services/kalshi/" in labels.LANES[1]["packages"]
    assert "whalewatchers/" in labels.LANES[2]["packages"]
    assert "strategy_engine.py" in labels.LANES[3]["packages"]
    assert "candidate_log.py" in labels.LANES[4]["packages"]
    assert "tick_executor.py" in labels.LANES[5]["packages"]
    assert "diagnostics/" in labels.LANES[6]["packages"]
    assert "services/config/" in labels.LANES[7]["packages"]
    assert "frontend/" in labels.LANES[8]["packages"]
    assert "tools/" in labels.LANES[9]["packages"]


def test_every_lane_package_path_exists_on_disk():
    # Mechanical, not a spot check: every single entry in every lane must
    # resolve to a real file or directory. Catches transcription errors the
    # spot-check test above can't (e.g. assuming a filename mentioned "in"
    # a package's prose description is nested under it, when the real file
    # sits flat one level up - caught live 2026-09-06 for three of Lane 2's
    # entries, and separately for Lane 4's config_performance.py, both
    # entries that looked plausible without checking the real tree).
    missing = []
    for number, lane in labels.LANES.items():
        for pkg in lane["packages"]:
            if not _resolve_package_path(pkg).exists():
                missing.append((number, pkg))
    assert not missing, f"LANES package paths not found on disk: {missing}"


def test_all_lane_labels_contains_exactly_nine_values():
    assert labels.ALL_LANE_LABELS == {f"lane:{n}" for n in range(1, 10)}
