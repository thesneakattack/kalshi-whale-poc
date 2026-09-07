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
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

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


@pytest.fixture(autouse=True)
def _reset_aio_db_cache():
    """Same fixture as tests/test_series_watcher.py's — this file has no
    other teardown for the shared _aio_db connection cache, and its own
    single asyncio.run(sw_module.capture_stats(...)) call below (Task 5,
    event-loop-blocking-elimination Fix 2) opens a fresh event loop that
    is closed once asyncio.run() returns, leaving a cached
    aiosqlite.Connection bound to a dead loop plus its own non-daemon
    worker thread (aiosqlite/core.py) that only exits on .close() -
    without this, that one leaked thread is enough to keep the whole test
    process alive after pytest reports its results, exactly like the hang
    tests/test_series_watcher.py's own missing version of this fixture
    caused."""
    yield
    from services.diagnostics import _aio_db
    asyncio.run(_aio_db.reset())


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
    stats = asyncio.run(sw_module.capture_stats("KXBTC15M"))
    assert stats.get("raw_trades") == 2


def test_flush_secondary_capture_stores_runs_via_tick_executor(monkeypatch):
    """index_feed/settlement_edge/game_state's flush() + the hourly
    _maybe_prune_capture_stores sweep (which includes series_watcher.prune()'s
    full-scan DELETE - the same file capture_writer's raw_trades store
    writes to) used to run directly on the event loop: a lock collision on
    any of them froze WS ticker/trade processing and HTTP requests, not just
    this tick - the same class of bug P1 Task 7/8 already fixed for
    raw_trades/resolve_and_record. Moved onto tick_executor the same way."""
    calls = []

    async def _spy_run(fn):
        calls.append(fn)
        return fn()

    monkeypatch.setattr(main.tick_executor, "run", _spy_run)
    asyncio.run(main._flush_secondary_capture_stores_async(CFG, 1_755_000_000.0))
    assert len(calls) == 1


def test_flush_secondary_capture_stores_flushes_all_three_and_prunes(monkeypatch, tmp_path):
    from services import game_state as gs_module
    from services import index_feed as idxf_module
    from services.index_feed import ingestion as idxf_ingestion_module

    monkeypatch.setattr(sedge_module, "DB_PATH", tmp_path / "settlement_edge_isolated.db")
    monkeypatch.setattr(gs_module, "DB_PATH", tmp_path / "game_state_isolated.db")
    monkeypatch.setattr(idxf_ingestion_module, "DB_PATH", tmp_path / "index_feed_isolated.db")

    monkeypatch.setattr(sedge_module, "_buffer", [])
    monkeypatch.setattr(gs_module, "_buffer", [])
    monkeypatch.setattr(idxf_ingestion_module, "_tick_buffer", [])
    # Through the real recorder, not a hand-built row tuple - the column
    # order is that function's own concern, not this test's to guess at.
    sedge_module.record_observation(
        "KXBTC15M-26AUG17-B1",
        spec={"index_id": "BTC", "strike": 50000.0, "comparison": "gte", "close_time": None},
        projection={
            "status": "accumulating", "observations_known": 30, "partial_average": 0.5,
            "spot": 100.0, "required_remaining": 40.0, "gap_from_spot": 0.6,
        },
        market_yes_price=0.55, now=1_755_000_100.0,
    )

    # Force the hourly prune gate open so this call actually exercises
    # _maybe_prune_capture_stores' own body, not just its early return.
    monkeypatch.setattr(main, "_last_capture_prune_at", 0.0)
    pruned = []
    monkeypatch.setattr(main.series_watcher, "prune", lambda **kw: pruned.append("series_watcher"))
    monkeypatch.setattr(idxf_module, "prune", lambda **kw: pruned.append("index_feed"))
    monkeypatch.setattr(gs_module, "prune", lambda **kw: pruned.append("game_state"))
    monkeypatch.setattr(main.observability, "prune", lambda **kw: pruned.append("observability"))
    monkeypatch.setattr(mh_module, "prune", lambda **kw: pruned.append("market_history"))
    monkeypatch.setattr(main.fault_log, "prune", lambda **kw: pruned.append("fault_log"))

    result = asyncio.run(main._flush_secondary_capture_stores_async(CFG, 1_755_000_000.0))

    assert result["settlement_edge"]["observations"] == 1
    assert sedge_module._buffer == []  # flushed, not left buffered
    assert set(pruned) == {
        "series_watcher", "index_feed", "game_state", "observability", "market_history", "fault_log",
    }


# --- Task 7 of docs/archive/lane-3-strategy-risk-execution/plans/2026-09-03-strategy-edge-gate-
# implementation.md: hourly Delta_calibrated recompute-and-cache sweep,
# wired next to _maybe_prune_capture_stores (same interval-guard idiom,
# same call site inside _flush_secondary_capture_stores) -----------------


def test_maybe_recompute_edge_gate_deltas_calls_recompute_when_due(monkeypatch):
    monkeypatch.setattr(main, "_last_edge_gate_delta_recompute_at", 0.0)
    calls = []
    monkeypatch.setattr(
        main.confidence_calibration, "recompute_deltas",
        lambda cfg, now: calls.append((cfg, now)),
    )
    cfg = {"strategy": {"edge_gate_recompute_interval_sec": 3600}}
    main._maybe_recompute_edge_gate_deltas(cfg, 1_755_000_000.0)
    assert calls == [(cfg, 1_755_000_000.0)]


def test_maybe_recompute_edge_gate_deltas_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(main, "_last_edge_gate_delta_recompute_at", 1_755_000_000.0)
    monkeypatch.setattr(
        main.confidence_calibration, "recompute_deltas",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    cfg = {"strategy": {"edge_gate_recompute_interval_sec": 3600}}
    main._maybe_recompute_edge_gate_deltas(cfg, 1_755_000_100.0)  # only 100s later, not due yet


def test_maybe_recompute_edge_gate_deltas_swallows_exceptions_into_fault_log(monkeypatch):
    monkeypatch.setattr(main, "_last_edge_gate_delta_recompute_at", 0.0)

    def _boom(cfg, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(main.confidence_calibration, "recompute_deltas", _boom)
    recorded = []
    monkeypatch.setattr(
        main.fault_log, "record",
        lambda component, operation, exc: recorded.append((component, operation, type(exc))),
    )
    main._maybe_recompute_edge_gate_deltas({}, 1_755_000_000.0)  # must not raise
    assert recorded == [("whale_calibration", "recompute_edge_gate_deltas", RuntimeError)]


def test_flush_secondary_capture_stores_recomputes_edge_gate_deltas(monkeypatch):
    """Pins Task 7's own wiring point: _maybe_recompute_edge_gate_deltas
    runs from inside _flush_secondary_capture_stores, next to
    _maybe_prune_capture_stores - unconditionally, not gated behind
    edge_gate_enabled (design's own stated exception: recomputing an
    unused cache is cheap and harmless, and keeps delta_calibrated_for
    warm from the moment the gate is eventually flipped on)."""
    monkeypatch.setattr(main, "_last_edge_gate_delta_recompute_at", 0.0)
    calls = []
    monkeypatch.setattr(
        main.confidence_calibration, "recompute_deltas",
        lambda cfg, now: calls.append((cfg, now)),
    )
    asyncio.run(main._flush_secondary_capture_stores_async(CFG, 1_755_000_000.0))
    assert calls == [(CFG, 1_755_000_000.0)]


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
    # Balanced-paren scan, not the first ")" - an argument can itself be a call
    # (get_whale_provider() since #565), which truncated the naive slice and
    # made this assert on a fragment rather than the real argument list.
    _open = loop_src.index("(", loop_src.index("candidate_retry.run_pending("))
    _depth, _end = 0, len(loop_src)
    for _i in range(_open, len(loop_src)):
        if loop_src[_i] == "(":
            _depth += 1
        elif loop_src[_i] == ")":
            _depth -= 1
            if _depth == 0:
                _end = _i
                break
    call_args = loop_src[_open:_end]
    for required_arg in ("client", "whale_provider", "_handle_signal", "cfg"):
        assert required_arg in call_args, f"run_pending call is missing {required_arg!r}: {call_args!r}"
    assert "_streaming_trade_tape_enabled()" in loop_src  # the stream-mode gate moved with the call
    assert inspect.getsource(main).count("candidate_retry.run_pending(") == 1  # single mutator, still
    assert "_candidate_retry_loop" in inspect.getsource(main.lifespan)


def test_trading_loop_stamps_category_tags_from_in_memory_series_cache():
    """X1 (2026-08-30 design spec + 2026-08-31 fix): category_tags now
    contains real per-series tags from series_metadata/series_tags (Task 1),
    sourced from the in-memory state["series_cache"]["series"] index built
    each tick. Structural check on the real source: verifies the code reads
    from _build_series_tags_cache() and stamps all events in
    state["event_titles"], not just the fetch delta (completeness fix)."""
    import inspect
    source = inspect.getsource(main.trading_loop)

    # Must call _build_series_tags_cache() to get in-memory index
    assert "_build_series_tags_cache" in source, \
        "trading_loop must call _build_series_tags_cache() for in-memory tag index"

    # Must read from that index, not call a DB-opening function
    assert "tags_by_series_ticker.get(" in source or "tags_by_series_ticker.get" in source, \
        "trading_loop must read from in-memory tags_by_series_ticker dict, not DB"

    # Must stamp ALL state["event_titles"], not just the fetch delta
    assert 'state["event_titles"].items()' in source, \
        "trading_loop must stamp ALL events in state cache, not just fetch delta, for backlog completeness"

    # Must check for series_ticker existence before looking it up
    assert 'event_meta.get("series_ticker")' in source or "series_ticker = event_meta.get" in source, \
        "trading_loop must safely check for series_ticker before lookup"


def test_maybe_capture_markouts_writes_a_row_for_a_due_trade(monkeypatch):
    """Pins the sweep's own wiring - not a re-test of pending_markout_targets/
    record_markout's own logic (Task 3 already covers that), just that
    main.py's sweep actually calls them with real broker/market_catalog/
    market_history data, on its own interval, unconditionally (not gated
    on edge_gate_enabled - design SS5/SS8's stated exception). Task 4 of
    docs/archive/lane-3-strategy-risk-execution/plans/2026-09-03-strategy-edge-gate-implementation.md.

    main.broker is the correct reference (a bare module-level name from
    services/app_state.py, never state["broker"] - that key doesn't exist
    anywhere in this codebase; corrected after independent adversarial
    review, Finding F3)."""
    now = time.time()
    fake_trade = SimpleNamespace(id="t1", ticker="TICK-A", side="yes", price=0.6, timestamp=now - 400)
    monkeypatch.setattr(main.broker, "trades_since", lambda after: [fake_trade])
    monkeypatch.setattr(mc_module, "close_ts_for_tickers", lambda tickers: {})
    monkeypatch.setattr(mh_module, "recent_price", lambda ticker, max_age_sec, as_of=None: 0.63)
    recorded = []
    monkeypatch.setattr(mh_module, "record_markout", lambda *a, **kw: recorded.append(a))
    # _last_markout_capture_at gate forced open via monkeypatch (auto-
    # reverting), matching this file's own established convention for the
    # sibling _last_capture_prune_at gate above rather than a raw module
    # assignment.
    monkeypatch.setattr(main, "_last_markout_capture_at", 0.0)

    cfg = {"strategy": {"edge_gate_markout_offsets_sec": [300, 3600, None]}}
    main._maybe_capture_markouts(cfg, now)

    assert recorded  # at least the due 300s offset was captured
