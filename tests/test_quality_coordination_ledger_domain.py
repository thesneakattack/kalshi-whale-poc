from datetime import datetime, timezone
from pathlib import Path

from tools.quality_coordination import (
    FLOOR_HOURS_LEDGER, _ledger_is_complete, _ledger_last_state_line, collect_ledger_signals,
)

AT = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_floor_hours_ledger_is_a_positive_constant():
    assert FLOOR_HOURS_LEDGER > 0


def test_ledger_last_state_line_returns_last_nonblank_line():
    text = "## Task 1\n- [x] done\n\n## Task 2\n- [ ] in progress\n\n"
    assert _ledger_last_state_line(text) == "- [ ] in progress"


def test_ledger_is_complete_recognizes_explicit_marker():
    assert _ledger_is_complete("STATUS: COMPLETE - all tasks done") is True


def test_ledger_is_complete_false_for_an_ordinary_task_line():
    assert _ledger_is_complete("- [ ] Task 4: implement X") is False


def test_collect_ledger_signals_still_present_for_incomplete_ledger(tmp_path):
    ledger = tmp_path / "progress.md"
    ledger.write_text("## Task 1\n- [x] done\n\n## Task 2\n- [ ] in progress\n")

    signals = collect_ledger_signals([ledger], at=AT)

    assert len(signals) == 1
    assert signals[0].still_present is True
    assert signals[0].domain == "ledger"


def test_collect_ledger_signals_absent_when_complete(tmp_path):
    ledger = tmp_path / "progress.md"
    ledger.write_text("## Task 3\nSTATUS: COMPLETE - all tasks done\n")

    signals = collect_ledger_signals([ledger], at=AT)

    assert signals == []


def test_collect_ledger_signals_absent_when_file_missing(tmp_path):
    missing = tmp_path / "nope" / "progress.md"

    signals = collect_ledger_signals([missing], at=AT)

    assert signals == []
