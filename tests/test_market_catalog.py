import time

from services import market_catalog as mc


def _mc(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "DB_PATH", tmp_path / "market_catalog.db")
    return mc


def _market(ticker, event_ticker, occurrence_offset_sec, volume=1000, close_offset_sec=None, status="open"):
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
    m = {"ticker": "TICK-NO-SCHEDULE", "event_ticker": "EVT-A", "volume_24h_fp": "1000", "status": "open"}
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


def test_next_series_to_scan_prioritizes_never_scanned():
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
