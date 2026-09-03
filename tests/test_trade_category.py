import pytest

from services import trade_category as tc


@pytest.fixture(autouse=True)
def _redirect_db(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "DB_PATH", tmp_path / "trade_category.db")


def test_record_and_lookup_a_category():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {"TICK-A": "Sports"}


def test_record_category_noop_when_category_is_none():
    tc.record_category("TICK-A", None, now=1000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {}


def test_record_category_noop_when_category_is_empty_string():
    tc.record_category("TICK-A", "", now=1000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {}


def test_record_category_noop_when_ticker_is_empty():
    tc.record_category("", "Sports", now=1000.0)
    assert tc.categories_for_tickers([""]) == {}


def test_re_recording_updates_the_category():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    tc.record_category("TICK-A", "Politics", now=2000.0)
    assert tc.categories_for_tickers(["TICK-A"]) == {"TICK-A": "Politics"}


def test_categories_for_tickers_bulk_lookup():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    tc.record_category("TICK-B", "Politics", now=1000.0)
    result = tc.categories_for_tickers(["TICK-A", "TICK-B", "TICK-C"])
    assert result == {"TICK-A": "Sports", "TICK-B": "Politics"}  # TICK-C never recorded, absent not None


def test_categories_for_tickers_empty_list_returns_empty_dict():
    assert tc.categories_for_tickers([]) == {}


def test_clear_all_wipes_every_category():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    tc.clear_all()
    assert tc.categories_for_tickers(["TICK-A"]) == {}


# --- subcategory (2026-08-16, series -> subcategory -> category fallback) --

def test_record_and_lookup_a_subcategory():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    assert tc.subcategories_for_tickers(["TICK-A"]) == {"TICK-A": "Baseball"}


def test_subcategory_defaults_to_none_when_not_given():
    tc.record_category("TICK-A", "Sports", now=1000.0)
    assert tc.subcategories_for_tickers(["TICK-A"]) == {}


def test_subcategory_excluded_from_bulk_lookup_when_null():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.record_category("TICK-B", "Politics", now=1000.0)  # no subcategory - Politics doesn't have one
    result = tc.subcategories_for_tickers(["TICK-A", "TICK-B"])
    assert result == {"TICK-A": "Baseball"}


def test_re_recording_category_without_subcategory_does_not_clear_a_known_one():
    # A later call that only knows the category shouldn't blow away a
    # subcategory a whale-follow entry already recorded for the same
    # ticker.
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.record_category("TICK-A", "Sports", now=2000.0)
    assert tc.subcategories_for_tickers(["TICK-A"]) == {"TICK-A": "Baseball"}


def test_re_recording_with_a_new_subcategory_updates_it():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.record_category("TICK-A", "Sports", now=2000.0, subcategory="Football")
    assert tc.subcategories_for_tickers(["TICK-A"]) == {"TICK-A": "Football"}


def test_subcategories_for_tickers_empty_list_returns_empty_dict():
    assert tc.subcategories_for_tickers([]) == {}


def test_clear_all_wipes_subcategory_too():
    tc.record_category("TICK-A", "Sports", now=1000.0, subcategory="Baseball")
    tc.clear_all()
    assert tc.subcategories_for_tickers(["TICK-A"]) == {}


# --- services/db.py migration (Task 10) -----------------------------------

def test_connect_closes_its_connection(monkeypatch):
    """_RecordingConnection wraps the real connection instead of mutating
    conn.close directly - mutating it raises AttributeError on this
    container's Python (sqlite3.Connection.close is read-only), the same
    defect Task 1's own implementation (PR #518) found and fixed against
    tests/test_signal_log.py's proven pattern (Tier 0's own Task 5).
    real_connect captures whatever sqlite3.connect currently is, which
    under pytest is tests/support/runtime_isolation.py's _guarded_connect,
    not the raw stdlib one - the live-data guard stays in force underneath
    the spy. The autouse _redirect_db fixture above already redirects
    DB_PATH, so no per-test monkeypatching of it is needed here or below."""
    import sqlite3

    closed = []
    real_connect = sqlite3.connect

    class _RecordingConnection:
        def __init__(self, inner):
            self._inner = inner

        def close(self):
            closed.append(True)
            self._inner.close()

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _tracking_connect(*args, **kwargs):
        return _RecordingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(tc.db.sqlite3, "connect", _tracking_connect)
    with tc._connect() as conn:
        conn.execute("SELECT 1")
    assert closed == [True]


def test_connect_still_creates_table_and_subcategory_column():
    with tc._connect() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "trade_category" in tables
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_category)")}
        assert {"ticker", "category", "recorded_at", "subcategory"} <= cols


def test_connect_sets_explicit_busy_timeout_pragma():
    """D3: 5000ms is sqlite3's existing implicit default made explicit,
    not a new number invented for this module."""
    with tc._connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_connect_does_not_leak_file_descriptors():
    """Adversarial review on the observability.py migration (PR #540)
    required this as a committed artifact, not review-comment prose -
    carried forward to every subsequent task in this migration."""
    import os

    def _open_fd_count():
        return len(os.listdir("/proc/self/fd"))

    before = _open_fd_count()
    for i in range(50):
        tc.record_category(f"TICK-FD-{i}", "Sports", now=1000.0 + i)
    for _ in range(50):
        tc.categories_for_tickers(["TICK-FD-0"])
    after = _open_fd_count()
    assert after == before



def test_connect_creates_table_and_column_in_autocommit_not_a_transaction():
    """Task 10 moved db.add_column_if_missing() inside db.connect()'s
    `with conn:` (previously an inlined PRAGMA table_info guard in the
    same position) - the same class of reordering Task 5's CREATE INDEX
    move was, and Task 5's adversarial review asked this be a committed
    assertion rather than review-comment prose. Python's sqlite3 only
    emits an implicit BEGIN before INSERT/UPDATE/DELETE/REPLACE, so the
    CREATE TABLE and ALTER TABLE both commit immediately regardless of
    which `with` block runs them."""
    with tc._connect() as conn:
        assert conn.in_transaction is False
