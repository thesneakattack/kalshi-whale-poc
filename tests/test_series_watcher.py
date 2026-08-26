"""services/series_watcher.py - capture + the accuracy-vs-win-rate reconcile.

Every DB_PATH is monkeypatched to a tmp file (CLAUDE.md's standing rule:
tests never touch a real data/*.db), including the two stores this module
only reads - signal_log and paper_broker.
"""
import json
import sqlite3

import pytest

from services import series_watcher as sw


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from services import data_quarantine, signal_log
    from services import paper_broker as pb_module

    monkeypatch.setattr(sw, "DB_PATH", tmp_path / "series_watcher.db")
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "signal_log.db")
    monkeypatch.setattr(pb_module, "DB_PATH", tmp_path / "paper_broker.db")
    monkeypatch.setattr(data_quarantine, "DB_PATH", tmp_path / "quarantine.db")
    sw._last_book_write.clear()
    # Capture is buffered now (see series_watcher.flush) and the buffers are
    # module globals, so they have to be reset between tests or one test's
    # unflushed rows land in the next one's database.
    monkeypatch.setattr(sw, "_trade_buffer", [])
    monkeypatch.setattr(sw, "_book_buffer", [])
    monkeypatch.setattr(sw, "_dropped_rows", 0)
    monkeypatch.setattr(sw, "_quarantine_cache", None)
    yield


CFG = {
    "series_watcher": {"enabled": True, "series": ["KXBTC15M"], "book_snapshot_interval_sec": 5},
    "whale_watcher_kalshi": {"min_contracts": 5000, "min_contracts_by_series": {"KXBTC15M": 2500}},
}


def _trade(trade_id, ticker="KXBTC15M-26AUG17-B1", outcome="yes", count="1000.00",
           yes_price="0.60", no_price="0.40", **extra):
    return {
        "trade_id": trade_id, "ticker": ticker, "count_fp": count,
        "yes_price_dollars": yes_price, "no_price_dollars": no_price,
        "taker_outcome_side": outcome, "taker_book_side": "bid" if outcome == "yes" else "ask",
        "taker_side": outcome, "is_block_trade": False, "ts_ms": 1_755_000_000_000, **extra,
    }


# ------------------------------------------------------------------ capture

def test_record_trade_keeps_the_whole_payload_not_just_the_derived_fields():
    trade = _trade("t1", some_future_kalshi_field="whatever it adds next")
    assert sw.record_trade(trade, CFG, now=1000.0) is True
    sw.flush()

    with sqlite3.connect(sw.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM raw_trades").fetchone()
    assert row["ticker"] == "KXBTC15M-26AUG17-B1"
    assert row["series"] == "KXBTC15M"
    assert row["resolved_side"] == "yes"
    assert row["notional_usd"] == pytest.approx(600.0)  # 1000 contracts * $0.60
    # The point of the raw_json column: a field this schema has never heard
    # of still survives, so a question asked next month isn't blocked on a
    # migration that should have happened today.
    assert json.loads(row["raw_json"])["some_future_kalshi_field"] == "whatever it adds next"


def test_record_trade_ignores_unwatched_series_and_duplicate_ids():
    assert sw.record_trade(_trade("t1", ticker="KXETH15M-26AUG17-B1"), CFG) is False
    # Both of these buffer - the return value means "accepted", not
    # "written", now that capture is batched. Deduplication happens at the
    # flush, via the trade_id primary key and INSERT OR IGNORE, which is
    # also what makes the REST and websocket paths safe to both capture the
    # same print.
    assert sw.record_trade(_trade("t1"), CFG) is True
    assert sw.record_trade(_trade("t1"), CFG) is True
    sw.flush()

    with sqlite3.connect(sw.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_trades").fetchone()[0] == 1


def test_record_trade_stores_all_three_direction_fields_separately():
    """The 2026-08-17 audit found taker_side deprecated with an expired
    removal guarantee. Keeping all three columns means the day the legacy
    field disappears is visible in the data, not inferred from a support
    ticket."""
    sw.record_trade(_trade("t1", outcome="no"), CFG)
    sw.flush()
    with sqlite3.connect(sw.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM raw_trades").fetchone()
    assert (row["taker_outcome_side"], row["taker_book_side"], row["taker_side_legacy"]) == ("no", "ask", "no")
    assert row["resolved_side"] == "no"
    assert row["notional_usd"] == pytest.approx(400.0)  # no-side: 1000 * $0.40, not * $0.60


def test_record_trade_marks_side_unreadable_rather_than_guessing():
    trade = _trade("t1")
    for key in ("taker_outcome_side", "taker_book_side", "taker_side"):
        trade.pop(key)
    assert sw.record_trade(trade, CFG) is True
    sw.flush()
    with sqlite3.connect(sw.DB_PATH) as conn:
        side, notional = conn.execute("SELECT resolved_side, notional_usd FROM raw_trades").fetchone()
    assert side is None and notional is None


def test_record_trade_never_raises_on_garbage():
    assert sw.record_trade({}, CFG) is False
    assert sw.record_trade({"trade_id": "x"}, CFG) is False
    assert sw.record_trade({"trade_id": "x", "ticker": "KXBTC15M-A", "count_fp": "not-a-number"}, CFG) is True


def test_record_book_keeps_the_fields_process_stream_ticker_throws_away():
    msg = {
        "market_ticker": "KXBTC15M-26AUG17-B1", "price_dollars": "0.480",
        "yes_bid_dollars": "0.450", "yes_ask_dollars": "0.530",
        "yes_bid_size_fp": "300.00", "yes_ask_size_fp": "150.00",
        "volume_fp": "33896.00", "open_interest_fp": "20422.00",
        "dollar_volume": 16948, "dollar_open_interest": 10211,
        "last_trade_size_fp": "25.00", "ts_ms": 1_755_000_000_000,
    }
    assert sw.record_book(msg, CFG, now=1000.0) is True
    sw.flush()
    with sqlite3.connect(sw.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM book_snapshots").fetchone()
    # main.py keeps only yes_bid/yes_ask; these are the ones it drops.
    assert row["yes_bid_size_fp"] == 300.0
    assert row["yes_ask_size_fp"] == 150.0
    assert row["open_interest_fp"] == 20422.0
    assert row["dollar_volume"] == 16948.0
    assert row["last_trade_size_fp"] == 25.0
    assert row["exchange_ts"] == pytest.approx(1_755_000_000.0)


def test_record_book_throttles_per_ticker():
    msg = {"market_ticker": "KXBTC15M-26AUG17-B1", "yes_bid_dollars": "0.45"}
    assert sw.record_book(msg, CFG, now=1000.0) is True
    assert sw.record_book(msg, CFG, now=1002.0) is False   # inside the 5s interval
    assert sw.record_book(msg, CFG, now=1006.0) is True
    other = {"market_ticker": "KXBTC15M-26AUG17-B2", "yes_bid_dollars": "0.45"}
    assert sw.record_book(other, CFG, now=1002.0) is True  # throttle is per ticker


def test_capture_disabled_writes_nothing():
    cfg = {"series_watcher": {"enabled": False, "series": ["KXBTC15M"]}}
    assert sw.record_trade(_trade("t1"), cfg) is False
    assert sw.record_book({"market_ticker": "KXBTC15M-A"}, cfg) is False


def test_prune_drops_old_book_snapshots_but_never_trades():
    sw.record_trade(_trade("t1"), CFG, now=1000.0)
    sw.record_book({"market_ticker": "KXBTC15M-A"}, CFG, now=1000.0)
    sw.flush()
    result = sw.prune(retention_hours=1.0, now=1000.0 + 2 * 3600)
    assert result["book_snapshots_deleted"] == 1
    with sqlite3.connect(sw.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_trades").fetchone()[0] == 1


def test_quarantined_window_marks_captured_trades_excluded():
    from services import data_quarantine

    data_quarantine.start("latency probe", reason="deliberate test")
    sw.record_trade(_trade("t1"), CFG, now=1000.0)
    sw.flush()
    with sqlite3.connect(sw.DB_PATH) as conn:
        assert conn.execute("SELECT excluded FROM raw_trades").fetchone()[0] == 1


# --------------------------------------------------------------- concurrency
#
# Code-review finding #2 (/code-review high pass against PR #23): this PR's
# own P1 work made record_trade/record_book and flush() genuinely
# cross-thread - a tick_executor worker thread (main.py's
# _flush_trade_capture_async) and the main asyncio event-loop thread
# (services/whale_stream/whale_stream_handlers.py's _process_stream_trade,
# which calls series_watcher.record_trade() directly and synchronously per
# WS message) both touch _trade_buffer/_book_buffer and flush()'s
# swap-and-clear, which was never synchronized. book_snapshots has no
# unique constraint (only an AUTOINCREMENT surrogate key), so a genuine
# flush-vs-flush race - record_trade's own batch-triggered inline flush()
# on the event-loop thread racing the tick_executor's scheduled flush() on
# a worker thread - can grab the SAME buffer twice (duplicate rows) or
# orphan an appended row into a buffer nothing ever flushes again (a
# silently lost row, no error, no drop counter increment).
#
# Root-cause note on the test design below: the actual vulnerable window is
# two adjacent plain statements inside flush() (capture the old lists, then
# reassign to new ones) with no function call or loop between them - under
# CPython's GIL, that is a genuinely hard window to hit via pure scheduling
# luck even with sys.setswitchinterval cranked way down (measured directly:
# 3/3 clean runs of a 400-iteration record_book()-vs-flush() burst, and
# 3/3 clean runs of a two-threads-both-calling-flush()-via-Barrier variant,
# neither ever reproduced a lost or duplicated row on this build/machine).
# That is a real property of GIL-scheduled CPython, not proof the race is
# safe - it is still a genuine TOCTOU bug (real on a free-threaded/no-GIL
# Python build, and not something anyone should rely on GIL incidental
# protection for). So the primary proof here is a DETERMINISTIC test of the
# fix's actual mechanism (sw._buffer_lock provides real mutual exclusion
# over every _trade_buffer/_book_buffer touch, verified by forcing the
# exact interleaving rather than hoping to get lucky with it) - the
# volume-based tests are kept below as secondary smoke coverage that the
# fixed system also behaves correctly under real concurrent load, not as
# the primary regression proof.

def test_flush_and_record_book_are_mutually_exclusive_via_the_buffer_lock():
    """The direct proof of the fix's synchronization mechanism (code-review
    finding #2): record_book() (and, by the same code path, record_trade()
    and flush()) must acquire sw._buffer_lock around every touch of
    _trade_buffer/_book_buffer. Proven by holding the lock from this test
    thread and confirming a concurrent record_book() call genuinely blocks
    until the lock is released - not "usually doesn't interleave badly,"
    a real mutual-exclusion guarantee that holds regardless of GIL
    scheduling timing (and would still hold on a free-threaded Python
    build, where the GIL's incidental protection disappears entirely)."""
    import threading

    entered = threading.Event()
    finished = threading.Event()

    def _blocked_writer() -> None:
        entered.set()
        sw.record_book(
            {"market_ticker": "KXBTC15M-LOCKTEST", "price_dollars": "0.50"}, CFG, now=1000.0,
        )
        finished.set()

    with sw._buffer_lock:
        t = threading.Thread(target=_blocked_writer)
        t.start()
        assert entered.wait(timeout=2.0)
        # The writer thread has started and is trying to record - it must
        # NOT be able to finish while this thread still holds the lock.
        # (finished.wait returning False here means "still not set after
        # waiting" - the writer is genuinely blocked, not just fast.)
        assert finished.wait(timeout=0.2) is False
    # Lock released here (context manager exit) - the writer can now proceed.
    t.join(timeout=2.0)
    assert finished.is_set()


def test_flush_holds_the_buffer_lock_too_not_just_record_calls():
    """The other half of the same guarantee: flush()'s own swap-and-clear
    must also go through sw._buffer_lock, or a concurrent record_book()
    could still interleave with flush()'s reassignment even though
    record_book() itself is lock-protected."""
    import threading

    entered = threading.Event()
    finished = threading.Event()

    def _blocked_flusher() -> None:
        entered.set()
        sw.flush()
        finished.set()

    with sw._buffer_lock:
        t = threading.Thread(target=_blocked_flusher)
        t.start()
        assert entered.wait(timeout=2.0)
        assert finished.wait(timeout=0.2) is False
    t.join(timeout=2.0)
    assert finished.is_set()

def test_concurrent_record_and_flush_loses_no_rows_and_creates_no_duplicates(monkeypatch):
    import sys
    import threading

    n_records = 400
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.00001)  # yield far more often - makes the race reliably observable
    try:
        errors: list[Exception] = []

        def _writer() -> None:
            try:
                for i in range(n_records):
                    ok = sw.record_book(
                        {"market_ticker": f"KXBTC15M-RACE-{i}", "price_dollars": "0.50"},
                        CFG, now=1000.0 + i,
                    )
                    assert ok is True
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                errors.append(exc)

        def _flusher(stop: threading.Event) -> None:
            try:
                while not stop.is_set():
                    sw.flush()
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                errors.append(exc)

        stop_flushing = threading.Event()
        writer = threading.Thread(target=_writer)
        flusher = threading.Thread(target=_flusher, args=(stop_flushing,))
        writer.start()
        flusher.start()
        writer.join()
        stop_flushing.set()
        flusher.join()
        sw.flush()  # catch anything left buffered after both threads finished
    finally:
        sys.setswitchinterval(old_interval)

    assert errors == []
    with sqlite3.connect(sw.DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM book_snapshots").fetchone()[0]
        distinct = conn.execute("SELECT COUNT(DISTINCT ticker) FROM book_snapshots").fetchone()[0]
    assert count == n_records  # no row lost, and no row duplicated
    assert distinct == n_records


def test_concurrent_two_flushers_never_double_write_the_same_buffered_rows():
    """The more specific shape of the bug: two threads calling flush() at
    the same time (record_trade's own batch-triggered inline flush racing
    the tick_executor's scheduled one) must partition the buffered rows
    between them, never both grab the same rows."""
    import sys
    import threading

    n_records = 300
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.00001)
    try:
        for i in range(n_records):
            assert sw.record_book(
                {"market_ticker": f"KXBTC15M-DBLFLUSH-{i}", "price_dollars": "0.50"},
                CFG, now=1000.0 + i,
            ) is True

        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def _flush_after_barrier() -> None:
            try:
                barrier.wait()
                sw.flush()
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                errors.append(exc)

        t1 = threading.Thread(target=_flush_after_barrier)
        t2 = threading.Thread(target=_flush_after_barrier)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    finally:
        sys.setswitchinterval(old_interval)

    assert errors == []
    with sqlite3.connect(sw.DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM book_snapshots").fetchone()[0]
    assert count == n_records  # never double-written, never dropped


# ------------------------------------------------------------------ analysis

def _seed_signal(ticker, side, seen_at, price, resolved=1, correct=1, notional=5000.0):
    from services import signal_log

    signal_log.log_signal(ticker, side, 100, 0.8, "kalshi_trade_tape", seen_at,
                          raw_context={"notional_usd": notional}, price=price)
    with sqlite3.connect(signal_log.DB_PATH) as conn:
        conn.execute("UPDATE signals SET resolved = ?, correct = ? WHERE ticker = ? AND seen_at = ?",
                     (resolved, correct, ticker, seen_at))


def _seed_trade(tid, ticker, side, price, ts, reason, signal_seen_at=None, size=100, fee=0.0):
    from services import paper_broker as pb_module

    with sqlite3.connect(pb_module.DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, ticker TEXT NOT NULL, "
            "side TEXT NOT NULL, size INTEGER NOT NULL, price REAL NOT NULL, reason TEXT NOT NULL, "
            "timestamp REAL NOT NULL, config_fingerprint TEXT, fee REAL, signal_seen_at REAL, "
            "excluded INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "INSERT INTO trades (id, ticker, side, size, price, reason, timestamp, "
            "config_fingerprint, fee, signal_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, ticker, side, size, price, reason, ts, "fp", fee, signal_seen_at),
        )


def test_funnel_reports_every_stage_and_separates_capture_from_signals():
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    _seed_signal("KXBTC15M-B", "yes", 1010.0, 0.70, correct=0)
    sw.record_trade(_trade("t1", ticker="KXBTC15M-A", count="10000.00"), CFG, now=1000.0)
    sw.record_trade(_trade("t2", ticker="KXBTC15M-A", count="10.00"), CFG, now=1001.0)
    sw.flush()

    out = sw.funnel("KXBTC15M", hours=24, cfg=CFG, now=1100.0)
    stages = {s["stage"]: s["count"] for s in out["stages"]}
    assert stages["prints_observed"] == 2
    assert stages["whale_sized_prints"] == 1   # 10,000 contracts clears 2,500; 10 does not
    assert stages["signals_logged"] == 2
    assert stages["signals_resolved"] == 2
    assert stages["signals_correct"] == 1
    assert out["signal_accuracy_pct"] == 50.0
    assert out["capture_active"] is True


def test_funnel_flags_that_capture_was_off_rather_than_claiming_no_whales():
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    out = sw.funnel("KXBTC15M", hours=24, cfg=CFG, now=1100.0)
    stages = {s["stage"]: s["count"] for s in out["stages"]}
    assert stages["prints_observed"] == 0
    assert stages["signals_logged"] == 1
    assert out["capture_active"] is False


def test_reconcile_splits_the_gap_into_selection_and_exit():
    """Two signals, both correct; only one is traded, and that one is
    stopped out. Whale accuracy 100%, realised win rate 0% - and the whole
    gap has to land on the exit component, not on the signal."""
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    _seed_signal("KXBTC15M-B", "yes", 1010.0, 0.60)
    _seed_trade("e1", "KXBTC15M-A", "yes", 0.62, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    _seed_trade("x1", "KXBTC15M-A", "yes", 0.40, 1100.0, "closed: stop-loss hit at 0.40 (realized -22.00)")

    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["signal_accuracy_pct"] == 100.0
    assert r["traded_signal_accuracy_pct"] == 100.0
    assert r["realised_win_rate_pct"] == 0.0
    assert r["selection_delta_pts"] == 0.0     # the traded subset was no worse than the population
    assert r["exit_delta_pts"] == -100.0       # the entire gap is the exit rule
    assert r["stop_losses"] == 1
    assert r["stop_losses_on_correct_signals"] == 1


def test_reconcile_detects_adverse_selection():
    """Population is 50% accurate; the one signal the gates picked was the
    wrong one. That belongs to selection, not to exits."""
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60, correct=0)
    _seed_signal("KXBTC15M-B", "yes", 1010.0, 0.60, correct=1)
    _seed_trade("e1", "KXBTC15M-A", "yes", 0.60, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    _seed_trade("x1", "KXBTC15M-A", "yes", 0.20, 1100.0, "closed: market settled no - position lost (realized -40.00)")

    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["signal_accuracy_pct"] == 50.0
    assert r["traded_signal_accuracy_pct"] == 0.0
    assert r["selection_delta_pts"] == -50.0
    assert r["exit_delta_pts"] == 0.0


def test_breakeven_accuracy_is_the_entry_price():
    """The arithmetic that makes "70% accurate" and "profitable" unrelated:
    EV per contract is (p - c), so an average entry at $0.80 needs 80%
    accuracy just to break even."""
    for i in range(7):
        _seed_signal(f"KXBTC15M-W{i}", "yes", 1000.0 + i, 0.80, correct=1)
    for i in range(3):
        _seed_signal(f"KXBTC15M-L{i}", "yes", 1100.0 + i, 0.80, correct=0)
    _seed_trade("e1", "KXBTC15M-W0", "yes", 0.80, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)

    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["signal_accuracy_pct"] == 70.0
    assert r["mean_entry_unit_cost"] == pytest.approx(0.80)
    assert r["breakeven_accuracy_pct"] == 80.0
    assert r["edge_pts"] == -10.0   # 70% accurate at $0.80 loses 10c/contract


def test_breakeven_accuracy_uses_the_no_side_inversion():
    """A no-side entry at yes_price 0.30 cost $0.70/contract, not $0.30 -
    CLAUDE.md's "no-side dollar math" bug class."""
    _seed_signal("KXBTC15M-A", "no", 1000.0, 0.30)
    _seed_trade("e1", "KXBTC15M-A", "no", 0.30, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["mean_entry_unit_cost"] == pytest.approx(0.70)


def test_reconcile_measures_slippage_between_print_and_fill():
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    _seed_trade("e1", "KXBTC15M-A", "yes", 0.64, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["mean_entry_slippage_pts"] == pytest.approx(4.0)   # paid 4 cents above the print


def test_reconcile_counts_entries_it_could_not_join_to_a_signal():
    """An entry with no signal_seen_at can't be attributed, and saying so is
    the difference between a measurement and a guess."""
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    _seed_trade("e1", "KXBTC15M-A", "yes", 0.60, 1005.0, "whale print (conf 0.80)", signal_seen_at=None)
    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["entries"] == 1
    assert r["entries_unjoined_to_signal"] == 1
    assert r["traded_signal_accuracy_pct"] is None


def test_reconcile_ignores_other_series():
    _seed_signal("KXETH15M-A", "yes", 1000.0, 0.60)
    _seed_trade("e1", "KXETH15M-A", "yes", 0.60, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["signals"] == 0 and r["entries"] == 0


def test_reconcile_excludes_quarantined_signals():
    from services import signal_log

    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60, correct=0)
    _seed_signal("KXBTC15M-B", "yes", 1010.0, 0.60, correct=1)
    signal_log.mark_excluded_range(999.0, 1005.0)   # quarantines the losing one only
    r = sw.reconcile("KXBTC15M", hours=24, cfg=CFG, now=2000.0)
    assert r["signals_resolved"] == 1
    assert r["signal_accuracy_pct"] == 100.0


# ------------------------------------------------------------------ the plug

def test_check_series_funnel_returns_a_diagnostics_check():
    from services.diagnostics.diagnostics import Check

    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    _seed_trade("e1", "KXBTC15M-A", "yes", 0.60, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    _seed_trade("x1", "KXBTC15M-A", "yes", 0.30, 1100.0, "closed: stop-loss hit at 0.30 (realized -30.00)")

    check = sw.check_series_funnel(CFG, "KXBTC15M", hours=24, now=2000.0)
    assert isinstance(check, Check)
    assert check.name == "series_funnel:KXBTC15M"
    assert check.status == "fail"
    assert "100.0% accurate" in check.summary or "100.0%" in check.summary
    assert check.to_dict()["detail"]["realised_win_rate_pct"] == 0.0


def test_check_is_unknown_not_ok_when_a_side_of_the_comparison_is_missing():
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    check = sw.check_series_funnel(CFG, "KXBTC15M", hours=24, now=2000.0)
    assert check.status == "unknown"
    assert "closed positions" in check.summary


def test_negative_edge_outranks_the_gap_in_the_headline():
    """A series can have a fine win rate and still be unprofitable at the
    price level - the check has to say so rather than reporting "ok"."""
    for i in range(9):
        _seed_signal(f"KXBTC15M-W{i}", "yes", 1000.0 + i, 0.95, correct=1)
    _seed_signal("KXBTC15M-L0", "yes", 1100.0, 0.95, correct=0)
    _seed_trade("e1", "KXBTC15M-W0", "yes", 0.95, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    _seed_trade("x1", "KXBTC15M-W0", "yes", 0.97, 1100.0, "closed: market settled yes - position won (realized +2.00)")

    check = sw.check_series_funnel(CFG, "KXBTC15M", hours=24, now=2000.0)
    assert check.status == "fail"
    assert "break even" in check.summary
    assert check.detail["edge_pts"] == pytest.approx(-5.0)


def test_book_context_says_unknown_rather_than_inventing_a_spread():
    _seed_signal("KXBTC15M-A", "yes", 1000.0, 0.60)
    _seed_trade("e1", "KXBTC15M-A", "yes", 0.60, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0)
    out = sw.book_context_at_entry("KXBTC15M", hours=24, now=2000.0)
    assert out["status"] == "unknown"
    assert "not reconstructable" in out["reason"]


def test_book_context_reports_spread_and_depth_when_snapshots_exist():
    _seed_trade("e1", "KXBTC15M-A", "yes", 0.60, 1005.0, "whale print (conf 0.80)", signal_seen_at=1000.0, size=500)
    sw.record_book({
        "market_ticker": "KXBTC15M-A", "yes_bid_dollars": "0.58", "yes_ask_dollars": "0.62",
        "yes_bid_size_fp": "900.00", "yes_ask_size_fp": "100.00",
    }, CFG, now=1004.0)
    sw.flush()

    out = sw.book_context_at_entry("KXBTC15M", hours=24, now=2000.0)
    assert out["status"] == "ok"
    assert out["entries_with_book"] == 1
    assert out["mean_spread_pts"] == pytest.approx(4.0)
    # 100 resting on the ask against a 500-contract buy - the book could not
    # fill this entry at the quoted price.
    assert out["mean_depth_ratio"] == pytest.approx(0.2)
    assert out["entries_with_insufficient_depth"] == 1


def test_watched_series_defaults_to_kxbtc15m():
    assert sw.watched_series(None) == ["KXBTC15M"]
    assert sw.watched_series({"series_watcher": {"series": "KXETH15M"}}) == ["KXETH15M"]
    assert sw.watched_series({"series_watcher": {"series": ["A", "B"]}}) == ["A", "B"]
