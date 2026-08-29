"""P4 Task 18 (realtime-data-plane-remediation): critical/market two-consumer
mode, behind realtime_data_plane.two_consumer_mode (default false - single
queue, byte-for-byte today's behavior).

Why: the ingest queue is drained by ONE serial consumer, so any slow
message class stalls every other class behind it - the 2026-08-29
settlement-cascade finding (lifecycle events at 1.5s each starved trades;
71/81 drop episodes at :00-:09), and before it the cfbenchmarks starvation
incident documented in run()'s own comments. Splitting decision-critical
classes (fill/position/lifecycle/control) from bulk market flow
(trade/ticker/index/other) bounds the blast radius: one slow class can now
only stall its own queue.
"""
import asyncio
import json
import time

import pytest

from services.kalshi import websocket as ws_module
from services.kalshi.websocket import KalshiStreamGateway


def _gateway(queue_max: int = 20000, two_consumer: bool = False, monkeypatch=None) -> KalshiStreamGateway:
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2", ingest_queue_max=queue_max)
    if monkeypatch is not None:
        monkeypatch.setattr(ws_module.config_store, "get", lambda: {
            "realtime_data_plane": {"two_consumer_mode": two_consumer},
        })
    gw._gate_cfg_cache = None  # force a fresh config read past the 1s cache
    gw._begin_connection()
    return gw


def _fill(fill_id: str = "f1") -> str:
    return json.dumps({"type": "fill", "msg": {"trade_id": fill_id, "market_ticker": "K1", "count": 1, "yes_price": 50, "side": "yes", "action": "buy"}})


def _trade(trade_id: str = "t1") -> str:
    return json.dumps({"type": "trade", "msg": {"trade_id": trade_id, "market_ticker": "K1", "count": 500}})


def _lifecycle() -> str:
    return json.dumps({"type": "market_lifecycle_v2", "msg": {"market_ticker": "K1", "event_type": "settled", "settled_ts": 123}})


def _ticker() -> str:
    return json.dumps({"type": "ticker", "msg": {"market_ticker": "K1"}})


def _control() -> str:
    return json.dumps({"type": "subscribed", "msg": {"channel": "trade", "sid": 1}})


def test_fill_and_trade_land_on_separate_queues_when_enabled(monkeypatch):
    gw = _gateway(two_consumer=True, monkeypatch=monkeypatch)
    assert gw._ingest_raw(_fill())
    assert gw._ingest_raw(_trade())
    assert gw._critical_queue.qsize() == 1
    assert gw._market_queue.qsize() == 1
    assert gw._queue.qsize() == 0


def test_critical_classes_route_critical_and_market_classes_route_market(monkeypatch):
    gw = _gateway(two_consumer=True, monkeypatch=monkeypatch)
    gw._ingest_raw(_fill())
    gw._ingest_raw(_lifecycle())
    gw._ingest_raw(_control())
    gw._ingest_raw(_trade())
    gw._ingest_raw(_ticker())
    assert gw._critical_queue.qsize() == 3  # fill + lifecycle + control
    assert gw._market_queue.qsize() == 2   # trade + ticker


def test_single_queue_mode_is_unchanged_when_disabled(monkeypatch):
    gw = _gateway(two_consumer=False, monkeypatch=monkeypatch)
    gw._ingest_raw(_fill())
    gw._ingest_raw(_trade())
    assert gw._queue.qsize() == 2
    assert gw._critical_queue.qsize() == 0
    assert gw._market_queue.qsize() == 0


def test_a_full_market_queue_sheds_without_touching_the_critical_queue(monkeypatch):
    gw = _gateway(queue_max=2, two_consumer=True, monkeypatch=monkeypatch)
    for i in range(4):
        gw._ingest_raw(_trade(f"t{i}"))
    assert gw._ingest_raw(_fill())  # critical still has room
    assert gw.dropped_messages == 2
    assert gw._dropped_by_class == {"trade": 2}
    assert gw._critical_queue.qsize() == 1


def test_both_consumers_drain_independently(monkeypatch):
    gw = _gateway(two_consumer=True, monkeypatch=monkeypatch)
    handled = []

    async def fake_handle(data, *cbs) -> None:
        handled.append(data.get("type"))

    gw._handle_message = fake_handle
    gw._ingest_raw(_fill())
    gw._ingest_raw(_trade())

    async def _drive():
        tasks = [
            asyncio.create_task(gw._consume_from(gw._critical_queue)),
            asyncio.create_task(gw._consume_from(gw._market_queue)),
        ]
        await asyncio.wait_for(gw._critical_queue.join(), timeout=1.0)
        await asyncio.wait_for(gw._market_queue.join(), timeout=1.0)
        for t in tasks:
            t.cancel()

    asyncio.run(_drive())
    assert sorted(handled) == ["fill", "trade"]
    assert gw._processed_by_class == {"fill": 1, "trade": 1}


def test_ingest_metrics_depth_and_oldest_age_cover_both_queues(monkeypatch):
    gw = _gateway(two_consumer=True, monkeypatch=monkeypatch)
    gw._ingest_raw(_fill(), now=100.0)
    gw._ingest_raw(_trade(), now=50.0)  # older message sits on the MARKET queue
    metrics = gw.ingest_metrics(now=110.0)
    assert metrics["queue"]["depth"] == 2
    assert metrics["queue"]["oldest_message_age_sec"] == pytest.approx(60.0)


def test_liveness_backstop_sees_backlog_on_a_split_queue(monkeypatch):
    # ensure_consumer_progressing's "queue holds a backlog" input must not
    # go blind just because the backlog lives on _market_queue now.
    gw = _gateway(two_consumer=True, monkeypatch=monkeypatch)
    gw._ingest_raw(_trade())
    assert gw._queue.qsize() == 0
    assert gw._total_queue_depth() == 1
