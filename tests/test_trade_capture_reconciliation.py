"""REST-vs-WebSocket trade capture reconciliation (realtime data-plane task
I4, hypothesis H5). Quantifies actual capture completeness by trade_id over
a bounded exchange-time window instead of inferring it from local drop
counters.

Doc-backed shape (docs/kalshi/get-trades.md): GET /markets/trades is
exchange-wide when `ticker` is omitted, pages 1-1000 per request via a
cursor that is empty when exhausted, filters with `min_ts`/`max_ts` (Unix
seconds), and every Trade carries `trade_id`, `ticker`, `count_fp` and an
ISO `created_time`."""
import asyncio
from datetime import datetime, timezone

import pytest

from services.diagnostics import trade_capture_reconciliation as recon
from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _rest_trade(trade_id: str, ts: float, ticker: str = "TICK-A", count_fp: str = "10.00") -> dict:
    return {"trade_id": trade_id, "ticker": ticker, "count_fp": count_fp, "created_time": _iso(ts),
            "yes_price_dollars": "0.50", "no_price_dollars": "0.50",
            "taker_outcome_side": "yes", "taker_book_side": "bid", "is_block_trade": False}


class _PagedClient:
    """Serves `pages` in order; records every call's parameters."""

    def __init__(self, pages: list[list[dict]]):
        self._pages = pages
        self.calls: list[dict] = []

    async def get_trades(self, ticker=None, limit=25, min_ts=None, cursor=None, max_ts=None):
        self.calls.append({"ticker": ticker, "limit": limit, "min_ts": min_ts, "max_ts": max_ts, "cursor": cursor})
        index = int(cursor) if cursor else 0
        page = self._pages[index] if index < len(self._pages) else []
        next_cursor = str(index + 1) if index + 1 < len(self._pages) else ""
        return {"trades": page, "cursor": next_cursor}


def _run(client, seen: dict[str, float], **kwargs):
    return asyncio.run(recon.reconcile_window(
        client, seen_exchange_ts_by_id=seen, min_contracts_for=lambda ticker: 5000.0, **kwargs,
    ))


# --- core comparison ------------------------------------------------------

def test_reconciles_rest_and_ws_by_trade_id_over_the_window():
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 10), _rest_trade("b", t0 + 20), _rest_trade("c", t0 + 30)]])
    seen = {"a": t0 + 10, "c": t0 + 30, "zzz-outside": t0 - 500}

    r = _run(client, seen, window_start=t0, window_end=t0 + 60)

    assert (r["rest"]["count"], r["rest"]["pages"], r["rest"]["truncated"]) == (3, 1, False)
    assert r["ws"]["count_in_window"] == 2
    assert r["intersection"] == 2
    assert r["missing"]["count"] == 1 and r["missing"]["ids"] == ["b"]
    assert r["capture_completeness"] == pytest.approx(2 / 3)
    assert r["window"] == {"start": t0, "end": t0 + 60, "seconds": 60.0}


def test_requests_are_exchange_wide_time_bounded_and_max_page_size():
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 1)]])
    _run(client, {"a": t0 + 1}, window_start=t0, window_end=t0 + 60)
    call = client.calls[0]
    assert call["ticker"] is None                       # exchange-wide form (docs: omit ticker)
    assert call["limit"] == 1000                        # documented maximum page size
    assert (call["min_ts"], call["max_ts"]) == (int(t0), int(t0 + 60))
    assert call["cursor"] is None


def test_pages_until_the_cursor_is_empty_and_reports_page_count():
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 1)], [_rest_trade("b", t0 + 2)], [_rest_trade("c", t0 + 3)]])
    r = _run(client, {"a": t0 + 1, "b": t0 + 2, "c": t0 + 3}, window_start=t0, window_end=t0 + 60)
    assert r["rest"]["pages"] == 3 and r["rest"]["count"] == 3 and r["rest"]["truncated"] is False
    assert [c["cursor"] for c in client.calls] == [None, "1", "2"]
    assert r["capture_completeness"] == 1.0


def test_explicit_truncation_when_max_pages_is_hit_with_more_available():
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade(f"p{i}", t0 + i)] for i in range(5)])
    r = _run(client, {}, window_start=t0, window_end=t0 + 60, max_pages=2)
    assert r["rest"]["pages"] == 2 and r["rest"]["truncated"] is True
    assert any("truncated" in c for c in r["caveats"])


def test_rest_trades_outside_the_window_are_filtered_locally_by_created_time():
    # min_ts/max_ts inclusivity is not documented; the window is enforced
    # locally on created_time so the comparison is exact regardless.
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("early", t0 - 1), _rest_trade("in", t0 + 5), _rest_trade("late", t0 + 61)]])
    r = _run(client, {"in": t0 + 5}, window_start=t0, window_end=t0 + 60)
    assert r["rest"]["count"] == 1 and r["intersection"] == 1 and r["missing"]["count"] == 0


def test_whale_sized_missing_trades_are_called_out_separately():
    t0 = 1_000_000.0
    client = _PagedClient([[
        _rest_trade("small-miss", t0 + 1, count_fp="10.00"),
        _rest_trade("whale-miss", t0 + 2, ticker="TICK-W", count_fp="6000.00"),
        _rest_trade("whale-hit", t0 + 3, ticker="TICK-W", count_fp="7000.00"),
    ]])
    r = _run(client, {"whale-hit": t0 + 3}, window_start=t0, window_end=t0 + 60)
    assert r["missing"]["count"] == 2
    assert r["missing"]["whale_sized"] == {"count": 1, "ids": ["whale-miss"], "tickers": ["TICK-W"]}
    assert r["whale_capture_completeness"] == pytest.approx(1 / 2)
    assert r["rest"]["whale_sized_count"] == 2


def test_exchange_wide_completeness_is_count_based_against_received_by_class():
    # I4 completeness split (P3 Task 17 Step 6): exchange_wide_completeness
    # is a raw trade-class message COUNT from the WS reader (services/
    # kalshi/websocket.py's ingest_metrics()' received_by_class) against the
    # REST trade COUNT for the window - not an id-based comparison against
    # the bounded seen-record ring the way capture_completeness/
    # whale_capture_completeness are. It has to stay independent of those
    # two: a reader-gate drop never reaches seen_exchange_ts_by_id (so the
    # id-based fields see it as "missing"), but it was still received off
    # the wire, so the count-based field should not move with it.
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 10), _rest_trade("b", t0 + 20)]])
    evidence = {"received_by_class": {"trade": 1, "ticker": 40}}

    r = _run(client, {}, window_start=t0, window_end=t0 + 60, ingest_evidence=evidence)

    assert r["rest"]["count"] == 2
    assert r["exchange_wide_completeness"] == pytest.approx(0.5)  # 1 received / 2 REST
    assert r["capture_completeness"] == 0.0  # id-based, empty ring here - unaffected
    assert r["whale_capture_completeness"] is None


def test_exchange_wide_completeness_is_none_without_a_received_count():
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 10)]])

    r = _run(client, {}, window_start=t0, window_end=t0 + 60)  # no ingest_evidence at all
    assert r["exchange_wide_completeness"] is None

    r2 = _run(client, {}, window_start=t0, window_end=t0 + 60,
              ingest_evidence={"received_by_class": {"ticker": 5}})  # no "trade" key
    assert r2["exchange_wide_completeness"] is None

    r3 = _run(client, {}, window_start=t0, window_end=t0 + 60,
              ingest_evidence={"dropped_window": 0})  # no received_by_class key at all
    assert r3["exchange_wide_completeness"] is None


def test_exchange_wide_completeness_is_none_when_no_rest_trades_in_window():
    t0 = 1_000_000.0
    r = _run(_PagedClient([[]]), {}, window_start=t0, window_end=t0 + 60,
             ingest_evidence={"received_by_class": {"trade": 5}})
    assert r["exchange_wide_completeness"] is None


def test_ws_only_ids_in_window_are_counted_not_treated_as_misses():
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 1)]])
    r = _run(client, {"a": t0 + 1, "ws-only": t0 + 2}, window_start=t0, window_end=t0 + 60)
    assert r["ws"]["count_in_window"] == 2 and r["ws"]["not_in_rest"] == 1
    assert r["missing"]["count"] == 0 and r["capture_completeness"] == 1.0


def test_empty_rest_window_reports_unknown_completeness_not_100_percent():
    t0 = 1_000_000.0
    r = _run(_PagedClient([[]]), {}, window_start=t0, window_end=t0 + 60)
    assert r["rest"]["count"] == 0
    assert r["capture_completeness"] is None and r["whale_capture_completeness"] is None
    assert any("no REST trades" in c for c in r["caveats"])


def test_rest_failure_degrades_to_an_explicit_error_not_an_exception():
    class _Broken:
        async def get_trades(self, **kwargs):
            raise RuntimeError("429 Too Many Requests")

    r = _run(_Broken(), {}, window_start=1.0, window_end=61.0)
    assert r["error"].startswith("RuntimeError: 429")
    assert r["capture_completeness"] is None


def test_missing_id_list_is_bounded_but_the_count_is_exact():
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade(f"m{i}", t0 + 1) for i in range(120)]])
    r = _run(client, {}, window_start=t0, window_end=t0 + 60)
    assert r["missing"]["count"] == 120 and len(r["missing"]["ids"]) == recon.MAX_LISTED_IDS


def test_ingest_evidence_is_attached_verbatim_when_supplied():
    t0 = 1_000_000.0
    evidence = {"dropped_window": 3, "error_25_window": 0, "reconnects": 1, "oldest_message_age_sec": 0.2}
    r = _run(_PagedClient([[]]), {}, window_start=t0, window_end=t0 + 60, ingest_evidence=evidence)
    assert r["ingest_evidence"] == evidence


def test_seen_horizon_older_than_window_start_is_required_or_flagged():
    # If the WS-side record only reaches back to t0+30 (dedupe ring eviction),
    # anything REST reports before that would be a false miss.
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 5), _rest_trade("b", t0 + 40)]])
    r = _run(client, {"b": t0 + 40}, window_start=t0, window_end=t0 + 60, seen_horizon_ts=t0 + 30)
    assert any("horizon" in c for c in r["caveats"])
    assert r["missing"]["count"] == 1  # still reported, but the caveat says why it may be false


# --- the WS-side record the provider must keep for this to work -----------

def test_provider_remembers_exchange_timestamps_of_seen_trades_for_reconciliation(tmp_path, monkeypatch):
    from services import candidate_log, market_history, series_evaluator, signal_log
    from services.market_analyst_agent import _db as maa_db_module
    monkeypatch.setattr(signal_log, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(market_history, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "a.db")
    monkeypatch.setattr(series_evaluator, "DB_PATH", tmp_path / "e.db")
    monkeypatch.setattr(candidate_log, "DB_PATH", tmp_path / "c.db")

    provider = KalshiTradeTapeProvider()
    t0 = 1_000_000.0
    tape = [
        {"trade_id": "x", "ticker": "UNK", "count_fp": "1.00", "yes_price_dollars": "0.5",
         "taker_outcome_side": "yes", "created_time": _iso(t0 + 5)},
        {"trade_id": "y", "ticker": "UNK", "count_fp": "1.00", "yes_price_dollars": "0.5",
         "taker_outcome_side": "yes", "ts_ms": int((t0 + 7) * 1000)},
    ]
    asyncio.run(provider.fetch_signals(market_context={"markets": [], "trade_tape": tape, "cfg": {}}))

    seen = provider.seen_exchange_ts_by_id()
    assert seen == {"x": pytest.approx(t0 + 5), "y": pytest.approx(t0 + 7)}
    assert provider.seen_horizon_ts() == pytest.approx(t0 + 5)  # oldest retained exchange timestamp


def test_provider_seen_timestamps_are_evicted_with_the_dedupe_ring(monkeypatch):
    from services.whalewatchers import kalshi_trade_tape as mod
    monkeypatch.setattr(mod, "_MAX_SEEN_TRADE_IDS", 3)
    provider = KalshiTradeTapeProvider()
    for i in range(5):
        provider._mark_seen(f"t{i}", exchange_ts=100.0 + i)
    assert set(provider.seen_exchange_ts_by_id()) == {"t2", "t3", "t4"}
    assert provider.seen_horizon_ts() == 102.0
    assert "t0" not in provider._seen_trade_ids


def test_backlog_older_than_the_lag_is_flagged_because_missing_may_still_be_queued():
    # I7 found this blind spot live: with the ingest queue ~150 s deep and a
    # 60 s lag, prints that arrived in the window were still queued (not yet
    # in the seen-record) and were counted as missing. The tool must say so.
    t0 = 1_000_000.0
    client = _PagedClient([[_rest_trade("a", t0 + 5), _rest_trade("b", t0 + 6)]])
    evidence = {"oldest_message_age_sec": 150.0, "lag_sec": 60.0, "queue_depth": 18000}
    r = _run(client, {}, window_start=t0, window_end=t0 + 60, ingest_evidence=evidence)
    assert r["missing"]["count"] == 2
    assert r["backlog_exceeds_lag"] is True
    assert any("still be queued" in c for c in r["caveats"])
    quiet = _run(client, {}, window_start=t0, window_end=t0 + 60,
                 ingest_evidence={"oldest_message_age_sec": 0.2, "lag_sec": 60.0})
    assert quiet["backlog_exceeds_lag"] is False and not any("still be queued" in c for c in quiet["caveats"])
