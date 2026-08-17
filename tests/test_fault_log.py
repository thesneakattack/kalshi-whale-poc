"""services/fault_log.py — the store that exists because a bare `except`
already cost this app hours of data.

game_state shipped with `event_type` added to its CREATE TABLE but no
guarded ALTER, so every INSERT raised on a pre-existing table. flush()
caught it, returned a drop count nobody read, and the store sat at zero rows
looking exactly like "no games are on right now."
"""
import sqlite3

import pytest

from services import fault_log as fl


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "DB_PATH", tmp_path / "fault_log.db")
    yield


def _boom(msg="table game_states has 15 columns but 16 values were supplied"):
    try:
        raise sqlite3.OperationalError(msg)
    except sqlite3.OperationalError as exc:
        return exc


def test_records_an_exception_with_its_traceback():
    assert fl.record("game_state", "flush", _boom(), context="1 row dropped") is True
    rows = fl.recent()
    assert len(rows) == 1
    r = rows[0]
    assert r["component"] == "game_state" and r["operation"] == "flush"
    assert r["exc_type"] == "OperationalError"
    assert "16 values" in r["message"]
    assert "OperationalError" in r["first_traceback"]
    assert r["context"] == "1 row dropped"
    assert r["count"] == 1


def test_repeats_increment_a_count_instead_of_flooding_the_table():
    """A fault on the websocket path can repeat thousands of times a
    minute. One row with count=N is the signal; N rows is noise that buries
    everything else."""
    for _ in range(500):
        fl.record("series_watcher", "record_trade", _boom("disk I/O error"))
    rows = fl.recent()
    assert len(rows) == 1
    assert rows[0]["count"] == 500
    assert rows[0]["first_seen"] <= rows[0]["last_seen"]


def test_the_first_traceback_is_kept_not_the_latest():
    """The original stack is the one worth having; later repeats add
    frequency, not information."""
    fl.record("m", "op", _boom("first"), now=100.0)
    first_tb = fl.recent()[0]["first_traceback"]
    fl.record("m", "op", _boom("first"), now=200.0)
    row = fl.recent()[0]
    assert row["first_traceback"] == first_tb
    assert row["first_seen"] == 100.0 and row["last_seen"] == 200.0


def test_distinct_faults_stay_distinct():
    fl.record("a", "op1", _boom("x"))
    fl.record("a", "op2", _boom("x"))
    fl.record("b", "op1", _boom("x"))
    fl.record("a", "op1", _boom("y"))
    assert len(fl.recent()) == 4


def test_non_exception_edge_cases_are_recordable():
    """Not everything worth knowing is an exception - an unparseable field
    or a refused projection is an edge case, not an error."""
    assert fl.record_fault("kalshi_trade_tape", "parse_price",
                           "yes_price_dollars absent", severity="warn") is True
    r = fl.recent()[0]
    assert r["severity"] == "warn" and r["exc_type"] is None
    assert "absent" in r["message"]


def test_summary_surfaces_the_loudest_faults():
    for _ in range(10):
        fl.record("index_feed", "flush", _boom("locked"))
    fl.record("game_state", "record", _boom("bad column"))
    fl.record_fault("provider", "resolve", "market not returned")

    s = fl.summary()
    assert s["distinct_faults"] == 3
    assert s["total_occurrences"] == 12
    assert s["by_component"]["index_feed"] == 10
    assert s["by_severity"]["error"] == 11 and s["by_severity"]["warn"] == 1
    assert s["most_frequent"][0]["component"] == "index_feed"


def test_recent_filters_by_component_and_time():
    fl.record("a", "op", _boom("x"), now=100.0)
    fl.record("b", "op", _boom("y"), now=200.0)
    assert len(fl.recent(component="a")) == 1
    assert len(fl.recent(since_ts=150.0)) == 1


def test_logging_never_raises_even_on_a_broken_store(monkeypatch, tmp_path):
    """A logger that throws inside an `except` block turns a handled fault
    into an unhandled one. This is the one place where swallowing is
    correct - there is nowhere left to report to."""
    def _explode():
        raise RuntimeError("no db")

    monkeypatch.setattr(fl, "_connect", _explode)
    assert fl.record("m", "op", _boom()) is False
    assert fl.record_fault("m", "op", "msg") is False
    assert fl.recent() == []


def test_oversized_message_and_traceback_are_bounded():
    huge = "x" * 50_000
    fl.record("m", "op", _boom(huge))
    r = fl.recent()[0]
    assert len(r["message"]) <= fl._MAX_MESSAGE_CHARS
    assert len(r["first_traceback"]) <= fl._MAX_TRACEBACK_CHARS
