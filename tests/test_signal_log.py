import asyncio
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


# --- series_of() resolves the real series_ticker via title_cache -----------
# (kalshi-category-data-completeness Task 3, docs/kalshi/terms.md:29's
# standing rule against parsing ticker strings: the naive
# ticker.split("-")[0] fallback above still applies when title_cache
# hasn't cached the market/event yet, but it's now the fallback, not the
# only path - see services/title_cache.py::series_ticker_for().)


def test_series_of_uses_title_cache_when_resolvable(tmp_path, monkeypatch):
    from services import title_cache
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    # Real-shape ticker from CHEATSHEET.md's KXMVECROSSCATEGORY0-SHARD1
    # gotcha: the real series_ticker itself contains a hyphen. This is the
    # discriminating case - ticker.split("-")[0] on the full 4-segment
    # ticker below truncates to "KXMVECROSSCATEGORY0", silently dropping
    # "-SHARD1", which is why this test (unlike a ticker with only one
    # internal hyphen) actually fails pre-fix and passes post-fix.
    ticker = "KXMVECROSSCATEGORY0-SHARD1-25NOV02-X"
    title_cache.save_market_titles({ticker: {
        "title": "T", "yes_sub_title": "", "no_sub_title": "", "event_ticker": "EVT-X"}})
    title_cache.save_event_titles({"EVT-X": {"series_ticker": "KXMVECROSSCATEGORY0-SHARD1"}})
    log = _log(tmp_path, monkeypatch)
    assert log.series_of(ticker) == "KXMVECROSSCATEGORY0-SHARD1"


def test_series_of_falls_back_to_prefix_when_unresolvable(tmp_path, monkeypatch):
    from services import title_cache
    monkeypatch.setattr(title_cache, "DB_PATH", tmp_path / "title_cache.db")
    log = _log(tmp_path, monkeypatch)
    assert log.series_of("KXBTC15M-26AUG29-B1") == "KXBTC15M"


def test_series_of_empty_ticker_unchanged():
    from services import signal_log
    assert signal_log.series_of("") == ""


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


def test_resolved_signals_with_factors_includes_the_stored_series(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("KXBTC15M-26AUG161645-45", "yes", 500, 0.6, "kalshi_trade_tape", factors={"depth_factor": 0.5})
    log.mark_resolved(1, correct=True)

    rows = log.resolved_signals_with_factors()

    assert rows[0]["series"] == "KXBTC15M"


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


# --- P8 Task 30: resolve from the settled lifecycle event, per ticker --------
# mark_resolved had exactly one caller in the whole app - main.py's 30s/200-
# batch REST poll - even though the same market_lifecycle_v2 `settled` event
# already resolves four other stores for the same ticker via WS. This is the
# ticker-scoped wrapper the WS path calls; each signal keeps its own side, so
# correctness is computed per row, not uniformly per ticker.

def test_resolve_from_market_results_resolves_every_unresolved_row_for_the_ticker(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    old = time.time() - 3600
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=old)
    log.log_signal("TICK-A", "no", 1000, 0.8, "simulated", seen_at=old)
    log.log_signal("TICK-B", "yes", 1000, 0.8, "simulated", seen_at=old)  # other ticker: untouched

    n = log.resolve_from_market_results("TICK-A", "yes")

    assert n == 2
    rows = {(r["ticker"], r["side"]): r for r in log.recent(limit=10)}
    assert rows[("TICK-A", "yes")]["resolved"] == 1 and rows[("TICK-A", "yes")]["correct"] == 1
    assert rows[("TICK-A", "no")]["resolved"] == 1 and rows[("TICK-A", "no")]["correct"] == 0
    assert rows[("TICK-B", "yes")]["resolved"] == 0
    assert [r["ticker"] for r in log.unresolved_batch(limit=10, older_than_sec=0)] == ["TICK-B"]


def test_resolve_from_market_results_is_idempotent_and_never_reopens(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=time.time() - 3600)
    assert log.resolve_from_market_results("TICK-A", "yes") == 1
    assert log.resolve_from_market_results("TICK-A", "no") == 0  # already resolved: no rows touched, verdict untouched
    row = log.recent(limit=1)[0]
    assert row["resolved"] == 1 and row["correct"] == 1


def test_resolve_from_market_results_with_no_rows_returns_zero(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    assert log.resolve_from_market_results("NOPE", "yes") == 0


# --- history-push trigger point (docs/superpowers/specs/2026-09-03-
# history-event-driven-design.md §2/§4.3 - loadBacktestSweeps/
# loadCalibrationReport are signal-resolution-driven) -----------------------


def test_mark_resolved_notifies_history_push(tmp_path, monkeypatch):
    from services import history_push
    log = _log(tmp_path, monkeypatch)

    calls = []
    monkeypatch.setattr(history_push, "mark_history_changed", lambda: calls.append(1))
    log.mark_resolved(1, correct=True)  # same "id needn't pre-exist" convention this file's other mark_resolved tests already use

    assert calls == [1]


def test_resolve_from_market_results_notifies_history_push_only_when_rows_change(tmp_path, monkeypatch):
    from services import history_push
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 1000, 0.8, "simulated", seen_at=time.time() - 3600)

    calls = []
    monkeypatch.setattr(history_push, "mark_history_changed", lambda: calls.append(1))

    assert log.resolve_from_market_results("NOPE", "yes") == 0  # no rows touched
    assert calls == []

    assert log.resolve_from_market_results("TICK-A", "yes") == 1  # a real write
    assert calls == [1]


def test_resolved_signals_with_factors_orders_by_seen_at_ascending(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    # Logged out of chronological order - the old behavior (unspecified
    # rowid order) would return them in insertion order here, which is
    # coincidentally reverse-chronological; ORDER BY seen_at ASC must not
    # depend on insertion order at all.
    log.log_signal("B", "yes", 100, 0.6, "real-provider", seen_at=200, factors={"depth_factor": 0.5})
    log.log_signal("A", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.log_signal("C", "yes", 100, 0.6, "real-provider", seen_at=300, factors={"depth_factor": 0.5})
    for row_id in (1, 2, 3):
        log.mark_resolved(row_id, correct=True)
    rows = log.resolved_signals_with_factors()
    # Tightened 2026-08-31 adversarial review: the prior "assert len(rows) == 3"
    # passes identically with or without ORDER BY seen_at ASC - it doesn't test
    # ordering despite the test's name. seen_at itself isn't in the returned
    # dict shape, but `series` now is (this task's SELECT was corrected to
    # keep it, see the query above) - tickers "A"/"B"/"C" have no hyphen, so
    # today's series_of() (ticker.split("-")[0]) maps each straight through
    # to its own name, giving a real per-row ordering probe with no schema
    # change needed:
    assert [r["series"] for r in rows] == ["A", "B", "C"]


def test_resolved_signals_with_factors_since_ts_scopes_the_window(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("OLD", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.log_signal("NEW", "yes", 100, 0.6, "real-provider", seen_at=500, factors={"depth_factor": 0.5})
    for row_id in (1, 2):
        log.mark_resolved(row_id, correct=True)
    assert len(log.resolved_signals_with_factors()) == 2  # default: unscoped, unchanged
    assert len(log.resolved_signals_with_factors(since_ts=300)) == 1  # only NEW


# --- strategy-edge-gate-implementation Task 5: Delta_calibrated's whole
# resolved-signal input population, NOT filtered to factors_json IS NOT NULL
# like resolved_signals_with_factors above (docs/superpowers/plans/
# 2026-09-03-strategy-edge-gate-implementation.md Task 5 - deliberately
# broader than its sibling per the design's Sec2.1 completeness argument).

def test_resolved_signals_for_edge_calibration_includes_rows_without_factors(tmp_path, monkeypatch):
    """Deliberately broader than resolved_signals_with_factors - this
    function's job is the FULL resolved population (design Sec2.1's
    completeness argument), not just the factors_json-populated subset."""
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 10, 0.7, "simulated", seen_at=1000.0, price=0.6)  # no factors=
    log.mark_resolved(1, correct=True)
    rows = log.resolved_signals_for_edge_calibration()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TICK-A"
    assert rows[0]["price"] == 0.6
    assert rows[0]["correct"] == 1


def test_resolved_signals_for_edge_calibration_respects_since_ts(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("TICK-A", "yes", 10, 0.7, "simulated", seen_at=1000.0, price=0.6)
    log.mark_resolved(1, correct=True)
    assert log.resolved_signals_for_edge_calibration(since_ts=2000.0) == []
    assert len(log.resolved_signals_for_edge_calibration(since_ts=500.0)) == 1


# --- write-path capacity fix Task 2: scoring reads use the cached connection
# pool (services/whalewatchers/_scoring_pool.py), not a fresh _connect() per
# call - per-trade connection churn on recent_sides_for_ticker/cluster_factor
# was a measured contributor to a live write-path capacity incident.

def test_recent_sides_for_ticker_uses_the_scoring_cache(tmp_path, monkeypatch):
    # Two deviations from the task's literal snippet, both verified against
    # the real source rather than assumed:
    #
    # 1. That version took only `monkeypatch` and asserted against the real,
    #    unpatched module-level DB_PATH (data/signal_log.db) - which
    #    CLAUDE.md's live-db rule says tests must never touch ("tests always
    #    monkeypatch DB_PATH to a tmp path"). Using this file's own
    #    _log(tmp_path, monkeypatch) helper keeps the same assertion (the
    #    cache is called with whatever DB_PATH currently is) while staying
    #    isolated from the live file.
    #
    # 2. signal_log.py cannot bind `_scoring_pool` as a module-level
    #    attribute (see _scoring_read_connection's docstring: services/
    #    whalewatchers/__init__.py eagerly imports kalshi_trade_tape.py,
    #    which needs services.signal_log.series_of at ITS OWN module top
    #    level - a top-level import the other way would be a genuine
    #    circular import, not a style choice). _scoring_read_connection
    #    imports services.whalewatchers._scoring_pool lazily at call time
    #    instead, so this test patches that real, single canonical module
    #    object directly rather than a `log._scoring_pool` attribute that
    #    doesn't exist - same behavior verified (the scoring-read path goes
    #    through cached_read_connection with the right db_path), just
    #    patched at its actual location.
    from services.whalewatchers import _scoring_pool

    log = _log(tmp_path, monkeypatch)
    calls = []
    real = _scoring_pool.cached_read_connection

    def spy(db_path, schema_init):
        calls.append(db_path)
        return real(db_path, schema_init)

    monkeypatch.setattr(_scoring_pool, "cached_read_connection", spy)
    log.recent_sides_for_ticker("KXTEST-25", since_ts=0)
    assert calls == [log.DB_PATH]


def test_scoring_read_connection_and_plain_connect_see_the_same_committed_data(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    # A row written via the plain, event-loop-side _connect() path must be
    # immediately visible through the cached scoring-read path - same file,
    # same WAL, no staleness introduced by caching.
    log.log_signal("KXTEST-VIS", "yes", 100, 0.9, "test", 12345.0)
    sides = log.recent_sides_for_ticker("KXTEST-VIS", since_ts=0)
    assert "yes" in sides


def test_connect_closes_its_connection(tmp_path, monkeypatch):
    """Same fd-leak class as Tasks 2-4 - 21 call sites in this module
    share one non-closing _connect().

    Deviation from the plan's literal Step 1 test (docs/superpowers/plans/
    2026-09-03-tier0-live-incident-remediation.md, Task 5): the plan's own
    text monkeypatches `conn.close` directly on a real sqlite3.Connection
    instance, but that raises `AttributeError: 'sqlite3.Connection' object
    attribute 'close' is read-only` on this container's Python (3.13.15,
    confirmed empirically, not assumed) - `close` is not instance-settable
    on that C type. This repo already has a working pattern for this exact
    fix shape (tests/test_pipeline_health_cost.py's `_RecordingConnection`,
    used by `test_probe_closes_its_connection` for Task 1's analogous
    close-tracking assertion): wrap the real connection instead of mutating
    it, delegate everything else via `__getattr__`, and also delegate
    `__enter__`/`__exit__` since `_connect()`'s body does `with conn:` for
    its own commit/rollback semantics (the fd-closing `finally: conn.
    close()` is a separate, outer step - see the fix itself)."""
    import sqlite3
    from services import signal_log as sl

    monkeypatch.setattr(sl, "DB_PATH", tmp_path / "signal_log.db")
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

    monkeypatch.setattr(sl.sqlite3, "connect", _tracking_connect)

    with sl._connect() as conn:
        conn.execute("SELECT 1")

    assert closed == [True]


def test_connect_closes_on_setup_failure(tmp_path, monkeypatch):
    """Same setup-failure leak class as market_history.py's analogous test:
    the try/finally only wraps `with conn: yield conn`, not the
    sqlite3.connect() + PRAGMA + _init_schema(conn) setup before it - a
    setup failure would otherwise leave `conn` open with nothing left to
    close it."""
    import sqlite3
    from services import signal_log as sl

    monkeypatch.setattr(sl, "DB_PATH", tmp_path / "signal_log.db")
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

    monkeypatch.setattr(sl.sqlite3, "connect", _tracking_connect)

    def _boom(conn):
        raise RuntimeError("schema init failed")

    monkeypatch.setattr(sl, "_init_schema", _boom)

    raised = None
    try:
        with sl._connect():
            pass
    except RuntimeError as exc:
        raised = exc

    assert raised is not None and "schema init failed" in str(raised)
    assert closed == [True], "connection must be closed even when setup (PRAGMA/_init_schema) raises before the try block"


# --- resolved_signals_with_factors_async / _ensure_schema_aio /
# materialize_signals_with_factors (issue #410) -----------------------------
#
# Adversarial review of this PR, finding F4: before this, only monkeypatch
# stubs of the async name existed, in tests/test_whale_calibration_routes.py.
# The reviewer wrote and ran an ad hoc probe confirming correctness on
# fresh-DB self-heal, since_ts filtering, ORDER BY seen_at, and malformed/
# excluded-row handling against production data - code was correct, but
# shipped on that equivalence claim rather than a repo test. These codify
# what the probe checked so it survives as a permanent guard, not one
# reviewer's transcript.


@pytest.fixture(autouse=True)
def _reset_aio_db_cache():
    """Same BINDING requirement as tests/test_candidate_log.py's fixture of
    this name (design Sec 3 / adversarial review finding C1): aiosqlite gives
    every cached connection a non-daemon OS thread, and CPython's shutdown
    joins those - a module that opens one and never resets it can print
    "N passed" and then hang forever. Also stops one test's tmp_path-scoped
    connection (deleted at teardown) from being handed to the next test."""
    yield
    from services.diagnostics import _aio_db
    asyncio.run(_aio_db.reset())


def test_resolved_signals_with_factors_async_matches_the_sync_version_exactly(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("A", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.log_signal("B", "yes", 100, 0.6, "real-provider", seen_at=200, factors={"depth_factor": 0.7})
    # Excluded and no-factors rows must be filtered out identically by both
    # paths - they share _resolved_with_factors_query precisely so this can
    # be asserted rather than trusted.
    log.log_signal("C-excl", "yes", 100, 0.6, "real-provider", seen_at=300, factors={"depth_factor": 0.9})
    log.log_signal("D-nofactors", "yes", 100, 0.6, "simulated", seen_at=400)
    for row_id in (1, 2, 3, 4):
        log.mark_resolved(row_id, correct=True)
    log.mark_excluded_range(after=250, before=350)  # covers only C-excl's seen_at=300

    sync_result = log.resolved_signals_with_factors()
    async_result = asyncio.run(log.resolved_signals_with_factors_async())

    assert async_result == sync_result
    assert [r["series"] for r in async_result] == ["A", "B"]


def test_resolved_signals_with_factors_async_self_heals_a_missing_schema(tmp_path, monkeypatch):
    """_connect() re-runs _init_schema on every sync call, so the sync read
    path has always repaired a missing table rather than raising. The async
    path must keep that via _ensure_schema_aio - without it, "no data yet"
    becomes a hard sqlite3.OperationalError instead of an empty list. Not
    theoretical: this surfaced immediately at implementation time against a
    fresh DB (see resolved_signals_with_factors_async's own docstring).

    The parent directory is created explicitly here rather than left to the
    async path: unlike services/db.py's connect() (candidate_log.py's sync
    path, which mkdirs `parents=True`), the raw `aiosqlite.connect()` this
    module's _ensure_schema_aio runs through does not create a missing
    parent directory (corrected 2026-09-05 docstring finding, F9) - this
    test covers a missing signal_log.db FILE within an existing directory,
    not a genuinely fresh checkout with no data/ dir at all."""
    fresh = tmp_path / "signal_log.db"
    monkeypatch.setattr(sl, "DB_PATH", fresh)

    assert asyncio.run(sl.resolved_signals_with_factors_async()) == []


def test_resolved_signals_with_factors_async_creates_every_index(tmp_path, monkeypatch):
    """If _ensure_schema_aio's delegation to the one real _init_schema ever
    stops covering the full schema, a query silently gets a worse plan
    rather than failing - assert the indexes directly rather than trusting
    the delegation."""
    log = _log(tmp_path, monkeypatch)
    asyncio.run(log.resolved_signals_with_factors_async())
    with log._connect() as conn:
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {
        "idx_signals_resolved", "idx_signals_series", "idx_signals_ticker_seen", "idx_signals_excluded",
    } <= indexes


def test_resolved_signals_with_factors_async_orders_by_seen_at_ascending(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("B", "yes", 100, 0.6, "real-provider", seen_at=200, factors={"depth_factor": 0.5})
    log.log_signal("A", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.log_signal("C", "yes", 100, 0.6, "real-provider", seen_at=300, factors={"depth_factor": 0.5})
    for row_id in (1, 2, 3):
        log.mark_resolved(row_id, correct=True)

    rows = asyncio.run(log.resolved_signals_with_factors_async())

    assert [r["series"] for r in rows] == ["A", "B", "C"]


def test_resolved_signals_with_factors_async_since_ts_scopes_the_window(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch)
    log.log_signal("OLD", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.log_signal("NEW", "yes", 100, 0.6, "real-provider", seen_at=500, factors={"depth_factor": 0.5})
    for row_id in (1, 2):
        log.mark_resolved(row_id, correct=True)

    assert len(asyncio.run(log.resolved_signals_with_factors_async())) == 2  # unscoped, unchanged
    assert len(asyncio.run(log.resolved_signals_with_factors_async(since_ts=300))) == 1  # only NEW


def test_materialize_signals_with_factors_skips_a_malformed_row_rather_than_raising(tmp_path, monkeypatch):
    """factors_json is written by json.dumps() at every real call site, so a
    malformed value here means a row from before a schema change, a
    hand-edited DB, or a genuinely corrupt write (data-plane HARD RULE's
    fidelity requirement) - never log_signal() itself, which is why this
    inserts directly rather than through it. materialize_signals_with_factors
    must skip it (`except (TypeError, ValueError): continue`), not crash the
    whole report over one bad row, and both the sync and async paths funnel
    through the same function so this exercises both."""
    log = _log(tmp_path, monkeypatch)
    log.log_signal("GOOD", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.mark_resolved(1, correct=True)
    with log._connect() as conn:
        conn.execute(
            "INSERT INTO signals (ticker, series, side, size, confidence, source, seen_at, "
            "factors_json, resolved, correct, excluded) VALUES "
            "('BAD', 'BAD', 'yes', 100, 0.6, 'real-provider', 200, 'not valid json', 1, 1, 0)"
        )

    sync_rows = log.resolved_signals_with_factors()
    async_rows = asyncio.run(log.resolved_signals_with_factors_async())

    assert [r["series"] for r in sync_rows] == ["GOOD"]
    assert [r["series"] for r in async_rows] == ["GOOD"]


def test_resolved_signals_with_factors_async_yields_to_the_event_loop(tmp_path, monkeypatch):
    """Design Sec 5's "assert the aiosqlite read path yields" detection
    requirement, for this module's own consumer - test_candidate_log.py's
    version of this test covers population_gate_summary_async, a separate
    read path against a separate DB file, so it does not exercise this one.

    Same F2-corrected shape as that sibling test (adversarial review of
    this PR): the ticker's progress is snapshotted at the instant the read
    call RETURNS, before the ticker task is awaited - awaiting it
    afterward would let it finish regardless of whether the read itself
    ever yielded, which is exactly the bug F2 found and fixed in the
    sibling test."""
    log = _log(tmp_path, monkeypatch)
    log.log_signal("A", "yes", 100, 0.6, "real-provider", seen_at=100, factors={"depth_factor": 0.5})
    log.mark_resolved(1, correct=True)

    import threading
    from services.diagnostics import _aio_db as aio_db_module

    progressed = []
    progress_at_read_return = []
    read_thread_ids = []
    original_connection_for = aio_db_module.connection_for

    async def _spy_connection_for(db_path, schema_init=None):
        conn = await original_connection_for(db_path, schema_init=schema_init)
        original_sync_fetchall = conn._execute_fetchall

        def _spy_sync_fetchall(sql, parameters):
            read_thread_ids.append(threading.get_ident())
            return original_sync_fetchall(sql, parameters)

        conn._execute_fetchall = _spy_sync_fetchall
        return conn

    async def _exercise():
        async def _ticker():
            for _ in range(50):
                await asyncio.sleep(0)
                progressed.append(1)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(aio_db_module, "connection_for", _spy_connection_for)
            ticker = asyncio.create_task(_ticker())
            result = await log.resolved_signals_with_factors_async()
        progress_at_read_return.append(len(progressed))
        await ticker
        return result

    result = asyncio.run(_exercise())
    assert result != []
    assert progress_at_read_return[0] > 0, (
        "the ticker made zero progress before the read returned - "
        "the async read never actually yielded control to the loop"
    )
    assert read_thread_ids, "execute_fetchall spy never recorded a call - test is broken"
    assert threading.get_ident() not in read_thread_ids, (
        "the SQL ran on the test's own (main) thread, not aiosqlite's worker thread"
    )
