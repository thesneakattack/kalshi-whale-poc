import time

from services import market_catalog as mc


def _mc(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "DB_PATH", tmp_path / "market_catalog.db")
    return mc


def _market(ticker, event_ticker, occurrence_offset_sec, volume=1000, close_offset_sec=None, status="active"):
    # "active" is the real REST response value (docs/kalshi/market_lifecycle.
    # md) - "open" is only ever a query filter value, never what a real
    # market object's own status field sends. See market_catalog.py's own
    # 2026-08-16 fix removing the dead status = 'open' SQL check.
    now = time.time()
    m = {
        "ticker": ticker, "event_ticker": event_ticker, "volume_24h_fp": str(volume),
        "occurrence_datetime": _iso(now + occurrence_offset_sec), "status": status,
    }
    if close_offset_sec is not None:
        m["close_time"] = _iso(now + close_offset_sec)
    return m


def _iso(unix_ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def test_upsert_and_candidates_in_window(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [_market("TICK-A", "EVT-A", occurrence_offset_sec=-300)]
    cat.upsert_markets("SER-A", "Sports", markets, updated_at=now)
    result = cat.candidates_in_window(now, lookahead_sec=3600, lookback_sec=21600)
    assert len(result) == 1
    assert result[0]["ticker"] == "TICK-A"


def test_candidates_in_window_carries_real_display_titles(tmp_path, monkeypatch):
    # Direct regression report: "the long string (KXLPLMATCH-...) is back
    # again. i want readable titles remember." Catalog rows were missing
    # title/yes_sub_title/no_sub_title entirely, so main.py's title-building
    # (m.get("title") or m.get("yes_sub_title") or m["ticker"]) fell all the
    # way through to the raw ticker for anything sourced via the catalog.
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    m = _market("TICK-A", "EVT-A", occurrence_offset_sec=-300)
    m["title"] = "Liverpool vs Chelsea"
    m["yes_sub_title"] = "Liverpool wins"
    m["no_sub_title"] = "Liverpool doesn't win"
    cat.upsert_markets("SER-A", "Sports", [m], updated_at=now)
    result = cat.candidates_in_window(now, lookahead_sec=3600, lookback_sec=21600)
    assert result[0]["title"] == "Liverpool vs Chelsea"
    assert result[0]["yes_sub_title"] == "Liverpool wins"
    assert result[0]["no_sub_title"] == "Liverpool doesn't win"


def test_upsert_overwrites_title_on_conflict(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    m1 = _market("TICK-A", "EVT-A", occurrence_offset_sec=-300)
    m1["title"] = "Old Title"
    cat.upsert_markets("SER-A", "Sports", [m1], updated_at=now)
    m2 = _market("TICK-A", "EVT-A", occurrence_offset_sec=-300)
    m2["title"] = "New Title"
    cat.upsert_markets("SER-A", "Sports", [m2], updated_at=now + 10)
    result = cat.candidates_in_window(now + 10, lookahead_sec=3600, lookback_sec=21600)
    assert result[0]["title"] == "New Title"


def test_candidates_in_window_excludes_markets_outside_bounds(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [_market("TICK-FAR-FUTURE", "EVT-A", occurrence_offset_sec=5 * 3600)]  # 5h out, past 3600 lookahead
    cat.upsert_markets("SER-A", "Sports", markets, updated_at=now)
    result = cat.candidates_in_window(now, lookahead_sec=3600, lookback_sec=21600)
    assert result == []


def test_upsert_skips_markets_far_beyond_the_near_term_horizon(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    # Direct scope correction: "not 6 months or a year from now" - a market
    # scheduled 6 months out should never even be stored.
    markets = [
        _market("TICK-SOON", "EVT-A", occurrence_offset_sec=2 * 24 * 3600),  # 2 days out - keep
        _market("TICK-FAR", "EVT-B", occurrence_offset_sec=180 * 24 * 3600),  # 6 months out - skip
    ]
    cat.upsert_markets("SER-A", "Sports", markets, updated_at=now)
    progress = cat.scan_progress()
    assert progress["total_markets"] == 1


def test_upsert_skips_markets_too_far_in_the_past(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [_market("TICK-OLD", "EVT-A", occurrence_offset_sec=-30 * 24 * 3600)]  # 30 days ago
    cat.upsert_markets("SER-A", "Sports", markets, updated_at=now)
    assert cat.scan_progress()["total_markets"] == 0


def test_upsert_skips_markets_with_no_occurrence_datetime(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    m = {"ticker": "TICK-NO-SCHEDULE", "event_ticker": "EVT-A", "volume_24h_fp": "1000", "status": "active"}
    cat.upsert_markets("SER-A", "Economics", [m])
    assert cat.scan_progress()["total_markets"] == 0


def test_upsert_is_idempotent_per_ticker(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    m1 = _market("TICK-A", "EVT-A", occurrence_offset_sec=-300, volume=1000)
    cat.upsert_markets("SER-A", "Sports", [m1], updated_at=now)
    m2 = _market("TICK-A", "EVT-A", occurrence_offset_sec=-300, volume=5000)  # same ticker, updated volume
    cat.upsert_markets("SER-A", "Sports", [m2], updated_at=now + 10)
    assert cat.scan_progress()["total_markets"] == 1
    result = cat.candidates_in_window(now + 10, lookahead_sec=3600, lookback_sec=21600)
    assert result[0]["volume_24h_fp"] == 5000.0


def test_candidates_in_window_respects_min_volume(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [
        _market("TICK-LOW", "EVT-A", occurrence_offset_sec=-300, volume=10),
        _market("TICK-HIGH", "EVT-B", occurrence_offset_sec=-300, volume=10000),
    ]
    cat.upsert_markets("SER-A", "Sports", markets, updated_at=now)
    result = cat.candidates_in_window(now, lookahead_sec=3600, lookback_sec=21600, min_volume=100)
    assert [r["ticker"] for r in result] == ["TICK-HIGH"]


def test_candidates_in_window_excludes_closed_status(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [_market("TICK-CLOSED", "EVT-A", occurrence_offset_sec=-300, status="closed")]
    cat.upsert_markets("SER-A", "Sports", markets, updated_at=now)
    result = cat.candidates_in_window(now, lookahead_sec=3600, lookback_sec=21600)
    assert result == []


# --- close_ts > now filter (2026-08-16 direct report: "im not seeing any
# positions being opened or signals being read" for KXBTC15M) - real,
# confirmed-live root cause: occurrence_datetime for this market shape is
# ~5min AFTER close_time (a settlement-checkpoint timestamp, not a "start of
# live window" one), so an already-closed instance can still fall inside the
# occurrence_ts window and its stale `status` column never self-corrects
# without a rescan - confirmed live, 3 real KXBTC15M rows sitting 2-8h
# stale, all already closed, all still reading status="active". -----------

def test_candidates_in_window_excludes_a_market_past_its_own_close_time(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    # occurrence_datetime deliberately AFTER close_time (the real KXBTC15M
    # shape) and status still "active" (stale, never corrected) - would
    # pass every other filter this query has.
    markets = [_market(
        "KXBTC15M-STALE", "KXBTC15M-EVT", occurrence_offset_sec=-295, close_offset_sec=-300, status="active",
    )]
    cat.upsert_markets("KXBTC15M", "Crypto", markets, updated_at=now)
    result = cat.candidates_in_window(now, lookahead_sec=3600, lookback_sec=21600)
    assert result == []


def test_candidates_in_window_includes_a_market_not_yet_closed(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [_market(
        "KXBTC15M-LIVE", "KXBTC15M-EVT", occurrence_offset_sec=300, close_offset_sec=180, status="active",
    )]
    cat.upsert_markets("KXBTC15M", "Crypto", markets, updated_at=now)
    result = cat.candidates_in_window(now, lookahead_sec=3600, lookback_sec=21600)
    assert [r["ticker"] for r in result] == ["KXBTC15M-LIVE"]


def test_open_candidates_excludes_a_market_past_its_own_close_time(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [_market(
        "KXBTC15M-STALE", "KXBTC15M-EVT", occurrence_offset_sec=-295, close_offset_sec=-300, status="active",
    )]
    cat.upsert_markets("KXBTC15M", "Crypto", markets, updated_at=now)
    result = cat.open_candidates(min_volume=0, now=now)
    assert result == []


def test_open_candidates_includes_a_market_not_yet_closed(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    markets = [_market(
        "KXBTC15M-LIVE", "KXBTC15M-EVT", occurrence_offset_sec=300, close_offset_sec=180, status="active",
    )]
    cat.upsert_markets("KXBTC15M", "Crypto", markets, updated_at=now)
    result = cat.open_candidates(min_volume=0, now=now)
    assert [r["ticker"] for r in result] == ["KXBTC15M-LIVE"]


# --- series_with_expired_data / next_series_to_scan priority (same
# 2026-08-16 incident) - a fast-rotating series (new market every 15
# minutes) needs to be rescanned far sooner than pure least-recently-
# scanned ordering alone can provide once the full series list is large
# enough that a whole rotation takes hours. -------------------------------

def test_series_with_expired_data_flags_a_series_whose_only_market_has_closed(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("KXBTC15M", "Crypto", [
        _market("KXBTC15M-OLD", "EVT-A", occurrence_offset_sec=-295, close_offset_sec=-300),
    ], updated_at=now)
    assert cat.series_with_expired_data(now) == {"KXBTC15M"}


def test_series_with_expired_data_excludes_a_series_still_trading(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("KXBTC15M", "Crypto", [
        _market("KXBTC15M-LIVE", "EVT-A", occurrence_offset_sec=300, close_offset_sec=180),
    ], updated_at=now)
    assert cat.series_with_expired_data(now) == set()


def test_series_with_expired_data_uses_the_most_recent_market_per_series(tmp_path, monkeypatch):
    # A series with one closed AND one still-open market (e.g. mid-rotation)
    # must not be flagged expired - it has real, currently-tradeable data.
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("KXBTC15M", "Crypto", [
        _market("KXBTC15M-OLD", "EVT-A", occurrence_offset_sec=-895, close_offset_sec=-900),
        _market("KXBTC15M-LIVE", "EVT-B", occurrence_offset_sec=300, close_offset_sec=180),
    ], updated_at=now)
    assert cat.series_with_expired_data(now) == set()


def test_next_series_to_scan_prioritizes_expired_data_over_recency(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    # KXBTC15M was scanned recently (would normally rank LAST by pure LRU)
    # but its only known market has already closed - must still come first.
    cat.upsert_markets("KXBTC15M", "Crypto", [
        _market("KXBTC15M-OLD", "EVT-A", occurrence_offset_sec=-295, close_offset_sec=-300),
    ], updated_at=now)
    cat.mark_scanned(["KXBTC15M"], scanned_at=now)  # scanned just now
    cat.mark_scanned(["SER-STALE-BUT-NOT-EXPIRED"], scanned_at=now - 1000)  # scanned longer ago
    all_series = [{"ticker": "KXBTC15M"}, {"ticker": "SER-STALE-BUT-NOT-EXPIRED"}]
    batch = cat.next_series_to_scan(all_series, batch_size=1, now=now)
    assert batch[0]["ticker"] == "KXBTC15M"


def test_next_series_to_scan_never_scanned_still_ranks_behind_expired(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("KXBTC15M", "Crypto", [
        _market("KXBTC15M-OLD", "EVT-A", occurrence_offset_sec=-295, close_offset_sec=-300),
    ], updated_at=now)
    all_series = [{"ticker": "KXBTC15M"}, {"ticker": "SER-NEVER-SCANNED"}]
    batch = cat.next_series_to_scan(all_series, batch_size=2, now=now)
    assert batch[0]["ticker"] == "KXBTC15M"


def test_next_series_to_scan_prioritizes_never_scanned(tmp_path, monkeypatch):
    _mc(tmp_path, monkeypatch)
    all_series = [{"ticker": "SER-A"}, {"ticker": "SER-B"}, {"ticker": "SER-C"}]
    batch = mc.next_series_to_scan(all_series, batch_size=2)
    assert len(batch) == 2  # no scan history at all yet - any 2 is fine, just confirms the size


def test_next_series_to_scan_prioritizes_least_recently_scanned(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    all_series = [{"ticker": "SER-A"}, {"ticker": "SER-B"}, {"ticker": "SER-C"}]
    cat.mark_scanned(["SER-A", "SER-B"], scanned_at=now)  # C never scanned - should come first
    cat.mark_scanned(["SER-C"], scanned_at=now - 1000)  # scanned, but longer ago than A/B
    batch = cat.next_series_to_scan(all_series, batch_size=1)
    assert batch[0]["ticker"] == "SER-C"


def test_mark_scanned_is_idempotent_and_updates_timestamp(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    cat.mark_scanned(["SER-A"], scanned_at=100.0)
    cat.mark_scanned(["SER-A"], scanned_at=200.0)
    all_series = [{"ticker": "SER-A"}, {"ticker": "SER-B"}]
    batch = cat.next_series_to_scan(all_series, batch_size=1)
    assert batch[0]["ticker"] == "SER-B"  # never-scanned SER-B still ranks before the re-scanned SER-A


def test_scan_progress_reports_counts(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("SER-A", "Sports", [_market("TICK-A", "EVT-A", occurrence_offset_sec=-300)], updated_at=now)
    cat.mark_scanned(["SER-A", "SER-B"], scanned_at=now)
    progress = cat.scan_progress()
    assert progress["scanned_series"] == 2
    assert progress["total_markets"] == 1
    assert progress["oldest_scan_at"] == now


def test_clear_all_wipes_both_tables(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("SER-A", "Sports", [_market("TICK-A", "EVT-A", occurrence_offset_sec=-300)], updated_at=now)
    cat.mark_scanned(["SER-A"], scanned_at=now)
    cat.clear_all()
    progress = cat.scan_progress()
    assert progress["scanned_series"] == 0
    assert progress["total_markets"] == 0


# --- open_candidates (2026-08-15 direct incident: "you made the market
# watch list and whale watching grind to a halt" - discovery now reads the
# already-persistent catalog instead of a fresh REST fetch per refresh) ---

def test_open_candidates_sorted_by_volume_descending(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("SER-A", "Sports", [_market("LOW", "EVT-A", occurrence_offset_sec=3600, volume=100)], updated_at=now)
    cat.upsert_markets("SER-B", "Sports", [_market("HIGH", "EVT-B", occurrence_offset_sec=3600, volume=9000)], updated_at=now)
    result = cat.open_candidates(min_volume=0)
    assert [r["ticker"] for r in result] == ["HIGH", "LOW"]


def test_open_candidates_filters_by_category(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("SER-A", "Sports", [_market("SPORT-1", "EVT-A", occurrence_offset_sec=3600)], updated_at=now)
    cat.upsert_markets("SER-B", "Crypto", [_market("CRYPTO-1", "EVT-B", occurrence_offset_sec=3600)], updated_at=now)
    result = cat.open_candidates(categories=["Sports"], min_volume=0)
    assert [r["ticker"] for r in result] == ["SPORT-1"]


def test_open_candidates_no_categories_returns_all(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("SER-A", "Sports", [_market("SPORT-1", "EVT-A", occurrence_offset_sec=3600)], updated_at=now)
    cat.upsert_markets("SER-B", "Crypto", [_market("CRYPTO-1", "EVT-B", occurrence_offset_sec=3600)], updated_at=now)
    result = cat.open_candidates(categories=None, min_volume=0)
    assert {r["ticker"] for r in result} == {"SPORT-1", "CRYPTO-1"}


def test_open_candidates_respects_min_volume(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("SER-A", "Sports", [_market("LOW", "EVT-A", occurrence_offset_sec=3600, volume=50)], updated_at=now)
    cat.upsert_markets("SER-B", "Sports", [_market("HIGH", "EVT-B", occurrence_offset_sec=3600, volume=5000)], updated_at=now)
    result = cat.open_candidates(min_volume=1000)
    assert [r["ticker"] for r in result] == ["HIGH"]


def test_open_candidates_min_volume_by_series_override(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    # KXBTC15M's entire tradeable lifetime is 15min - volume_24h is
    # genuinely 0 for every still-open instance (see open_candidates'
    # own docstring for the confirmed-live incident this fixes), so the
    # override needs to admit a zero-volume row the blanket min_volume
    # would otherwise reject.
    cat.upsert_markets("KXBTC15M", "Crypto", [_market("BTC-1", "EVT-BTC", occurrence_offset_sec=300, volume=0)], updated_at=now)
    cat.upsert_markets("KXMLBGAME", "Sports", [_market("MLB-1", "EVT-MLB", occurrence_offset_sec=300, volume=0)], updated_at=now)
    result = cat.open_candidates(min_volume=1000, min_volume_by_series={"KXBTC15M": 0})
    assert [r["ticker"] for r in result] == ["BTC-1"]


def test_open_candidates_min_volume_by_series_still_applies_default_elsewhere(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("KXBTC15M", "Crypto", [_market("BTC-1", "EVT-BTC", occurrence_offset_sec=300, volume=0)], updated_at=now)
    cat.upsert_markets("KXETH15M", "Crypto", [_market("ETH-1", "EVT-ETH", occurrence_offset_sec=300, volume=50)], updated_at=now)
    result = cat.open_candidates(min_volume=1000, min_volume_by_series={"KXBTC15M": 0})
    assert [r["ticker"] for r in result] == ["BTC-1"]


def test_open_candidates_excludes_closed_status(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    cat.upsert_markets("SER-A", "Sports", [_market("OPEN-1", "EVT-A", occurrence_offset_sec=3600, status="active")], updated_at=now)
    cat.upsert_markets("SER-B", "Sports", [_market("CLOSED-1", "EVT-B", occurrence_offset_sec=3600, status="closed")], updated_at=now)
    result = cat.open_candidates(min_volume=0)
    assert [r["ticker"] for r in result] == ["OPEN-1"]


def test_open_candidates_carries_real_display_titles_and_schedule(tmp_path, monkeypatch):
    cat = _mc(tmp_path, monkeypatch)
    now = time.time()
    m = _market("TICK-A", "EVT-A", occurrence_offset_sec=3600, close_offset_sec=7200)
    m["title"] = "Real Title"
    cat.upsert_markets("SER-A", "Sports", [m], updated_at=now)
    result = cat.open_candidates(min_volume=0)
    assert result[0]["title"] == "Real Title"
    assert result[0]["event_ticker"] == "EVT-A"
    assert "occurrence_datetime" in result[0]
    assert "close_time" in result[0]
