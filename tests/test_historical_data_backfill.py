"""tools/historical_data_backfill.py - issue #265.

One-off, parameterized recovery tool for specific, already-quantified
capture gaps (#209/#211): fetch a named [start_ts, end_ts) window from
Kalshi's trades REST surface (auto-selecting the live `/markets/trades`
endpoint vs the historical `/historical/trades` archive per the real
`/historical/cutoff` boundary - verified 2026-08-30 that #211's own loss
window, 2026-08-27..2026-08-30, is far inside the live tier, not the
historical one) and write the recovered raw_trades rows through the exact
same capture_writer.submit()/flush_now() path and DDL the live pipeline
uses. Every test here uses a tmp_path DB - never a real data/*.db (CLAUDE.md).

No real network access in this suite: every Kalshi call goes through a
tiny stub client (_StubKalshiClient) with the same method names/shapes as
kalshi_python_async's KalshiClient (get_historical_cutoff, get_trades,
get_trades_historical), so tools.historical_data_backfill's own pagination/
endpoint-selection logic is exercised without depending on the network or
the real SDK.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from tools import historical_data_backfill as backfill


# --- parse_ts ----------------------------------------------------------------

def test_parse_ts_accepts_unix_seconds_as_int_or_float():
    assert backfill.parse_ts(1700000000) == 1700000000.0
    assert backfill.parse_ts("1700000000") == 1700000000.0
    assert backfill.parse_ts(1700000000.5) == 1700000000.5


def test_parse_ts_accepts_iso8601_utc():
    ts = backfill.parse_ts("2026-08-27T20:22:15Z")
    expected = datetime(2026, 8, 27, 20, 22, 15, tzinfo=timezone.utc).timestamp()
    assert ts == pytest.approx(expected)


def test_parse_ts_rejects_garbage():
    with pytest.raises(ValueError):
        backfill.parse_ts("not-a-timestamp")


# --- classify_window -----------------------------------------------------------

def test_classify_window_entirely_before_cutoff_is_historical_only():
    segs = backfill.classify_window(start_ts=100.0, end_ts=200.0, cutoff_ts=300.0)
    assert segs == [("historical", 100.0, 200.0)]


def test_classify_window_entirely_after_cutoff_is_live_only():
    segs = backfill.classify_window(start_ts=400.0, end_ts=500.0, cutoff_ts=300.0)
    assert segs == [("live", 400.0, 500.0)]


def test_classify_window_straddling_cutoff_splits_in_order():
    segs = backfill.classify_window(start_ts=100.0, end_ts=500.0, cutoff_ts=300.0)
    assert segs == [("historical", 100.0, 300.0), ("live", 300.0, 500.0)]


def test_classify_window_start_exactly_at_cutoff_is_live_only():
    # docs/kalshi/historical_data.md: trades *before* trades_created_ts are
    # historical-only; the cutoff instant itself is already live.
    segs = backfill.classify_window(start_ts=300.0, end_ts=400.0, cutoff_ts=300.0)
    assert segs == [("live", 300.0, 400.0)]


def test_classify_window_rejects_end_before_start():
    with pytest.raises(ValueError):
        backfill.classify_window(start_ts=500.0, end_ts=100.0, cutoff_ts=300.0)


# --- build_raw_trade_row -------------------------------------------------------

def _rest_trade(**overrides) -> dict:
    """A Trade dict shaped exactly like kalshi_python_async's Trade.model_dump
    (docs/kalshi/get-trades.md / get-historical-trades.md): ticker (not
    market_ticker), created_time (not ts/ts_ms)."""
    trade = {
        "trade_id": "11111111-1111-1111-1111-111111111111",
        "ticker": "KXBTC15M-26AUG3015-B1",
        "count_fp": "10.00",
        "yes_price_dollars": "0.5600",
        "no_price_dollars": "0.4400",
        "taker_side": "yes",
        "taker_outcome_side": "yes",
        "taker_book_side": "bid",
        "created_time": "2026-08-27T20:30:00Z",
        "is_block_trade": False,
    }
    trade.update(overrides)
    return trade


def test_build_raw_trade_row_matches_raw_trades_column_order_and_values():
    trade = _rest_trade()
    observed_at = 1700000000.0
    row = backfill.build_raw_trade_row(trade, observed_at)

    assert len(row) == 16
    (trade_id, ticker, series, obs, exchange_ts, taker_outcome_side, taker_book_side,
     taker_side_legacy, resolved_side, count_fp, yes_price, no_price, notional,
     is_block_trade, excluded, raw_json) = row

    assert trade_id == trade["trade_id"]
    assert ticker == "KXBTC15M-26AUG3015-B1"
    assert series == "KXBTC15M"
    assert obs == observed_at
    expected_ts = datetime(2026, 8, 27, 20, 30, 0, tzinfo=timezone.utc).timestamp()
    assert exchange_ts == pytest.approx(expected_ts)
    # The three raw alias columns stay NULL here, deliberately - see
    # test_module_never_reads_deprecated_direction_aliases_directly below.
    # tools/quality_audit/kalshi_boundary.py's check 4 hard-fails any
    # `.get("taker_outcome_side"/"taker_book_side"/"taker_side")` (or
    # subscript) read outside services/kalshi/ and its one reviewed
    # archival exemption (services/series_watcher.py) - this tool is a
    # different file and gets no second exemption. The values are not
    # lost: raw_json below still carries them byte-for-byte, and the one
    # column anything actually keys decisions off - resolved_side - is
    # populated through the boundary's own resolve_taker_outcome_side.
    assert taker_outcome_side is None
    assert taker_book_side is None
    assert taker_side_legacy is None
    assert resolved_side == "yes"
    assert count_fp == 10.0
    assert yes_price == 0.56
    assert no_price == 0.44
    assert notional == pytest.approx(10.0 * 0.56)  # yes-side: count * yes_price
    assert is_block_trade == 0
    assert excluded == 0  # never repurposed as a provenance flag - see module docstring
    assert json.loads(raw_json) == trade  # byte-for-byte the Kalshi response, no injected keys


def test_module_never_reads_deprecated_direction_aliases_directly():
    """Direct regression for tools/quality_audit/kalshi_boundary.py's CI
    guard (check 4), which hard-fails a `.get("taker_outcome_side"/
    "taker_book_side"/"taker_side")` (or subscript) read anywhere outside
    services/kalshi/ and its one reviewed archival exemption,
    services/series_watcher.py. Reuses the scanner's own AST-based field-
    read detector (tools/kalshi_census._scan_known_field_reads) rather
    than a naive text/regex re-implementation, so this test can't produce
    a false positive against this module's OWN docstring prose the way a
    plain string search would, and stays exactly in sync with what CI
    actually checks."""
    from pathlib import Path

    from tools.kalshi_census import _scan_known_field_reads

    repo_root = Path(backfill.__file__).resolve().parent.parent
    reads = _scan_known_field_reads(repo_root)
    my_file = "tools/historical_data_backfill.py"
    for field in ("taker_outcome_side", "taker_book_side", "taker_side"):
        offending = [site for site in reads.get(field, []) if site.startswith(my_file + ":")]
        assert offending == [], f"{field}: {offending}"


def test_build_raw_trade_row_no_side_notional_uses_no_price_not_yes_price():
    # The exact no-side inversion bug class CLAUDE.md calls out by name.
    trade = _rest_trade(taker_outcome_side="no", taker_side="no", taker_book_side="ask")
    row = backfill.build_raw_trade_row(trade, 1700000000.0)
    notional = row[12]
    assert notional == pytest.approx(10.0 * 0.44)


def test_build_raw_trade_row_unresolvable_side_stays_none_never_guessed():
    trade = _rest_trade(taker_outcome_side="", taker_side="", taker_book_side="")
    row = backfill.build_raw_trade_row(trade, 1700000000.0)
    resolved_side, notional = row[8], row[12]
    assert resolved_side is None
    assert notional is None


def test_build_raw_trade_row_never_injects_a_key_into_raw_json():
    """Direct regression for the fidelity rule services/kalshi/websocket.py's
    MESSAGE_ENQUEUED_AT comment documents: stamping a synthetic key into the
    archived payload is the anti-pattern this module must not repeat.
    Provenance lives in the separate backfill ledger table instead."""
    trade = _rest_trade()
    row = backfill.build_raw_trade_row(trade, 1700000000.0)
    stored = json.loads(row[-1])
    assert set(stored.keys()) == set(trade.keys())


# --- filter_by_series -----------------------------------------------------------

def test_filter_by_series_keeps_only_matching_series():
    trades = [
        _rest_trade(ticker="KXBTC15M-26AUG3015-B1"),
        _rest_trade(ticker="KXETH15M-26AUG3015-B1", trade_id="2"),
        _rest_trade(ticker="KXBTCD-26AUG30-B1", trade_id="3"),
    ]
    kept = backfill.filter_by_series(trades, ["KXBTC15M", "KXBTCD"])
    assert {t["ticker"] for t in kept} == {"KXBTC15M-26AUG3015-B1", "KXBTCD-26AUG30-B1"}


def test_filter_by_series_empty_allowlist_keeps_everything():
    trades = [_rest_trade(ticker="ANYTHING-1")]
    assert backfill.filter_by_series(trades, []) == trades


# --- pagination / endpoint selection --------------------------------------------

class _StubKalshiClient:
    """Same method names/shapes as kalshi_python_async's KalshiClient for the
    three calls this tool makes - get_historical_cutoff, get_trades (live),
    get_trades_historical - so pagination/endpoint-selection is verified
    without any real network access or the real SDK installed."""

    def __init__(self, pages: dict[str, list[tuple[list[dict], str]]], cutoff_iso: str):
        # pages: {"live": [(trades, next_cursor), ...], "historical": [...]}
        self._pages = {k: list(v) for k, v in pages.items()}
        self._cutoff_iso = cutoff_iso
        self.calls: list[tuple[str, dict]] = []

    class _Resp:
        def __init__(self, data):
            self._data = data

        def model_dump(self, mode="json"):
            return self._data

    async def get_historical_cutoff(self):
        return self._Resp({
            "market_settled_ts": self._cutoff_iso, "trades_created_ts": self._cutoff_iso,
            "orders_updated_ts": self._cutoff_iso, "market_positions_last_updated_ts": self._cutoff_iso,
        })

    async def _paged(self, name, **kwargs):
        self.calls.append((name, kwargs))
        cursor = kwargs.get("cursor") or ""
        pages = self._pages[name]
        idx = 0 if not cursor else int(cursor)
        trades, next_cursor = pages[idx]
        return self._Resp({"trades": trades, "cursor": next_cursor})

    async def get_trades(self, **kwargs):
        return await self._paged("live", **kwargs)

    async def get_trades_historical(self, **kwargs):
        return await self._paged("historical", **kwargs)


def test_fetch_trades_segment_pages_until_empty_cursor():
    page1 = [_rest_trade(trade_id="a")]
    page2 = [_rest_trade(trade_id="b")]
    client = _StubKalshiClient(
        pages={"live": [(page1, "1"), (page2, "")]},
        cutoff_iso="2026-07-01T00:00:00Z",
    )
    trades = asyncio.run(backfill.fetch_trades_segment(client, "live", None, 100.0, 200.0, page_limit=1000))
    assert [t["trade_id"] for t in trades] == ["a", "b"]
    assert client.calls[0][0] == "live"
    assert client.calls[0][1]["min_ts"] == 100
    assert client.calls[0][1]["max_ts"] == 200


def test_fetch_trades_segment_uses_historical_endpoint_by_name():
    client = _StubKalshiClient(pages={"historical": [([_rest_trade()], "")]}, cutoff_iso="2026-07-01T00:00:00Z")
    trades = asyncio.run(backfill.fetch_trades_segment(client, "historical", "KXBTC15M-26AUG3015-B1", 1.0, 2.0))
    assert len(trades) == 1
    assert client.calls[0][0] == "historical"
    assert client.calls[0][1]["ticker"] == "KXBTC15M-26AUG3015-B1"


# --- ledger ----------------------------------------------------------------------

def test_write_ledger_entries_creates_table_and_is_idempotent(tmp_path):
    ledger_path = tmp_path / "historical_backfill_log.db"
    entries = [
        {"store": "raw_trades", "trade_id": "t1", "ticker": "KXBTC15M-1", "source_endpoint": "live",
         "window_start_ts": 1.0, "window_end_ts": 2.0, "fetched_at": 3.0, "issue_ref": "211",
         "already_present": False},
    ]
    n1 = backfill.write_ledger_entries(ledger_path, entries)
    assert n1 == 1
    n2 = backfill.write_ledger_entries(ledger_path, entries)  # re-run, same window
    assert n2 == 0  # already recorded - never double-counted

    conn = sqlite3.connect(ledger_path)
    rows = conn.execute("SELECT store, trade_id, ticker, source_endpoint, issue_ref FROM backfilled_rows").fetchall()
    conn.close()
    assert rows == [("raw_trades", "t1", "KXBTC15M-1", "live", "211")]


# --- run_backfill end-to-end (stub client, tmp paths) -----------------------------

def test_run_backfill_dry_run_never_touches_any_db_path(tmp_path):
    db_path = tmp_path / "series_watcher.db"
    ledger_path = tmp_path / "ledger.db"
    client = _StubKalshiClient(
        pages={"live": [([_rest_trade(trade_id="a", ticker="KXBTC15M-1")], "")]},
        cutoff_iso="2026-07-01T00:00:00Z",
    )
    report = asyncio.run(backfill.run_backfill(
        # #211's real, verified loss window (GET /api/health/faults, 2026-08-30):
        # 2026-08-27T20:22:15Z .. 2026-08-30T16:08:55Z - safely after the real
        # trades_created_ts cutoff (2026-07-01T00:00:00Z), i.e. squarely in the
        # "live" tier, not "historical" - using an arbitrary pre-cutoff pair
        # here would route to the wrong stub endpoint and fail for the wrong reason.
        start_ts=1787862135.0, end_ts=1788106135.0, tickers=None, series=["KXBTC15M"],
        db_path=db_path, ledger_db_path=ledger_path, dry_run=True, issue_ref="211",
        client=client,
    ))
    assert report["dry_run"] is True
    assert report["candidate_trade_count"] == 1
    assert not db_path.exists()
    assert not ledger_path.exists()


def test_run_backfill_real_run_writes_raw_trades_and_ledger(tmp_path, monkeypatch):
    from services import capture_writer

    db_path = tmp_path / "series_watcher.db"
    ledger_path = tmp_path / "ledger.db"
    monkeypatch.setitem(capture_writer._STORE_PATHS, "raw_trades", db_path)
    # Fresh-process invariant this tool relies on (see module docstring):
    # start every counter at 0 so a post-flush read is this run's own count.
    monkeypatch.setitem(capture_writer._dropped_counts, "raw_trades", 0)
    monkeypatch.setitem(capture_writer._overflow_dropped_counts, "raw_trades", 0)
    monkeypatch.setitem(capture_writer._buffers, "raw_trades", [])

    client = _StubKalshiClient(
        pages={"live": [([_rest_trade(trade_id="a", ticker="KXBTC15M-1")], "")]},
        cutoff_iso="2026-07-01T00:00:00Z",
    )
    report = asyncio.run(backfill.run_backfill(
        # #211's real, verified loss window (GET /api/health/faults, 2026-08-30):
        # 2026-08-27T20:22:15Z .. 2026-08-30T16:08:55Z - safely after the real
        # trades_created_ts cutoff (2026-07-01T00:00:00Z), i.e. squarely in the
        # "live" tier, not "historical" - using an arbitrary pre-cutoff pair
        # here would route to the wrong stub endpoint and fail for the wrong reason.
        start_ts=1787862135.0, end_ts=1788106135.0, tickers=None, series=["KXBTC15M"],
        db_path=db_path, ledger_db_path=ledger_path, dry_run=False, issue_ref="211",
        client=client,
    ))
    assert report["dry_run"] is False
    assert report["written"] == 1
    assert report["dropped"] == 0

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT trade_id, ticker, series FROM raw_trades").fetchone()
    conn.close()
    assert row == ("a", "KXBTC15M-1", "KXBTC15M")

    ledger_conn = sqlite3.connect(ledger_path)
    ledger_row = ledger_conn.execute("SELECT trade_id, source_endpoint, issue_ref FROM backfilled_rows").fetchone()
    ledger_conn.close()
    assert ledger_row == ("a", "live", "211")


def test_run_backfill_real_run_is_idempotent_on_rerun(tmp_path, monkeypatch):
    """Re-running for an overlapping window (the realistic operator workflow -
    check with --dry-run, then run for real, then maybe widen the window and
    re-run) must not duplicate rows: raw_trades' trade_id PRIMARY KEY (via
    INSERT OR IGNORE, same as live capture) and the ledger's UNIQUE(store,
    trade_id) both dedup on the real Kalshi trade_id."""
    from services import capture_writer

    db_path = tmp_path / "series_watcher.db"
    ledger_path = tmp_path / "ledger.db"
    monkeypatch.setitem(capture_writer._STORE_PATHS, "raw_trades", db_path)
    monkeypatch.setitem(capture_writer._dropped_counts, "raw_trades", 0)
    monkeypatch.setitem(capture_writer._overflow_dropped_counts, "raw_trades", 0)
    monkeypatch.setitem(capture_writer._buffers, "raw_trades", [])

    def make_client():
        return _StubKalshiClient(
            pages={"live": [([_rest_trade(trade_id="a", ticker="KXBTC15M-1")], "")]},
            cutoff_iso="2026-07-01T00:00:00Z",
        )

    kwargs = dict(
        # #211's real, verified loss window (GET /api/health/faults, 2026-08-30):
        # 2026-08-27T20:22:15Z .. 2026-08-30T16:08:55Z - safely after the real
        # trades_created_ts cutoff (2026-07-01T00:00:00Z), i.e. squarely in the
        # "live" tier, not "historical" - using an arbitrary pre-cutoff pair
        # here would route to the wrong stub endpoint and fail for the wrong reason.
        start_ts=1787862135.0, end_ts=1788106135.0, tickers=None, series=["KXBTC15M"],
        db_path=db_path, ledger_db_path=ledger_path, dry_run=False, issue_ref="211",
    )
    asyncio.run(backfill.run_backfill(**kwargs, client=make_client()))
    monkeypatch.setitem(capture_writer._buffers, "raw_trades", [])
    report2 = asyncio.run(backfill.run_backfill(**kwargs, client=make_client()))

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM raw_trades").fetchone()[0]
    conn.close()
    assert count == 1  # not 2
    assert report2["already_present"] == 1


def test_run_backfill_requires_ticker_or_series():
    client = _StubKalshiClient(pages={"live": [([], "")]}, cutoff_iso="2026-07-01T00:00:00Z")
    with pytest.raises(ValueError):
        asyncio.run(backfill.run_backfill(
            start_ts=1.0, end_ts=2.0, tickers=None, series=None,
            db_path=None, ledger_db_path=None, dry_run=True, issue_ref=None,
            client=client,
        ))


# --- CLI argument parsing (no network) --------------------------------------------

def test_parse_args_requires_ticker_or_series():
    with pytest.raises(SystemExit):
        backfill.parse_args(["--start", "2026-08-27T20:22:15Z", "--end", "2026-08-30T16:08:55Z", "--dry-run"])


def test_parse_args_accepts_series_and_defaults_dry_run_false():
    ns = backfill.parse_args([
        "--start", "2026-08-27T20:22:15Z", "--end", "2026-08-30T16:08:55Z",
        "--series", "KXBTC15M", "--series", "KXETH15M",
    ])
    assert ns.series == ["KXBTC15M", "KXETH15M"]
    assert ns.dry_run is False
    assert ns.tickers is None


def test_parse_args_accepts_ticker_repeatable():
    ns = backfill.parse_args([
        "--start", "1700000000", "--end", "1700003600",
        "--ticker", "KXBTC15M-26AUG3015-B1", "--dry-run",
    ])
    assert ns.tickers == ["KXBTC15M-26AUG3015-B1"]
    assert ns.dry_run is True
