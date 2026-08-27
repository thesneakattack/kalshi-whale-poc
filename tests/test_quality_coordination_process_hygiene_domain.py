import json
from pathlib import Path

from tools.quality_coordination import (
    FLOOR_HOURS_PROCESS_HYGIENE, _baseline_missing_dated_notes, collect_process_hygiene_signals,
)


def test_floor_hours_process_hygiene_is_zero_per_spec_rationale():
    assert FLOOR_HOURS_PROCESS_HYGIENE == 0.0


def test_missing_dated_notes_detects_id_whose_prefix_has_no_notes_entry():
    baseline = {
        "accepted_finding_ids": ["backend-route-unused:GET:/api/x"],
        "notes": {"config-unread:*": "an unrelated category's note"},
    }
    missing = _baseline_missing_dated_notes(json.dumps(baseline))

    assert "backend-route-unused:GET:/api/x" in missing


def test_missing_dated_notes_empty_when_prefix_note_exists():
    baseline = {
        "accepted_finding_ids": ["backend-route-unused:GET:/api/x"],
        "notes": {
            "backend-route-unused:*": "+1 2026-08-27: GET:/api/x accepted, no frontend caller yet.",
        },
    }
    missing = _baseline_missing_dated_notes(json.dumps(baseline))

    assert missing == []


def test_missing_dated_notes_against_a_baseline_shaped_like_the_real_file():
    """Regression test (found in review): the real tools/quality_audit/baseline.json's
    `notes` field is a dict keyed by check-prefix, not a flat string - a fixture using a
    flat string would pass while silently not exercising the real shape at all."""
    real_baseline_path = Path("tools/quality_audit/baseline.json")
    if not real_baseline_path.exists():
        return  # skip gracefully outside a full repo checkout - not this test's concern
    missing = _baseline_missing_dated_notes(real_baseline_path.read_text())

    assert missing == []  # every accepted ID's prefix has a notes entry as of 2026-08-27


def test_collect_process_hygiene_signals_one_per_missing_id(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({
        "accepted_finding_ids": ["api-usage:a", "backend-route-unused:b"],
        "notes": {"api-usage:*": "+1 2026-08-27: a accepted, reason x."},
    }))

    signals = collect_process_hygiene_signals(baseline_path, baseline_path.read_text())

    assert [s.identity for s in signals] == ["process_hygiene:baseline.json:backend-route-unused:b"]
    assert signals[0].domain == "process_hygiene"
