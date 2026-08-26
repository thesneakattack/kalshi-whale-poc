import time

import pytest

from services import signal_log as sl


def _log(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "DB_PATH", tmp_path / "signal_log.db")
    return sl


def test_log_signal_and_overall_stats(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A-123", "yes", 1000, 0.8, "simulated")
    stats = log.stats(days=30)
    assert stats["total_signals"] == 1
    assert stats["resolved"] == 0
    assert stats["win_rate"] is None


def test_series_extraction_uses_prefix_before_first_hyphen(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("KXOSCARBESTPICTURE-26-XYZ", "yes", 1000, 0.8, "simulated")
    stats = log.series_stats("KXOSCARBESTPICTURE-26-OTHER", days=30)
    assert stats["series"] == "KXOSCARBESTPICTURE"
    assert stats["total_signals"] == 1  # same series prefix, counted together


def test_unresolved_batch_respects_older_than_sec(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now)          # too recent
    log.log_signal("TICK-B", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)   # old enough
    batch = log.unresolved_batch(limit=10, older_than_sec=600)
    tickers = {row["ticker"] for row in batch}
    assert tickers == {"TICK-B"}


def test_unresolved_batch_excludes_a_row_checked_within_the_cooldown(tmp_path, monkeypatch):
    # Head-of-line-blocking fix (2026-08-15, direct live report: "why are
    # there SO MANY unresolved signals???") - a long-horizon signal (e.g. a
    # market closing next year) never resolves, so without this it would
    # sort to the front of every single batch forever and starve every
    # signal logged after it. First call claims+stamps it; a second call
    # within the cooldown window must skip it and surface something else.
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("STUCK-LONG-HORIZON", "yes", 1000, 0.8, "simulated", seen_at=now - 90000)  # oldest
    log.log_signal("NORMAL", "yes", 1000, 0.8, "simulated", seen_at=now - 700)

    first = log.unresolved_batch(limit=1, older_than_sec=600, recheck_cooldown_sec=3600)
    assert [r["ticker"] for r in first] == ["STUCK-LONG-HORIZON"]  # oldest first, as before

    second = log.unresolved_batch(limit=1, older_than_sec=600, recheck_cooldown_sec=3600)
    assert [r["ticker"] for r in second] == ["NORMAL"]  # stuck row's cooldown hasn't expired


def test_unresolved_batch_reoffers_a_row_once_its_cooldown_expires(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 700)
    log.unresolved_batch(limit=10, older_than_sec=600, recheck_cooldown_sec=0.01)
    time.sleep(0.02)
    second = log.unresolved_batch(limit=10, older_than_sec=600, recheck_cooldown_sec=0.01)
    assert [r["ticker"] for r in second] == ["TICK-A"]


def test_unresolved_batch_does_not_reclaim_an_already_resolved_row(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 700)
    batch = log.unresolved_batch(limit=10, older_than_sec=600, recheck_cooldown_sec=0.01)
    log.mark_resolved(batch[0]["id"], correct=True)
    time.sleep(0.02)
    second = log.unresolved_batch(limit=10, older_than_sec=600, recheck_cooldown_sec=0.01)
    assert second == []


def test_mark_resolved_updates_win_rate(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 1000)
    batch = log.unresolved_batch(limit=10, older_than_sec=600)
    assert len(batch) == 1
    log.mark_resolved(batch[0]["id"], correct=True)
    stats = log.stats(days=30)
    assert stats["resolved"] == 1
    assert stats["correct"] == 1
    assert stats["win_rate"] == 100.0


def test_series_stats_scoped_to_series_not_global(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("AAA-1", "yes", 1000, 0.8, "simulated")
    log.log_signal("BBB-1", "yes", 1000, 0.8, "simulated")
    stats = log.series_stats("AAA-2", days=30)  # different market, same AAA series
    assert stats["series"] == "AAA"
    assert stats["total_signals"] == 1


def test_window_days_excludes_old_signals(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 40 * 86400)  # 40 days ago
    stats = log.stats(days=30)
    assert stats["total_signals"] == 0


def test_all_series_stats_groups_every_series_in_one_call(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("AAA-1", "yes", 1000, 0.8, "simulated", seen_at=now - 100)
    log.log_signal("BBB-1", "yes", 1000, 0.8, "simulated", seen_at=now - 100)
    batch = log.unresolved_batch(limit=10, older_than_sec=0)
    for row in batch:
        log.mark_resolved(row["id"], correct=(row["ticker"] == "AAA-1"))
    stats = log.all_series_stats(days=30)
    assert set(stats.keys()) == {"AAA", "BBB"}
    assert stats["AAA"]["resolved"] == 1
    assert stats["AAA"]["win_rate"] == 100.0
    assert stats["BBB"]["win_rate"] == 0.0


def test_series_stats_bulk_matches_series_stats_per_ticker(tmp_path, monkeypatch):
    """realtime data-plane remediation plan, P1 Task 8: main.py's per-market
    series_stats N+1 loop (main.py:736, root-cause report C1) opens one
    _connect() per market ticker. series_stats_bulk must return exactly
    what calling series_stats(ticker, days) once per ticker would - same
    keys, same values - just on one connection with one query per unique
    series instead of N connections."""
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("AAA-1", "yes", 1000, 0.8, "simulated", seen_at=now - 100)
    log.log_signal("AAA-2", "no", 1000, 0.7, "simulated", seen_at=now - 50)
    log.log_signal("BBB-1", "yes", 1000, 0.9, "simulated", seen_at=now - 10)
    batch = log.unresolved_batch(limit=10, older_than_sec=0)
    for row in batch:
        log.mark_resolved(row["id"], correct=(row["ticker"] == "AAA-1"))

    tickers = ["AAA-1", "AAA-3", "BBB-1", "CCC-1"]  # AAA-3: same series, no signals of its own
    bulk = log.series_stats_bulk(tickers, days=30)

    assert set(bulk.keys()) == set(tickers)
    for ticker in tickers:
        assert bulk[ticker] == log.series_stats(ticker, days=30)


def test_series_stats_bulk_empty_list_returns_empty_dict(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    assert log.series_stats_bulk([], days=30) == {}


def test_all_series_stats_excludes_out_of_window_series(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("AAA-1", "yes", 1000, 0.8, "simulated", seen_at=now - 40 * 86400)
    stats = log.all_series_stats(days=30)
    assert stats == {}


def test_resolved_signals_with_series_returns_only_resolved_rows(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("AAA-1", "yes", 1000, 0.8, "simulated", seen_at=now - 100)
    log.log_signal("BBB-1", "yes", 1000, 0.8, "simulated", seen_at=now - 100)  # left unresolved
    batch = log.unresolved_batch(limit=10, older_than_sec=0)
    aaa_row = next(r for r in batch if r["ticker"] == "AAA-1")
    log.mark_resolved(aaa_row["id"], correct=True)
    rows = log.resolved_signals_with_series(days=30)
    assert rows == [{"series": "AAA", "correct": True}]


def test_clear_all_wipes_every_signal(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated")
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated")
    log.clear_all()
    stats = log.stats(days=30)
    assert stats["total_signals"] == 0
    assert log.series_stats("TICK-A", days=30)["total_signals"] == 0


def test_recent_returns_newest_first(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 100)
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated", seen_at=now)
    rows = log.recent(limit=10)
    assert [r["ticker"] for r in rows] == ["TICK-B", "TICK-A"]


def test_recent_respects_limit_and_offset(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    for i in range(5):
        log.log_signal(f"TICK-{i}", "yes", 1000, 0.8, "simulated", seen_at=now - i)
    first_page = log.recent(limit=2, offset=0)
    second_page = log.recent(limit=2, offset=2)
    assert [r["ticker"] for r in first_page] == ["TICK-0", "TICK-1"]
    assert [r["ticker"] for r in second_page] == ["TICK-2", "TICK-3"]


def test_recent_resolved_only_excludes_unresolved(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=now - 700)
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated", seen_at=now)
    batch = log.unresolved_batch(limit=10, older_than_sec=600)
    assert len(batch) == 1
    log.mark_resolved(batch[0]["id"], correct=True)

    all_rows = log.recent(limit=10)
    resolved_rows = log.recent(limit=10, resolved_only=True)
    assert len(all_rows) == 2
    assert len(resolved_rows) == 1
    assert resolved_rows[0]["ticker"] == "TICK-A"
    assert resolved_rows[0]["correct"] == 1

    unresolved_row = next(r for r in all_rows if r["ticker"] == "TICK-B")
    assert unresolved_row["resolved"] == 0
    assert unresolved_row["correct"] is None


def test_total_count(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated")
    log.log_signal("TICK-B", "no", 2000, 0.7, "simulated")
    assert log.total_count() == 2
    assert log.total_count(resolved_only=True) == 0


# ---- cluster_factor (live, per-signal analog of find_clusters) -------------

def test_cluster_factor_is_zero_with_no_recent_history(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    # Isolated print - nothing to compare against - scores 0.0, not the
    # neutral 0.5 recent_sides_for_ticker/agreement_factor would use, since
    # "no similar prints nearby" is itself informative here.
    assert log.cluster_factor("TICK-A", "yes", 5000, since_ts=now - 1800) == 0.0


def test_cluster_factor_scales_up_with_more_size_compatible_prints(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 5000, 0.7, "kalshi_trade_tape", seen_at=now - 600)
    one = log.cluster_factor("TICK-A", "yes", 5200, since_ts=now - 1800)
    log.log_signal("TICK-A", "yes", 5500, 0.7, "kalshi_trade_tape", seen_at=now - 300)
    two = log.cluster_factor("TICK-A", "yes", 5200, since_ts=now - 1800)
    log.log_signal("TICK-A", "yes", 4800, 0.7, "kalshi_trade_tape", seen_at=now - 100)
    three = log.cluster_factor("TICK-A", "yes", 5200, since_ts=now - 1800)
    assert 0.0 < one < two < three
    assert three == 1.0  # capped at 3 matching prints


def test_cluster_factor_ignores_size_mismatched_prints(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 500, 0.7, "kalshi_trade_tape", seen_at=now - 300)  # far outside a 4x ratio
    assert log.cluster_factor("TICK-A", "yes", 50000, since_ts=now - 1800, max_size_ratio=4.0) == 0.0


def test_cluster_factor_ignores_different_ticker_or_side(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 5000, 0.7, "kalshi_trade_tape", seen_at=now - 300)
    log.log_signal("TICK-B", "yes", 5000, 0.7, "kalshi_trade_tape", seen_at=now - 300)
    log.log_signal("TICK-A", "no", 5000, 0.7, "kalshi_trade_tape", seen_at=now - 300)
    assert log.cluster_factor("TICK-A", "yes", 5000, since_ts=now - 1800) == pytest.approx(1 / 3)


def test_cluster_factor_respects_since_ts_window(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 5000, 0.7, "kalshi_trade_tape", seen_at=now - 7200)  # 2h ago, outside window
    assert log.cluster_factor("TICK-A", "yes", 5000, since_ts=now - 1800) == 0.0


def test_find_clusters_groups_close_similar_prints(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 5000, 0.7, "simulated", seen_at=now - 600)
    log.log_signal("TICK-A", "yes", 6000, 0.75, "simulated", seen_at=now - 300)
    log.log_signal("TICK-A", "yes", 5500, 0.8, "simulated", seen_at=now)
    clusters = log.find_clusters(hours=24, time_window_min=30, max_size_ratio=4.0)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["ticker"] == "TICK-A"
    assert c["side"] == "yes"
    assert c["print_count"] == 3
    assert c["total_size"] == 16500


def test_find_clusters_ignores_lone_signals(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 5000, 0.7, "simulated")
    log.log_signal("TICK-B", "no", 3000, 0.6, "simulated")
    clusters = log.find_clusters(hours=24)
    assert clusters == []


def test_find_clusters_splits_on_time_gap(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 5000, 0.7, "simulated", seen_at=now - 7200)  # 2h ago
    log.log_signal("TICK-A", "yes", 5200, 0.7, "simulated", seen_at=now)  # now - way outside a 30min window
    clusters = log.find_clusters(hours=24, time_window_min=30)
    assert clusters == []  # each print is alone in its own would-be cluster


def test_find_clusters_splits_on_size_mismatch(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 500, 0.7, "simulated", seen_at=now - 60)
    log.log_signal("TICK-A", "yes", 50000, 0.7, "simulated", seen_at=now)  # same ticker/side/timing, wildly different size
    clusters = log.find_clusters(hours=24, time_window_min=30, max_size_ratio=4.0)
    assert clusters == []


def test_find_clusters_keeps_different_tickers_and_sides_separate(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 5000, 0.7, "simulated", seen_at=now - 60)
    log.log_signal("TICK-A", "no", 5100, 0.7, "simulated", seen_at=now - 30)  # same ticker, different side
    log.log_signal("TICK-B", "yes", 5200, 0.7, "simulated", seen_at=now)  # different ticker
    clusters = log.find_clusters(hours=24)
    assert clusters == []


def test_find_clusters_confidence_increases_with_more_prints(tmp_path, monkeypatch):
    now = time.time()

    monkeypatch.setattr(sl, "DB_PATH", tmp_path / "two_prints.db")
    for i in range(2):
        sl.log_signal("TICK-A", "yes", 5000, 0.7, "simulated", seen_at=now - (60 * i))
    two_print_conf = sl.find_clusters(hours=24)[0]["cluster_confidence"]

    monkeypatch.setattr(sl, "DB_PATH", tmp_path / "five_prints.db")
    for i in range(5):
        sl.log_signal("TICK-A", "yes", 5000, 0.7, "simulated", seen_at=now - (60 * i))
    five_print_conf = sl.find_clusters(hours=24)[0]["cluster_confidence"]

    assert five_print_conf > two_print_conf


def test_find_clusters_sorted_by_total_size_descending(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-SMALL", "yes", 1000, 0.7, "simulated", seen_at=now - 60)
    log.log_signal("TICK-SMALL", "yes", 1100, 0.7, "simulated", seen_at=now)
    log.log_signal("TICK-BIG", "no", 40000, 0.7, "simulated", seen_at=now - 60)
    log.log_signal("TICK-BIG", "no", 42000, 0.7, "simulated", seen_at=now)
    clusters = log.find_clusters(hours=24)
    assert [c["ticker"] for c in clusters] == ["TICK-BIG", "TICK-SMALL"]


def test_recent_sides_for_ticker_scoped_to_ticker_and_window(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    now = time.time()
    log.log_signal("TICK-A", "yes", 500, 0.7, "kalshi_trade_tape", seen_at=now - 60)
    log.log_signal("TICK-A", "no", 500, 0.7, "kalshi_trade_tape", seen_at=now - 3600 * 10)  # too old
    log.log_signal("TICK-B", "yes", 500, 0.7, "kalshi_trade_tape", seen_at=now - 60)  # different ticker
    sides = log.recent_sides_for_ticker("TICK-A", since_ts=now - 3600)
    assert sides == ["yes"]


def test_log_signal_persists_factors_json_roundtrip(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    factors = {"depth_factor": 0.8, "unusualness_factor": 0.3, "proximity_factor": 0.0,
               "context_factor": 0.5, "agreement_factor": 0.5, "score": 0.6}
    log.log_signal("TICK-A", "yes", 500, 0.6, "kalshi_trade_tape", factors=factors)
    log.mark_resolved(1, correct=True)
    rows = log.resolved_signals_with_factors()
    assert len(rows) == 1
    assert rows[0]["factors"] == factors
    assert rows[0]["correct"] is True


def test_log_signal_persists_raw_context_roundtrip(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    raw_context = {"notional_usd": 4200.5, "spread": 0.02, "volume_24h": 15000.0}
    log.log_signal("TICK-A", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.5}, raw_context=raw_context)
    log.mark_resolved(1, correct=True)
    rows = log.resolved_signals_with_factors()
    assert rows[0]["raw_notional_usd"] == 4200.5
    assert rows[0]["raw_spread"] == 0.02
    assert rows[0]["raw_volume_24h"] == 15000.0


def test_log_signal_without_raw_context_leaves_raw_fields_null(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.5})  # no raw_context
    log.mark_resolved(1, correct=True)
    rows = log.resolved_signals_with_factors()
    assert rows[0]["raw_notional_usd"] is None
    assert rows[0]["raw_spread"] is None
    assert rows[0]["raw_volume_24h"] is None


def test_resolved_signals_with_factors_excludes_rows_without_a_breakdown(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 500, 0.6, "simulated")  # no factors= passed - simulator-style
    log.mark_resolved(1, correct=True)
    assert log.resolved_signals_with_factors() == []


def test_resolved_signals_with_factors_excludes_unresolved_rows(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.5})
    assert log.resolved_signals_with_factors() == []  # never resolved


def test_resolved_with_factors_count_matches_len_of_resolved_signals_with_factors(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.5})
    log.log_signal("TICK-B", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.7})
    log.log_signal("TICK-C", "yes", 500, 0.6, "simulated")  # no factors - excluded either way
    log.mark_resolved(1, correct=True)
    log.mark_resolved(2, correct=False)
    log.mark_resolved(3, correct=True)
    assert log.resolved_with_factors_count() == len(log.resolved_signals_with_factors()) == 2


def test_resolved_with_factors_count_excludes_unresolved_rows(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.5})
    assert log.resolved_with_factors_count() == 0  # never resolved


def test_connect_enables_wal_mode(tmp_path, monkeypatch):
    # Real live incident (2026-08-11) - signal_log.py is on the exact hot
    # path (recent_sides_for_ticker/cluster_factor read from it on every
    # qualifying whale print) that froze the app when trade-tape volume
    # went uncapped; WAL mode lets readers proceed concurrently with a
    # writer instead of serializing every access.
    log = _log(tmp_path, monkeypatch)
    with log._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
