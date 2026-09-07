"""Option B (2026-09-03 live-incident fix, docs/archive/lane-2-whale-signal-
calibration/research/2026-09-03-trade-resolve-consumer-blocking-solution-
comparison.md, moved there 2026-09-07, planning-lanes migration, issues
#541/#542): _consume_market_from used to await each trade item's FULL
on_trade handling (which can include a multi-second REST resolve call,
kalshi_trade_tape.py's _resolve_unknown_markets) inline before it could
reach the next queued item, trade OR ticker - real, measured head-of-line
blocking, not a hypothetical. This file falsifies, not just asserts, that
the fix (_dispatch_trade_concurrent/_run_trade_item/_TickerDispatchGate)
actually closes it:

  test_a_ticker_right_behind_two_slow_trades_is_not_stuck_behind_them
      reproduces the exact live mechanism directly: two_consumer_mode
      routes trade AND ticker onto ONE queue/consumer, so a slow trade
      used to starve the very next ticker update - price freshness,
      check_exits - behind it (research doc §1.1's "new fact"). Shows the
      ticker item is reached and processed WHILE two slow trades (on
      different tickers) are still blocked mid-flight.

  test_bounded_concurrency_caps_in_flight_trade_dispatch_at_four
      6 trades on 6 different tickers, all blocking: shows in-flight
      dispatch caps at exactly _TRADE_DISPATCH_CONCURRENCY (4), not 6
      (unbounded) and not fewer - "bounded and observable," matching
      _scoring_pool.py's own precedent, not eliminated backpressure.

  test_same_ticker_prints_are_sequenced_not_concurrent
      two trades on the SAME ticker never have their on_trade handling
      running at the same time - the one real ordering invariant Option B
      must preserve (same-ticker resolve+score can't race
      KalshiTradeTapeProvider._market_cache's write).
"""
import asyncio
import json

from services.kalshi import websocket as ws_module
from services.kalshi.websocket import KalshiStreamGateway


def _gateway(monkeypatch, queue_max: int = 20000) -> KalshiStreamGateway:
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2", ingest_queue_max=queue_max)
    # Same pattern tests/test_kalshi_ws_two_consumers.py already uses -
    # two_consumer_mode routes trade AND ticker onto the shared market
    # queue this fix targets (the live config, config/settings.yaml:72).
    monkeypatch.setattr(ws_module.config_store, "get", lambda: {
        "realtime_data_plane": {"two_consumer_mode": True},
    })
    gw._gate_cfg_cache = None  # force a fresh config read past the 1s cache
    gw._begin_connection()
    return gw


def _trade_msg(trade_id: str, ticker: str) -> str:
    return json.dumps({"type": "trade", "msg": {"trade_id": trade_id, "market_ticker": ticker, "count": 500}})


def _ticker_msg(ticker: str) -> str:
    return json.dumps({"type": "ticker", "msg": {"market_ticker": ticker}})


async def _wait_until(predicate, *, timeout: float = 2.0, interval: float = 0.005):
    async def _poll():
        while not predicate():
            await asyncio.sleep(interval)
    await asyncio.wait_for(_poll(), timeout=timeout)


def _cancel_all(consumer, gw):
    consumer.cancel()
    for task in list(gw._pending_trade_tasks):
        task.cancel()


def test_a_ticker_right_behind_two_slow_trades_is_not_stuck_behind_them(monkeypatch):
    async def scenario():
        gw = _gateway(monkeypatch)
        event_a = asyncio.Event()
        event_b = asyncio.Event()
        trade_entered = []
        trade_exited = []
        ticker_seen = []

        async def on_trade(trade):
            ticker = trade.get("ticker")
            trade_entered.append(ticker)
            await (event_a if ticker == "SLOW-A" else event_b).wait()
            trade_exited.append(ticker)

        async def on_ticker(update):
            ticker_seen.append(update.get("ticker"))

        gw._ingest_raw(_trade_msg("t1", "SLOW-A"))
        gw._ingest_raw(_trade_msg("t2", "SLOW-B"))
        gw._ingest_raw(_ticker_msg("FAST-C"))

        consumer = asyncio.create_task(
            gw._consume_market_from(gw._market_queue, on_trade=on_trade, on_ticker=on_ticker)
        )
        try:
            await _wait_until(lambda: len(ticker_seen) == 1)
            # Both slow trades were dispatched (entered on_trade) but
            # NEITHER has finished - the ticker update still reached the
            # consumer and was processed while they were in flight. Before
            # this fix, the consumer could not have reached the ticker item
            # at all until trade1's (then trade2's) full handling returned.
            assert sorted(trade_entered) == ["SLOW-A", "SLOW-B"]
            assert trade_exited == []
            assert ticker_seen == ["FAST-C"]

            event_a.set()
            event_b.set()
            await _wait_until(lambda: len(trade_exited) == 2)
            assert sorted(trade_exited) == ["SLOW-A", "SLOW-B"]
        finally:
            _cancel_all(consumer, gw)

    asyncio.run(scenario())


def test_bounded_concurrency_caps_in_flight_trade_dispatch_at_four(monkeypatch):
    async def scenario():
        gw = _gateway(monkeypatch)
        release = asyncio.Event()
        entered = []
        in_flight = {"n": 0, "max": 0}

        async def on_trade(trade):
            in_flight["n"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["n"])
            entered.append(trade.get("ticker"))
            await release.wait()
            in_flight["n"] -= 1

        for i in range(6):
            gw._ingest_raw(_trade_msg(f"t{i}", f"TICK-{i}"))

        consumer = asyncio.create_task(gw._consume_market_from(gw._market_queue, on_trade=on_trade))
        try:
            await _wait_until(lambda: len(entered) >= 4)
            await asyncio.sleep(0.05)  # let anything further settle before asserting a ceiling
            assert len(entered) == 4, "exactly the N=4 bound, not 6 (unbounded) and not fewer"
            assert in_flight["max"] == 4
            # The remaining 2 items are genuinely still queued behind the
            # bound (the consumer itself is parked on the semaphore, not
            # spinning) - not silently dropped.
            assert gw._market_queue.qsize() + len(gw._pending_trade_tasks) >= 1

            release.set()
            await _wait_until(lambda: len(entered) == 6)
            assert sorted(entered) == sorted(f"TICK-{i}" for i in range(6))
        finally:
            _cancel_all(consumer, gw)

    asyncio.run(scenario())


def test_same_ticker_prints_are_sequenced_not_concurrent(monkeypatch):
    async def scenario():
        gw = _gateway(monkeypatch)
        release_first = asyncio.Event()
        order = []
        overlap_detected = {"flag": False}
        active = {"n": 0}

        async def on_trade(trade):
            active["n"] += 1
            if active["n"] > 1:
                overlap_detected["flag"] = True
            trade_id = trade.get("trade_id")
            order.append(("enter", trade_id))
            if trade_id == "t1":
                await release_first.wait()
            active["n"] -= 1
            order.append(("exit", trade_id))

        gw._ingest_raw(_trade_msg("t1", "SAME-TICK"))
        gw._ingest_raw(_trade_msg("t2", "SAME-TICK"))

        consumer = asyncio.create_task(gw._consume_market_from(gw._market_queue, on_trade=on_trade))
        try:
            await _wait_until(lambda: len(order) >= 1)
            await asyncio.sleep(0.05)
            # t1 has entered (blocked on release_first); t2 must NOT have
            # entered yet - _TickerDispatchGate keeps same-ticker prints
            # sequential even though both were dispatched concurrently
            # (well under the N=4 bound).
            assert order == [("enter", "t1")]

            release_first.set()
            await _wait_until(lambda: len(order) == 4)
            assert order == [("enter", "t1"), ("exit", "t1"), ("enter", "t2"), ("exit", "t2")]
            assert overlap_detected["flag"] is False
        finally:
            _cancel_all(consumer, gw)

    asyncio.run(scenario())


def test_different_tickers_are_not_serialized_by_the_ticker_gate(monkeypatch):
    """Sanity check on the flip side of the invariant above: the ticker
    gate must not accidentally serialize UNRELATED tickers too (that would
    silently degrade Option B back toward the old strictly-serial
    behavior)."""
    async def scenario():
        gw = _gateway(monkeypatch)
        release = asyncio.Event()
        entered = []

        async def on_trade(trade):
            entered.append(trade.get("ticker"))
            await release.wait()

        gw._ingest_raw(_trade_msg("t1", "TICK-A"))
        gw._ingest_raw(_trade_msg("t2", "TICK-B"))

        consumer = asyncio.create_task(gw._consume_market_from(gw._market_queue, on_trade=on_trade))
        try:
            await _wait_until(lambda: len(entered) == 2)
            assert sorted(entered) == ["TICK-A", "TICK-B"]  # both entered before either was released
        finally:
            release.set()
            _cancel_all(consumer, gw)

    asyncio.run(scenario())
