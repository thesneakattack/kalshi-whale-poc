import time

from services import market_history as mh


def _mh(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, "DB_PATH", tmp_path / "market_history.db")
    return mh


def test_record_and_count_snapshots(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    mh.record_snapshots([
        {"ticker": "TICK-A", "yes_price": 0.5, "spread": 0.02, "volume_24h": 1000, "time_to_close_sec": 3600},
        {"ticker": "TICK-B", "yes_price": 0.3, "spread": 0.05, "volume_24h": 500, "time_to_close_sec": 7200},
    ])
    assert mh.snapshot_count() == 2
    assert mh.snapshot_count("TICK-A") == 1
    assert mh.tracked_ticker_count() == 2


def test_series_derived_from_ticker_prefix(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    mh.record_snapshots([{"ticker": "KXPGATOUR-26-ABC", "yes_price": 0.5, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}])
    with mh._connect(tmp_path / "market_history.db") as conn:
        series = conn.execute("SELECT series FROM snapshots WHERE ticker = ?", ("KXPGATOUR-26-ABC",)).fetchone()[0]
    assert series == "KXPGATOUR"


def test_record_outcome_and_count(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    mh.record_outcome("TICK-A", "yes")
    mh.record_outcome("TICK-B", "no")
    assert mh.outcome_count() == 2


def test_clear_all_wipes_both_tables(tmp_path, monkeypatch):
    # Data-robustness audit finding (2026-08-10): this file had no wired
    # reset path at all, despite being one of the two largest data/*.db
    # files on disk - Danger Zone couldn't clear it.
    _mh(tmp_path, monkeypatch)
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.5}])
    mh.record_outcome("TICK-A", "yes")
    mh.clear_all()
    assert mh.snapshot_count() == 0
    assert mh.outcome_count() == 0


def test_record_outcome_is_idempotent_first_write_wins(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    mh.record_outcome("TICK-A", "yes", resolved_at=100.0)
    mh.record_outcome("TICK-A", "no", resolved_at=200.0)  # should be ignored
    with mh._connect(tmp_path / "market_history.db") as conn:
        row = conn.execute("SELECT result, resolved_at FROM outcomes WHERE ticker = ?", ("TICK-A",)).fetchone()
    assert row == ("yes", 100.0)


def test_seconds_to_close_parses_iso_close_time():
    now = time.time()
    close_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600))
    result = mh.seconds_to_close(close_time, now)
    assert result is not None
    assert 3590 <= result <= 3610


def test_seconds_to_close_none_for_missing_or_bad_input():
    assert mh.seconds_to_close(None, time.time()) is None
    assert mh.seconds_to_close("not-a-date", time.time()) is None


def test_momentum_none_with_fewer_than_two_snapshots(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    now = time.time()
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.5, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now)
    assert mh.momentum("TICK-A", lookback_sec=1800, as_of=now) is None


def test_momentum_none_when_window_coverage_too_thin(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    now = time.time()
    # Two snapshots only 10 seconds apart, but a 1800s window requested -
    # nowhere near _MIN_WINDOW_COVERAGE, should be rejected as noise.
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.5, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now - 10)
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.6, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now)
    assert mh.momentum("TICK-A", lookback_sec=1800, as_of=now) is None


def test_momentum_computes_delta_over_covered_window(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    now = time.time()
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.4, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now - 1800)
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.6, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now)
    result = mh.momentum("TICK-A", lookback_sec=1800, as_of=now)
    assert result is not None
    assert round(result["delta"], 2) == 0.20
    assert result["from_price"] == 0.4
    assert result["to_price"] == 0.6
    assert round(result["span_sec"]) == 1800


def test_momentum_ignores_snapshots_after_as_of(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    now = time.time()
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.4, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now - 1800)
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.6, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now)
    # A snapshot from the "future" relative to as_of shouldn't be used.
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.99, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=now + 3600)
    result = mh.momentum("TICK-A", lookback_sec=1800, as_of=now)
    assert result["to_price"] == 0.6


def test_compute_hypothetical_trades_favors_side_price_implies(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    now = time.time()
    resolved_at = now
    # Snapshot 2 hours before resolution at yes_price 0.7 -> hypothetical
    # entry is "yes" (price > 0.5).
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.7, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=resolved_at - 7200)
    mh.record_outcome("TICK-A", "yes", resolved_at=resolved_at)

    trades = mh.compute_hypothetical_trades(lookback_windows_sec=(3600,))
    assert len(trades) == 1
    t = trades[0]
    assert t["ticker"] == "TICK-A"
    assert t["side"] == "yes"
    assert t["won"] is True
    # unit_cost = 0.7 (yes), payout = 1.0 (won) -> pnl = 0.3
    assert round(t["pnl_per_contract"], 2) == 0.30


def test_compute_hypothetical_trades_no_side_math(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    resolved_at = time.time()
    # yes_price 0.3 -> hypothetical entry is "no" (price <= 0.5); result is
    # "yes" -> the hypothetical no side lost.
    mh.record_snapshots([{"ticker": "TICK-B", "yes_price": 0.3, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=resolved_at - 7200)
    mh.record_outcome("TICK-B", "yes", resolved_at=resolved_at)

    trades = mh.compute_hypothetical_trades(lookback_windows_sec=(3600,))
    t = trades[0]
    assert t["side"] == "no"
    assert t["won"] is False
    # unit_cost = 1-0.3 = 0.7 (no side), payout = 0.0 (lost) -> pnl = -0.7
    assert round(t["pnl_per_contract"], 2) == -0.70


def test_compute_hypothetical_trades_skips_window_with_no_prior_snapshot(tmp_path, monkeypatch):
    _mh(tmp_path, monkeypatch)
    resolved_at = time.time()
    # Only a snapshot from 30 minutes before resolution - the 24h lookback
    # window has nothing to look at (no snapshot old enough).
    mh.record_snapshots([{"ticker": "TICK-A", "yes_price": 0.6, "spread": 0.01,
                           "volume_24h": 100, "time_to_close_sec": 100}], timestamp=resolved_at - 1800)
    mh.record_outcome("TICK-A", "yes", resolved_at=resolved_at)

    trades = mh.compute_hypothetical_trades(lookback_windows_sec=(86400,))
    assert trades == []
