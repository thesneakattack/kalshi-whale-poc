"""Independent scheduled ticker-map flush (issue #576).

_consume_market_from's own QueueEmpty branch only reaches the ticker-
coalescing map (_ticker_by_market) when the market queue happens to be
momentarily empty. Under semaphore contention (Option B, 2026-09-03 - the
trade-dispatch semaphore held while on_trade's REST-resolve runs) that same
consumer can be fully suspended awaiting a semaphore slot for the entire
flush interval and never reach the loop top at all - the plausible trigger
#576 root-caused, distinct from a pure message-count starvation regime.

flush_pending_tickers() is the fix: an independent, timer-driven drain,
decoupled entirely from market_queue's own state, wired from main.py's own
_ticker_flush_loop (mirroring _stream_consumer_liveness_loop's own wiring
shape) so it keeps draining even while _consume_market_from is parked
elsewhere.

Deterministic throughout: every clock is injectable, no socket opened, and
the ticker-coalescing map is exercised directly via the same _coalesce_ticker/
_ingest_raw entry points the real reader uses.
"""
import asyncio
import contextlib
import json

from services.kalshi import websocket as ws_module
from services.kalshi.websocket import KalshiStreamGateway


def _gateway(queue_max: int = 20000, monkeypatch=None) -> KalshiStreamGateway:
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2", ingest_queue_max=queue_max)
    if monkeypatch is not None:
        monkeypatch.setattr(ws_module.config_store, "get", lambda: {
            "realtime_data_plane": {"two_consumer_mode": True},
        })
    gw._gate_cfg_cache = None  # force a fresh config read past the 1s cache
    gw._begin_connection()
    return gw


def _ticker(ticker: str = "TICK-A") -> str:
    return json.dumps({"type": "ticker", "msg": {"market_ticker": ticker}})


async def _noop(*_args):
    return None


def _bind_callbacks(gw: KalshiStreamGateway, on_ticker=_noop) -> None:
    """Same tuple shape run() binds at the top of its own call - see
    flush_pending_tickers()'s docstring for why this has to be reachable
    without threading callbacks through a second call chain."""
    gw._active_callbacks = (_noop, on_ticker, _noop, None, None, None, None)


# --- flush_pending_tickers() basic behavior --------------------------------

def test_flush_pending_tickers_drains_and_dispatches_through_on_ticker(monkeypatch):
    gw = _gateway(monkeypatch=monkeypatch)
    seen = []

    async def on_ticker(ticker_normalized):
        seen.append(ticker_normalized["market_ticker"])

    _bind_callbacks(gw, on_ticker=on_ticker)
    assert gw._ingest_raw(_ticker("TICK-A"), now=1.0) is True
    assert gw._ticker_by_market  # populated via _coalesce_ticker, not the market queue

    flushed = asyncio.run(gw.flush_pending_tickers())

    assert flushed == 1
    assert seen == ["TICK-A"]
    assert gw._ticker_by_market == {}


def test_flush_pending_tickers_is_a_noop_on_an_empty_map(monkeypatch):
    gw = _gateway(monkeypatch=monkeypatch)
    _bind_callbacks(gw)
    assert asyncio.run(gw.flush_pending_tickers()) == 0


def test_flush_pending_tickers_returns_zero_before_run_has_bound_callbacks(monkeypatch):
    """Direct-ingest callers (tests, replay tooling) may populate the map
    without ever calling run() - self._active_callbacks stays None until a
    real connection starts. Must not raise trying to unpack it."""
    gw = _gateway(monkeypatch=monkeypatch)
    assert gw._ingest_raw(_ticker("TICK-A"), now=1.0) is True
    assert gw._active_callbacks is None

    assert asyncio.run(gw.flush_pending_tickers()) == 0
    assert gw._ticker_by_market  # untouched - never popped without callbacks to dispatch to


# --- batch cap ---------------------------------------------------------

def test_flush_pending_tickers_respects_the_batch_cap(monkeypatch):
    gw = _gateway(monkeypatch=monkeypatch)
    _bind_callbacks(gw)
    over_cap = ws_module._TICKER_FLUSH_BATCH_MAX + 5
    for i in range(over_cap):
        ticker = f"TICK-{i}"
        assert gw._ingest_raw(_ticker(ticker), now=float(i)) is True
    assert len(gw._ticker_by_market) == over_cap

    flushed = asyncio.run(gw.flush_pending_tickers())

    assert flushed == ws_module._TICKER_FLUSH_BATCH_MAX
    assert len(gw._ticker_by_market) == over_cap - ws_module._TICKER_FLUSH_BATCH_MAX


def test_flush_pending_tickers_drains_a_backlog_across_repeated_calls(monkeypatch):
    """A map bigger than one batch drains fully over successive calls - the
    real periodic loop's steady state (main.py's _ticker_flush_loop calls
    this every interval_sec, not just once)."""
    gw = _gateway(monkeypatch=monkeypatch)
    _bind_callbacks(gw)
    total = ws_module._TICKER_FLUSH_BATCH_MAX * 2 + 3
    for i in range(total):
        assert gw._ingest_raw(_ticker(f"TICK-{i}"), now=float(i)) is True

    flushed_total = 0
    while gw._ticker_by_market:
        flushed_total += asyncio.run(gw.flush_pending_tickers())

    assert flushed_total == total


# --- interleaving: a same-ticker update arriving mid-flush is not lost -----

def test_flush_pending_tickers_does_not_lose_an_update_for_the_same_ticker_arriving_mid_dispatch(monkeypatch):
    """The exact interleaving the #576 fix's benchmark work verified safe:
    flush_pending_tickers pops (removes from the map) and only THEN awaits
    _process_item/on_ticker - so a fresh WS update for that same market
    landing on a genuinely different, concurrently-running task while
    on_ticker is still executing finds the ticker already gone from the map
    and correctly starts a brand-new entry (_coalesce_ticker's own
    "existing is None" branch), rather than being merged into - or
    overwritten alongside - the entry already popped for dispatch.

    Uses two asyncio.Events to force the real interleaving (a second task,
    not a nested synchronous call inside on_ticker itself - the latter
    would just get re-drained by the same flush_pending_tickers() call
    before it ever returns, since nothing yielded control to a rival task
    in between, which would not actually exercise concurrent access). The
    batch cap is pinned to 1 for this test so the concurrently-arriving
    entry is left pending rather than also being drained by this same
    call (which the real cap would otherwise legitimately do, since that
    entry is genuinely part of the map by the time the loop rechecks it -
    correct behavior, just not what this test is isolating)."""
    monkeypatch.setattr(ws_module, "_TICKER_FLUSH_BATCH_MAX", 1)
    gw = _gateway(monkeypatch=monkeypatch)
    seen = []
    dispatch_started = asyncio.Event()
    let_dispatch_finish = asyncio.Event()

    async def on_ticker(ticker_normalized):
        seen.append(ticker_normalized["market_ticker"])
        dispatch_started.set()
        await let_dispatch_finish.wait()

    _bind_callbacks(gw, on_ticker=on_ticker)
    assert gw._ingest_raw(_ticker("TICK-A"), now=1.0) is True
    assert len(gw._ticker_by_market) == 1

    async def _drive():
        flush_task = asyncio.create_task(gw.flush_pending_tickers())
        await dispatch_started.wait()
        # The entry is already gone from the map before on_ticker's own
        # await even yields back here - proves the pop happens before the
        # dispatch, not after (the ordering the fix's safety depends on).
        assert gw._ticker_by_market == {}
        gw._coalesce_ticker(json.loads(_ticker("TICK-A")), now=2.0)
        let_dispatch_finish.set()
        return await flush_task

    flushed = asyncio.run(_drive())

    assert flushed == 1
    assert seen == ["TICK-A"]  # the old update was dispatched exactly once
    # The concurrently-arriving update was NOT lost - it landed in a fresh
    # map entry rather than vanishing or clobbering the in-flight dispatch.
    assert len(gw._ticker_by_market) == 1
    ((enqueued_at, _data),) = gw._ticker_by_market.values()
    assert enqueued_at == 2.0


# --- counters (ticker_flush_runs / ticker_flush_total) ---------------------

def test_flush_pending_tickers_counts_runs_and_total_lifetime(monkeypatch):
    gw = _gateway(monkeypatch=monkeypatch)
    _bind_callbacks(gw)
    assert gw._ingest_raw(_ticker("A"), now=1.0) is True
    assert gw._ingest_raw(_ticker("B"), now=1.0) is True

    asyncio.run(gw.flush_pending_tickers())  # flushes both (well under the cap)
    asyncio.run(gw.flush_pending_tickers())  # empty map - still a run, zero items

    q = gw.ingest_metrics(now=1.0)["queue"]
    assert q["ticker_flush_runs"] == 2
    assert q["ticker_flush_total"] == 2


def test_ticker_flush_counters_start_at_zero(monkeypatch):
    gw = _gateway(monkeypatch=monkeypatch)
    q = gw.ingest_metrics(now=1.0)["queue"]
    assert q["ticker_flush_runs"] == 0
    assert q["ticker_flush_total"] == 0


# --- self._active_callbacks bound by run() ---------------------------------

def test_run_binds_active_callbacks_before_the_connection_loop(monkeypatch):
    """flush_pending_tickers() needs the same handler set _consume_market_from
    uses, without a second call chain threading callbacks through main.py's
    scheduler wiring again - run() binds them once, up front."""
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")
    assert gw._active_callbacks is None

    async def on_trade(_t):
        return None

    async def on_ticker(_t):
        return None

    # enabled is False (no credentials loaded in this test) - run()'s own
    # while loop immediately hits the `if not self.enabled` branch and
    # sleeps forever; cancel it right after the binding has had a chance to
    # run on the event loop.
    async def _drive():
        task = asyncio.create_task(gw.run(on_trade, on_ticker))
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_drive())
    assert gw._active_callbacks == (on_trade, on_ticker, None, None, None, None, None)
