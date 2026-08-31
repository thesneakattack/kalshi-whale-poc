import asyncio
import time
from datetime import datetime, timezone

import pytest
from services import candlestick_volatility as cv
from services.app_state import state
from services.market_catalog import market_catalog


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cv, "DB_PATH", tmp_path / "candlestick_volatility.db")


@pytest.fixture(autouse=True)
def _isolate_scan_state(tmp_path, monkeypatch):
    # Mirrors tests/test_mve_scan.py's fixture shape: an isolated
    # market_catalog.db (never the live data/*.db - CLAUDE.md) plus a reset
    # of the real app_state.state keys this module's scan reads/writes
    # directly (state["candlestick_volatility_scan"], state["markets"]).
    monkeypatch.setattr(market_catalog, "DB_PATH", tmp_path / "market_catalog.db")
    state["candlestick_volatility_scan"] = {"scanning": False, "last_started_at": 0.0, "task": None}
    state["markets"] = []


def _bar(end_period_ts, yes_bid_close, yes_bid_open=None, yes_bid_high=None, yes_bid_low=None,
         price_close=None, volume_fp=10.0, open_interest_fp=5.0):
    # Defaults let existing single-value tests keep passing unchanged
    # (open/high/low collapse to the same value as close when not given
    # explicitly) while dedicated OHLC tests pass all four.
    yes_bid_open = yes_bid_close if yes_bid_open is None else yes_bid_open
    yes_bid_high = yes_bid_close if yes_bid_high is None else yes_bid_high
    yes_bid_low = yes_bid_close if yes_bid_low is None else yes_bid_low
    return {
        "end_period_ts": end_period_ts,
        "yes_bid": {
            "open_dollars": yes_bid_open, "high_dollars": yes_bid_high,
            "low_dollars": yes_bid_low, "close_dollars": yes_bid_close,
        },
        "price": {"close_dollars": price_close},
        "volume_fp": volume_fp,
        "open_interest_fp": open_interest_fp,
    }


def test_record_candles_stores_full_yes_bid_ohlc_not_just_close():
    bars = [_bar(1000.0, yes_bid_close=0.42, yes_bid_open=0.38, yes_bid_high=0.45, yes_bid_low=0.36, price_close=None)]
    n = cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    assert n == 1
    with cv._connect() as conn:
        row = conn.execute(
            "SELECT yes_bid_open, yes_bid_high, yes_bid_low, yes_bid_close FROM candles WHERE ticker = ?",
            ("TICK-A",),
        ).fetchone()
    assert row == (0.38, 0.45, 0.36, 0.42)


def test_record_candles_skips_a_bar_missing_end_period_ts_or_yes_bid_close():
    bars = [
        {"yes_bid": {"close_dollars": 0.5}, "price": {}, "volume_fp": 1, "open_interest_fp": 1},
        {"end_period_ts": 1100.0, "yes_bid": {}, "price": {}, "volume_fp": 1, "open_interest_fp": 1},
        _bar(1200.0, 0.55),
    ]
    n = cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    assert n == 1
    # A bar with open_dollars/high_dollars/low_dollars present but close_dollars
    # missing is also skipped - close is the one currently used by volatility(),
    # so a bar without it is as unusable as one missing end_period_ts.
    bad_bar = {
        "end_period_ts": 1300.0,
        "yes_bid": {"open_dollars": 0.5, "high_dollars": 0.5, "low_dollars": 0.5},
        "price": {}, "volume_fp": 1, "open_interest_fp": 1,
    }
    n2 = cv.record_candles("TICK-A", "SERIES-A", 60, [bad_bar], fetched_at=2000.0)
    assert n2 == 0


def test_record_candles_upserts_idempotently_on_overlapping_refetch():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.48)], fetched_at=2100.0)
    with cv._connect() as conn:
        rows = conn.execute(
            "SELECT yes_bid_close FROM candles WHERE ticker = ? AND end_period_ts = ?",
            ("TICK-A", 1000.0),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0.48


def test_volatility_none_with_fewer_than_three_bars_in_window():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40), _bar(1060.0, 0.42)], fetched_at=2000.0)
    assert cv.volatility("TICK-A", lookback_sec=200, as_of=1100.0) is None


def test_volatility_computes_population_stdev_of_consecutive_close_deltas():
    bars = [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39), _bar(1180.0, 0.45)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    deltas = [0.02, -0.03, 0.06]
    mean = sum(deltas) / 3
    expected = (sum((d - mean) ** 2 for d in deltas) / 3) ** 0.5
    result = cv.volatility("TICK-A", lookback_sec=200, as_of=1180.0)
    assert result == pytest.approx(expected)


def test_volatility_ignores_bars_outside_the_lookback_window():
    bars = [_bar(500.0, 0.10), _bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    result = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0)
    deltas = [0.02, -0.03]
    mean = sum(deltas) / 2
    expected = (sum((d - mean) ** 2 for d in deltas) / 2) ** 0.5
    assert result == pytest.approx(expected)


def test_volatility_ignores_bars_after_as_of():
    bars = [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39), _bar(9999.0, 0.99)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    result = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0)
    assert result is not None and result < 0.5


def test_volatility_respects_period_interval_min_filter():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39)], fetched_at=2000.0)
    cv.record_candles("TICK-A", "SERIES-A", 1, [_bar(1000.0, 0.90), _bar(1010.0, 0.10), _bar(1020.0, 0.90)], fetched_at=2000.0)
    result_60 = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0, period_interval_min=60)
    result_1 = cv.volatility("TICK-A", lookback_sec=200, as_of=1120.0, period_interval_min=1)
    assert result_60 is not None and result_1 is not None
    assert result_60 != result_1


def test_last_fetched_at_returns_the_most_recent_fetched_at_for_a_ticker():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1060.0, 0.42)], fetched_at=2500.0)
    assert cv.last_fetched_at("TICK-A") == 2500.0


def test_last_fetched_at_returns_none_for_an_unfetched_ticker():
    assert cv.last_fetched_at("NEVER-FETCHED") is None


def test_bar_count_and_clear_all():
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    cv.record_candles("TICK-B", "SERIES-B", 60, [_bar(1000.0, 0.40)], fetched_at=2000.0)
    assert cv.bar_count() == 2
    assert cv.bar_count("TICK-A") == 1
    cv.clear_all()
    assert cv.bar_count() == 0


# --- background scan: mirrors tests/test_mve_scan.py's real conventions ----

def _iso(unix_ts):
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_series_ticker(ticker, series_ticker, close_offset_sec=3600):
    # Real catalog row via the same upsert_mve_markets path mve_scan uses -
    # gives series_ticker_for(ticker) a real answer without needing a full
    # get_markets()-shaped payload (upsert_mve_markets only requires
    # ticker/close_time within the near-term horizon).
    now = time.time()
    market_catalog.upsert_mve_markets([{
        "ticker": ticker, "event_ticker": f"EVT-{ticker}", "series_ticker": series_ticker,
        "category": "Crypto", "status": "active", "close_time": _iso(now + close_offset_sec),
    }])


class _FakeCandlestickClient:
    """Stands in for KalshiPublicGateway - only the one method
    candlestick_volatility.py actually calls."""

    def __init__(self, candles_by_ticker=None, fail_tickers=None):
        self._candles_by_ticker = candles_by_ticker or {}
        self._fail_tickers = fail_tickers or set()
        self.calls = []  # (ticker, series_ticker, start_ts, end_ts, period_interval)
        self.in_flight = 0
        self.max_in_flight = 0

    async def get_candlesticks(self, series_ticker, ticker, start_ts, end_ts, period_interval):
        self.calls.append((ticker, series_ticker, start_ts, end_ts, period_interval))
        if ticker in self._fail_tickers:
            raise RuntimeError("simulated transient API error")
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.005)
        self.in_flight -= 1
        return {"candlesticks": self._candles_by_ticker.get(ticker, [])}


# --- _maybe_scan_candlestick_volatility: due()/overlap-guard scheduling ----

def test_maybe_scan_candlestick_volatility_triggers_when_due_and_not_scanning(monkeypatch):
    supervised = []
    monkeypatch.setattr(cv.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel")

    cv._maybe_scan_candlestick_volatility({"candlestick_volatility": {"enabled": True}})

    assert len(supervised) == 1
    assert supervised[0]["component"] == "candlestick_volatility_scan"
    assert state["candlestick_volatility_scan"]["scanning"] is True
    assert state["candlestick_volatility_scan"]["task"] == "task-sentinel"


def test_maybe_scan_candlestick_volatility_does_not_refire_before_interval_elapses(monkeypatch):
    supervised = []
    monkeypatch.setattr(cv.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel")
    state["candlestick_volatility_scan"]["last_started_at"] = time.time()  # just started

    cv._maybe_scan_candlestick_volatility({"candlestick_volatility": {"enabled": True}})

    assert supervised == []


def test_maybe_scan_candlestick_volatility_does_not_overlap_a_run_already_in_flight(monkeypatch):
    supervised = []
    monkeypatch.setattr(cv.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel")
    state["candlestick_volatility_scan"]["scanning"] = True
    state["candlestick_volatility_scan"]["last_started_at"] = 0.0  # otherwise due()

    cv._maybe_scan_candlestick_volatility({"candlestick_volatility": {"enabled": True}})

    assert supervised == []


def test_maybe_scan_candlestick_volatility_noops_when_disabled(monkeypatch):
    supervised = []
    monkeypatch.setattr(cv.task_supervisor, "supervise", lambda fn, **kw: supervised.append(kw) or "task-sentinel")
    state["candlestick_volatility_scan"]["last_started_at"] = 0.0  # due() would otherwise be True

    cv._maybe_scan_candlestick_volatility({"candlestick_volatility": {"enabled": False}})

    assert supervised == []


# --- _scan_candlestick_volatility_batch: the real end-to-end wiring -------

def test_scan_batch_uses_last_fetched_at_as_a_watermark_not_the_full_window():
    _seed_series_ticker("TICK-A", "SERIES-A")
    _seed_series_ticker("TICK-B", "SERIES-B")
    old_fetched_at = time.time() - 3600  # newer than full_window_start_ts (now - 86400)
    cv.record_candles("TICK-A", "SERIES-A", 60, [_bar(1000.0, 0.40)], fetched_at=old_fetched_at)
    state["markets"] = [{"ticker": "TICK-A"}, {"ticker": "TICK-B"}]

    before = time.time()
    client = _FakeCandlestickClient()
    asyncio.run(cv._scan_candlestick_volatility_batch(client, {"candlestick_volatility": {}}))
    after = time.time()

    calls_by_ticker = {c[0]: c for c in client.calls}
    # TICK-A already has history - the watermark (its own last_fetched_at),
    # not now - lookback_window_sec, is the start_ts actually used.
    assert calls_by_ticker["TICK-A"][2] == pytest.approx(old_fetched_at, abs=2)
    # TICK-B has never been fetched - the full 86400s default lookback
    # window still applies, in the very same batch as TICK-A's watermarked
    # fetch.
    assert (before - 86400) - 2 <= calls_by_ticker["TICK-B"][2] <= (after - 86400) + 2


def test_scan_batch_skips_a_ticker_with_no_series_ticker_in_the_catalog():
    _seed_series_ticker("TICK-A", "SERIES-A")
    # TICK-UNCATALOGUED deliberately never seeded - series_ticker_for(...) is None.
    state["markets"] = [{"ticker": "TICK-A"}, {"ticker": "TICK-UNCATALOGUED"}]

    client = _FakeCandlestickClient()
    asyncio.run(cv._scan_candlestick_volatility_batch(client, {"candlestick_volatility": {}}))

    called_tickers = {c[0] for c in client.calls}
    assert "TICK-UNCATALOGUED" not in called_tickers
    assert "TICK-A" in called_tickers


def test_scan_batch_isolates_a_failing_ticker_from_the_rest(capsys):
    _seed_series_ticker("TICK-A", "SERIES-A")
    _seed_series_ticker("TICK-B", "SERIES-B")
    state["markets"] = [{"ticker": "TICK-A"}, {"ticker": "TICK-B"}]

    client = _FakeCandlestickClient(
        candles_by_ticker={"TICK-B": [_bar(1000.0, 0.40)]},
        fail_tickers={"TICK-A"},
    )
    asyncio.run(cv._scan_candlestick_volatility_batch(client, {"candlestick_volatility": {}}))

    assert cv.bar_count("TICK-A") == 0
    assert cv.bar_count("TICK-B") > 0
    assert "scan failed for 'TICK-A'" in capsys.readouterr().out


def test_scan_batch_paces_concurrent_fetches():
    n_tickers = cv._CANDLESTICK_PACE_LIMIT + 4
    tickers = [f"TICK-{i}" for i in range(n_tickers)]
    for ticker in tickers:
        _seed_series_ticker(ticker, f"SERIES-{ticker}")
    state["markets"] = [{"ticker": t} for t in tickers]

    client = _FakeCandlestickClient()
    asyncio.run(cv._scan_candlestick_volatility_batch(client, {"candlestick_volatility": {}}))

    assert client.max_in_flight <= cv._CANDLESTICK_PACE_LIMIT
    assert len(client.calls) == n_tickers  # every ticker still reached the call, just paced


# --- _scan_candlestick_volatility_batch_background: lifecycle -------------

def test_scan_batch_background_closes_the_client_and_clears_scanning(monkeypatch):
    closed = []

    class _FakeGateway:
        def __init__(self, base_url, timeout):
            pass

        async def get_candlesticks(self, series_ticker, ticker, start_ts, end_ts, period_interval):
            return {"candlesticks": []}

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(cv, "KalshiPublicGateway", _FakeGateway)
    state["candlestick_volatility_scan"]["scanning"] = True
    state["markets"] = []

    asyncio.run(cv._scan_candlestick_volatility_batch_background(
        {"kalshi": {"base_url": "https://x", "request_timeout_sec": 1.0}, "candlestick_volatility": {}}
    ))

    assert closed == [True]
    assert state["candlestick_volatility_scan"]["scanning"] is False


def test_scan_batch_background_clears_scanning_even_when_the_scan_raises(monkeypatch):
    closed = []

    class _FakeGateway:
        def __init__(self, base_url, timeout):
            pass

        async def close(self):
            closed.append(True)

    async def _raise(client, cfg):
        raise RuntimeError("boom")

    monkeypatch.setattr(cv, "KalshiPublicGateway", _FakeGateway)
    monkeypatch.setattr(cv, "_scan_candlestick_volatility_batch", _raise)

    with pytest.raises(RuntimeError):
        asyncio.run(cv._scan_candlestick_volatility_batch_background(
            {"kalshi": {"base_url": "https://x", "request_timeout_sec": 1.0}, "candlestick_volatility": {}}
        ))

    assert closed == [True]
    assert state["candlestick_volatility_scan"]["scanning"] is False


# --- comparison_report: diagnostics (Task 6) --------------------------------

def test_comparison_report_reports_none_delta_when_either_side_lacks_history(monkeypatch):
    bars = [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    monkeypatch.setattr("services.market_history.volatility", lambda *a, **k: None)
    report = cv.comparison_report(["TICK-A"], lookback_sec=200, as_of=1120.0)
    assert report[0]["ticker"] == "TICK-A"
    assert report[0]["candlestick_volatility"] is not None
    assert report[0]["snapshot_volatility"] is None
    assert report[0]["delta"] is None


def test_comparison_report_computes_delta_when_both_sides_have_history(monkeypatch):
    bars = [_bar(1000.0, 0.40), _bar(1060.0, 0.42), _bar(1120.0, 0.39)]
    cv.record_candles("TICK-A", "SERIES-A", 60, bars, fetched_at=2000.0)
    monkeypatch.setattr("services.market_history.volatility", lambda *a, **k: 0.01)
    report = cv.comparison_report(["TICK-A"], lookback_sec=200, as_of=1120.0)
    assert report[0]["delta"] == pytest.approx(report[0]["candlestick_volatility"] - 0.01)
