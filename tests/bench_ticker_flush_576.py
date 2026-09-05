"""Family B benchmark harness (issue #576, scheduled-flush fix, PR #597).

Archived, as-run artifact — not live tooling, not covered by CI or the
targeted-test hook. Committed per the standing lesson (docs/next-action.md,
2026-09-05): a benchmark run in a throwaway subagent worktree produces
numbers that become unfalsifiable to a future reader the moment the
worktree's gone. This is the exact script that produced the
"0/1740 baseline / 1475/1800 at flush_0.25s" and idle-overhead figures cited
in PR #597 and issue #576, recovered from the throwaway benchmark worktree
after the 2026-09-05 WSL restart and copied here unmodified except for this
docstring. Original author: a dispatched benchmark subagent, coordinated by
the "07"/autotrade-49 session lineage.

Exercises the REAL KalshiStreamGateway class (services/kalshi/websocket.py)
under a synthetic sustained trade-class load, with and without the
independent flush loop (flush_pending_tickers/_active_callbacks/
_ticker_flush_runs+total, shipped in this PR) draining _ticker_by_market.

Run: python3 tests/bench_ticker_flush_576.py   (from repo root, or inside
the fastapi container, so `services` imports resolve)
"""
import asyncio
import json
import statistics
import time

from services.kalshi import websocket as ws_module
from services.kalshi.websocket import KalshiStreamGateway


def _patch_config(two_consumer: bool = True):
    # Same monkeypatch shape as tests/test_kalshi_ws_two_consumers.py's
    # _gateway() helper - reader_gate_enabled absent -> falsy -> trades are
    # never reader-gate-filtered, exactly "shadow mode" default.
    ws_module.config_store.get = lambda: {
        "realtime_data_plane": {"two_consumer_mode": two_consumer},
    }


def _gateway(queue_max: int = 20000) -> KalshiStreamGateway:
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2", ingest_queue_max=queue_max)
    gw._gate_cfg_cache = None
    gw._begin_connection()
    return gw


def _trade_json(trade_id: str, ticker: str) -> str:
    # Real trade-channel shape (docs/kalshi/websocket-connection.md's trade
    # message + trade_contract.py's parse targets) - same fields
    # tests/test_kalshi_ws_two_consumers.py's _trade() uses, plus the
    # dollar-price/side fields trade_contract functions read (unused by
    # the reader gate here since reader_gate_enabled is off, but kept
    # realistic).
    return json.dumps({
        "type": "trade",
        "msg": {
            "trade_id": trade_id,
            "market_ticker": ticker,
            "count": 25,
            "yes_price_dollars": "0.42",
            "taker_side": "yes",
        },
    })


def _ticker_json(ticker: str, ts_ms: int, bid: int) -> str:
    return json.dumps({
        "type": "ticker",
        "msg": {"market_ticker": ticker, "ts": ts_ms, "yes_bid": bid},
    })


async def _noop_cb(*_args, **_kwargs) -> None:
    return None


# ---------------------------------------------------------------------------
# Calibration: how fast can _consume_market_from drain a pure trade backlog
# with instant handlers, no flush contention? Tells us how big a seeded
# backlog needs to be to keep get_nowait() non-empty for N wall-clock
# seconds - the exact mechanism issue #576 root-caused (queue draining
# 20000->308 over ~7 real minutes while never once reporting empty).
# ---------------------------------------------------------------------------

async def calibrate_drain_rate(n_trades: int = 20000) -> float:
    _patch_config(two_consumer=True)
    gw = _gateway()
    for i in range(n_trades):
        gw._ingest_raw(_trade_json(str(i), "KXCAL"), now=0.0)
    assert gw._market_queue.qsize() == n_trades

    task = asyncio.create_task(gw._consume_market_from(
        gw._market_queue, on_trade=_noop_cb, on_ticker=_noop_cb, on_status=_noop_cb,
    ))
    start = time.monotonic()
    while gw._market_queue.qsize() > 0 or len(gw._pending_trade_tasks) > 0:
        await asyncio.sleep(0.01)
    elapsed = time.monotonic() - start
    task.cancel()
    rate = n_trades / elapsed if elapsed > 0 else float("inf")
    print(f"[calibrate] drained {n_trades} trade items in {elapsed:.3f}s -> {rate:,.0f} items/sec")
    return rate


# ---------------------------------------------------------------------------
# Core benchmark: seed a persistent trade backlog (keeps market_queue
# non-empty for the whole run - the actual starvation trigger, not merely
# "high rate"), run N synthetic markets' ticker updates concurrently, and
# measure ticker service latency with vs without the flush loop.
# ---------------------------------------------------------------------------

async def run_scenario(
    *,
    label: str,
    backlog_trades: int,
    n_markets: int,
    ticker_update_interval_sec: float,
    run_duration_sec: float,
    flush_interval_sec: float | None,   # None = baseline, no flush task
    queue_max: int = 5_000_000,
) -> dict:
    _patch_config(two_consumer=True)
    gw = _gateway(queue_max=queue_max)
    # This harness drives _consume_market_from directly rather than
    # gw.run() (no real socket) - run() is what normally binds
    # _active_callbacks (once, at the top of that method, for the whole
    # connection's lifetime). Bind it here the same way, since
    # flush_pending_tickers() reads it and would otherwise no-op forever
    # (exactly the "never wired up" degrade-safe default, confirmed by an
    # earlier run of this harness that forgot this line: every flush
    # scenario silently matched the baseline).
    gw._active_callbacks = (_noop_cb, _noop_cb, _noop_cb, None, None, None, None)

    ticker_wait_samples: list[float] = []
    orig_process_item = gw._process_item

    async def _tracking_process_item(item, *args, **kwargs):
        cls = item[1]
        if cls == "ticker":
            enqueued_at = item[0]
            wait = time.monotonic() - enqueued_at
            ticker_wait_samples.append(wait)
        return await orig_process_item(item, *args, **kwargs)

    gw._process_item = _tracking_process_item

    # Seed a persistent backlog so the market queue's get_nowait() succeeds
    # continuously for a controlled stretch - this IS the mechanism
    # (issue #576: queue drained 20000->308 over ~7min while never empty).
    for i in range(backlog_trades):
        gw._ingest_raw(_trade_json(f"seed{i}", "KXBACKLOG"), now=0.0)

    consumer_task = asyncio.create_task(gw._consume_market_from(
        gw._market_queue, on_trade=_noop_cb, on_ticker=_noop_cb, on_status=_noop_cb,
    ))

    flush_task = None
    if flush_interval_sec is not None:
        async def _flush_loop():
            while True:
                await asyncio.sleep(flush_interval_sec)
                await gw.flush_pending_tickers()
        flush_task = asyncio.create_task(_flush_loop())

    async def _ticker_producer():
        tick = 0
        markets = [f"KXMKT-{i:03d}" for i in range(n_markets)]
        while True:
            for m in markets:
                gw._ingest_raw(_ticker_json(m, tick, 50 + (tick % 10)), now=time.monotonic())
            tick += 1
            await asyncio.sleep(ticker_update_interval_sec)

    producer_task = asyncio.create_task(_ticker_producer())

    start = time.monotonic()
    await asyncio.sleep(run_duration_sec)
    elapsed = time.monotonic() - start

    for t in (producer_task, consumer_task):
        t.cancel()
    if flush_task is not None:
        flush_task.cancel()
    await asyncio.sleep(0)  # let cancellations settle

    pending_left = len(gw._ticker_by_market)
    metrics = gw.ingest_metrics(now=time.monotonic())

    result = {
        "label": label,
        "elapsed_sec": round(elapsed, 3),
        "market_queue_depth_at_end": gw._market_queue.qsize(),
        "pending_tickers_at_end": pending_left,
        "ticker_updates_serviced": len(ticker_wait_samples),
        "ticker_wait_max_sec": round(max(ticker_wait_samples), 3) if ticker_wait_samples else None,
        "ticker_wait_mean_sec": round(statistics.mean(ticker_wait_samples), 3) if ticker_wait_samples else None,
        "ticker_wait_p95_sec": (
            round(statistics.quantiles(ticker_wait_samples, n=20)[18], 3)
            if len(ticker_wait_samples) >= 20 else None
        ),
        "coalesced_tickers": metrics["queue"]["coalesced_tickers"],
        "ticker_flush_runs": metrics["queue"]["ticker_flush_runs"],
        "ticker_flush_total": metrics["queue"]["ticker_flush_total"],
    }
    return result


async def measure_idle_overhead(interval_sec: float, n_ticks: int = 2000) -> dict:
    """Overhead of a flush loop ticking with an EMPTY _ticker_by_market -
    the common case (no backlog most of the time). Measures wall-clock
    drift (scheduling overhead beyond the nominal sleep) and confirms the
    early-return path costs no _process_item calls."""
    _patch_config(two_consumer=True)
    gw = _gateway()
    gw._active_callbacks = (_noop_cb, _noop_cb, _noop_cb, None, None, None, None)

    call_count = 0
    orig = gw.flush_pending_tickers

    async def _counting():
        nonlocal call_count
        call_count += 1
        return await orig()

    gw.flush_pending_tickers = _counting

    start = time.monotonic()
    for _ in range(n_ticks):
        await asyncio.sleep(interval_sec)
        await gw.flush_pending_tickers()
    elapsed = time.monotonic() - start
    nominal = n_ticks * interval_sec
    return {
        "interval_sec": interval_sec,
        "n_ticks": n_ticks,
        "elapsed_sec": round(elapsed, 3),
        "nominal_sec": round(nominal, 3),
        "drift_sec": round(elapsed - nominal, 3),
        "drift_per_tick_ms": round(1000 * (elapsed - nominal) / n_ticks, 4),
        "flush_calls": call_count,
        "process_item_calls_from_idle_flush": gw._ticker_flush_total,  # must stay 0
    }


async def main():
    print("=== starvation reproduction + flush comparison (rerun after _active_callbacks fix) ===")
    scenarios = [
        dict(label="baseline_no_flush", flush_interval_sec=None),
        dict(label="flush_5s", flush_interval_sec=5.0),
        dict(label="flush_1s", flush_interval_sec=1.0),
        dict(label="flush_0.25s", flush_interval_sec=0.25),
    ]
    for sc in scenarios:
        result = await run_scenario(
            # 2,500,000: at the ~25-26k/s calibrated drain rate that is
            # ~95-100s to fully drain - a >6x safety margin over the 15s
            # run_duration_sec below against shared-container CPU noise
            # (the 500k backlog used in an earlier run of this harness sat
            # right at the drain boundary and the baseline scenario
            # spuriously showed 0 backlog left / already-recovered timing
            # on a lower-contention run - a measurement artifact, not a
            # mechanism difference; this margin removes that confound).
            backlog_trades=2_500_000,
            n_markets=60,
            ticker_update_interval_sec=0.5,
            run_duration_sec=15.0,
            **sc,
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
