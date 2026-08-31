"""main.py's trading-tick synchronous SQLite phases, routed through
services/tick_executor.py instead of the calling event loop (realtime
data-plane remediation plan, P1 Tasks 7/8).

Follows the repo's established pattern of redirecting every DB_PATH before
`import main`, since main.py constructs PaperBroker/RiskManager/the
config_store singleton at import time (CLAUDE.md's "data/*.db files are
live" section) - see tests/test_active_terminal_refresh.py.
"""
import asyncio
import tempfile
from pathlib import Path

from services import candidate_log as cl_module
from services import risk_manager as rm_module
from services.config import config_performance as cp_module
from services.market_analyst_agent import _db as maa_db_module
from services import market_history as mh_module
from services.market_catalog import market_catalog as mc_module
from services import series_evaluator as se_module
from services import series_watcher as sw_module
from services import settlement_edge as sedge_module
from services import signal_log as sl_module

_tmp_dir = Path(tempfile.mkdtemp(prefix="tick_executor_wiring_"))
# services.paper_broker.DB_PATH is deliberately NOT imported+re-overridden
# here - see
# tests/test_trading_gate.py's own comment at the same spot for the full
# mechanism (conftest's install_runtime_isolation() already redirects it
# before this file is even collected; reassigning it again here is dead code
# for main.broker but stays live and dangerous for anything reading the
# module attribute fresh, like trade_archive.archive_epoch()).
rm_module.DB_PATH = _tmp_dir / "risk_state.db"
cp_module.DB_PATH = _tmp_dir / "config_performance.db"
mh_module.DB_PATH = _tmp_dir / "market_history.db"
mc_module.DB_PATH = _tmp_dir / "market_catalog.db"
se_module.DB_PATH = _tmp_dir / "series_evaluator.db"
sw_module.DB_PATH = _tmp_dir / "series_watcher.db"
cl_module.DB_PATH = _tmp_dir / "candidate_log.db"
sedge_module.DB_PATH = _tmp_dir / "settlement_edge.db"
maa_db_module.DB_PATH = _tmp_dir / "market_analyst.db"
sl_module.DB_PATH = _tmp_dir / "signal_log.db"

import main  # noqa: E402

CFG = {
    "series_watcher": {"enabled": True, "series": ["KXBTC15M"], "book_snapshot_interval_sec": 5},
}


def _trade(trade_id, ticker="KXBTC15M-26AUG17-B1", outcome="yes", count="1000.00",
           yes_price="0.60", no_price="0.40"):
    return {
        "trade_id": trade_id, "ticker": ticker, "count_fp": count,
        "yes_price_dollars": yes_price, "no_price_dollars": no_price,
        "taker_outcome_side": outcome, "taker_book_side": "bid" if outcome == "yes" else "ask",
        "taker_side": outcome, "is_block_trade": False, "ts_ms": 1_755_000_000_000,
    }


def test_flush_trade_capture_runs_via_tick_executor(monkeypatch):
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(main.tick_executor, "run", _spy_run)
    asyncio.run(main._flush_trade_capture_async([], CFG))
    assert len(calls) == 1


def test_flush_trade_capture_writes_the_same_rows_as_before_extraction(monkeypatch, tmp_path):
    from services import capture_writer as cw_module

    db_path = tmp_path / "series_watcher_isolated.db"
    monkeypatch.setattr(sw_module, "DB_PATH", db_path)
    monkeypatch.setattr(sw_module, "_book_buffer", [])
    monkeypatch.setattr(sw_module, "_dropped_rows", 0)
    monkeypatch.setattr(sw_module, "_quarantine_cache", None)
    # Trades route through capture_writer now (P3 Task 15), not sw_module's
    # own buffer - point its raw_trades store at the same isolated tmp path.
    monkeypatch.setattr(cw_module, "_STORE_PATHS", {"raw_trades": db_path})
    monkeypatch.setattr(cw_module, "_buffers", {"raw_trades": []})
    monkeypatch.setattr(cw_module, "_last_flush_at", {"raw_trades": 0.0})
    monkeypatch.setattr(cw_module, "_dropped_counts", {"raw_trades": 0})

    trade_tape = [_trade("t1"), _trade("t2")]
    result = asyncio.run(main._flush_trade_capture_async(trade_tape, CFG))

    # flush() is book-only post-Task-15 (trades no longer flow through it,
    # or through this function's return value at all) - no book messages
    # in this trade-only tape, so nothing to flush there either.
    assert result == {"books": 0}
    # capture_writer flushes on its own thread's cadence, not synchronously
    # - force it for a deterministic assertion, same as test_series_watcher.py.
    cw_module.flush_now("raw_trades")
    stats = sw_module.capture_stats("KXBTC15M")
    assert stats.get("raw_trades") == 2


def test_resolve_and_record_settlements_runs_via_tick_executor(monkeypatch):
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(main.tick_executor, "run", _spy_run)
    asyncio.run(main._resolve_and_record_settlements_async([], {}, 1_755_000_000.0))
    assert len(calls) == 1


def test_resolve_and_record_settlements_slims_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(mh_module, "DB_PATH", tmp_path / "market_history_isolated.db")
    monkeypatch.setattr(sedge_module, "DB_PATH", tmp_path / "settlement_edge_isolated.db")
    monkeypatch.setattr(maa_db_module, "DB_PATH", tmp_path / "market_analyst_isolated.db")
    monkeypatch.setattr(cl_module, "DB_PATH", tmp_path / "candidate_log_isolated.db")

    markets = [
        {
            "ticker": "KXBTC15M-26AUG17-B1", "status": "finalized", "result": "yes",
            "yes_bid_dollars": 0.62, "yes_ask_dollars": 0.64, "volume_24h_fp": "1000",
            "close_time": None, "event_ticker": "KXBTC15M-26AUG17", "not_a_real_field": "dropped by slimming",
        },
        {
            "ticker": "KXBTC15M-26AUG17-B2", "status": "active", "result": "",
            "yes_bid_dollars": 0.50, "yes_ask_dollars": 0.52, "volume_24h_fp": "500",
            "close_time": None, "event_ticker": "KXBTC15M-26AUG17",
        },
    ]
    market_results = {"KXBTC15M-26AUG17-B1": "yes"}
    tick_now = 1_755_000_100.0

    slimmed = asyncio.run(main._resolve_and_record_settlements_async(markets, market_results, tick_now))

    assert {m["ticker"] for m in slimmed} == {"KXBTC15M-26AUG17-B1", "KXBTC15M-26AUG17-B2"}
    assert all("not_a_real_field" not in m for m in slimmed)  # confirms real slimming happened
    assert mh_module.snapshot_count() == 2  # one row per market, batched write
    assert mh_module.outcome_count() == 1  # only the finalized B1 market resolved an outcome


def test_build_series_track_record_runs_via_tick_executor(monkeypatch):
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(main.tick_executor, "run", _spy_run)
    asyncio.run(main._build_series_track_record_async(["A-1", "B-1"], days=30))
    assert len(calls) == 1


def test_build_series_track_record_uses_series_stats_bulk_not_per_ticker(monkeypatch, tmp_path):
    """P1 Task 8's other target: main.py's series_track_record build used
    to call signal_log.series_stats once per market (one _connect() per
    market, root-cause report C1's main.py:736 N+1). Confirm the tick now
    makes exactly one series_stats_bulk call for all watched tickers,
    not one series_stats call per ticker."""
    monkeypatch.setattr(sl_module, "DB_PATH", tmp_path / "signal_log_isolated.db")
    bulk_calls = []
    per_ticker_calls = []
    original_bulk = sl_module.series_stats_bulk

    def _spy_bulk(tickers, days=30):
        bulk_calls.append(list(tickers))
        return original_bulk(tickers, days=days)

    monkeypatch.setattr(main.signal_log, "series_stats_bulk", _spy_bulk)
    monkeypatch.setattr(main.signal_log, "series_stats", lambda *a, **k: per_ticker_calls.append(a))

    result = asyncio.run(main._build_series_track_record_async(["A-1", "B-1"], days=30))

    assert bulk_calls == [["A-1", "B-1"]]
    assert per_ticker_calls == []  # the old N+1 path must never run
    assert set(result.keys()) == {"A-1", "B-1"}


def test_candidate_retry_runs_from_its_own_supervised_loop_not_the_tick():
    """P2 Task 13 put run_pending inside trading_loop (stream mode only, with
    whale_provider + _handle_signal threaded through so a recovered candidate
    is scored through the same pipeline as a first-try trade, not
    claim-and-dropped). P8 Task 37 moves it to its own supervised loop while
    keeping candidate_retry's documented single-mutator contract: exactly one
    caller in the whole application, now _candidate_retry_loop. Structural
    check on the real source, same idiom as before (nothing drives one full
    tick in this repo)."""
    import inspect
    assert "candidate_retry.run_pending(" not in inspect.getsource(main.trading_loop)
    loop_src = inspect.getsource(main._candidate_retry_loop)
    assert "candidate_retry.run_pending(" in loop_src
    call_args = loop_src[loop_src.index("candidate_retry.run_pending("):]
    call_args = call_args[:call_args.index(")")]
    for required_arg in ("client", "whale_provider", "_handle_signal", "cfg"):
        assert required_arg in call_args, f"run_pending call is missing {required_arg!r}: {call_args!r}"
    assert "_streaming_trade_tape_enabled()" in loop_src  # the stream-mode gate moved with the call
    assert inspect.getsource(main).count("candidate_retry.run_pending(") == 1  # single mutator, still
    assert "_candidate_retry_loop" in inspect.getsource(main.lifespan)


def test_trading_loop_no_longer_stamps_the_redundant_category_tags_field():
    """X1 (2026-08-30 design spec): category_tags carried the same
    facet-filter vocabulary for every event in a category - zero
    per-event signal, already flagged as a gotcha in docs/kalshi/
    CHEATSHEET.md. series_metadata/series_tags (Task 1) are the real,
    per-series replacement."""
    import inspect
    assert 'category_tags' not in inspect.getsource(main.trading_loop)
