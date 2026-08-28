"""Queue-health metrics on the Kalshi WebSocket ingest path (realtime
data-plane investigation task I1, docs/superpowers/plans/2026-08-25-
realtime-data-plane-investigation.md).

Deterministic: every clock the gateway reads is injectable, no socket is
opened, and the bounded reader->consumer queue is exercised directly via
the same two methods run() itself uses (_ingest_raw / _process_item)."""
import asyncio
import json
import time

import pytest

from services.kalshi import websocket as ws_module
from services.kalshi.websocket import KalshiStreamGateway


def _gateway(queue_max: int = 20000) -> KalshiStreamGateway:
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2", ingest_queue_max=queue_max)
    gw._begin_connection()
    return gw


def _trade(trade_id: str = "t1") -> str:
    return json.dumps({"type": "trade", "msg": {"market_ticker": "TICK-A", "trade_id": trade_id}})


def _ticker() -> str:
    return json.dumps({"type": "ticker", "msg": {"market_ticker": "TICK-A"}})


def _subscribed() -> str:
    return json.dumps({"type": "subscribed", "msg": {"channel": "trade", "sid": 1}})


def _server_error(code: int, text: str = "Subscription buffer overflow") -> str:
    # Exact documented shape: docs/kalshi/websocket-connection.md's
    # errorResponse example - {"id", "type": "error", "msg": {"code", "msg"}}.
    return json.dumps({"id": 7, "type": "error", "msg": {"code": code, "msg": text}})


async def _noop(*_args):
    return None


async def _drain(gw: KalshiStreamGateway, now: float, on_trade=_noop) -> int:
    processed = 0
    while not gw._queue.empty():
        item = gw._queue.get_nowait()
        await gw._process_item(item, on_trade=on_trade, on_ticker=_noop, on_status=_noop, now=now)
        processed += 1
    return processed


# --- received / processed / dropped by message class ----------------------

def test_received_and_processed_are_counted_by_message_class():
    gw = _gateway()
    for raw in (_trade("a"), _trade("b"), _ticker(), _subscribed()):
        assert gw._ingest_raw(raw, now=1.0) is True
    assert asyncio.run(_drain(gw, now=1.0)) == 4

    m = gw.ingest_metrics(now=1.0)
    assert m["messages_received"] == 4
    assert m["received_by_class"] == {"trade": 2, "ticker": 1, "control": 1}
    assert m["processed_by_class"] == {"trade": 2, "ticker": 1, "control": 1}
    assert m["dropped_messages"] == 0
    assert m["dropped_by_class"] == {}


def test_queue_full_drops_are_counted_by_class_and_are_not_server_errors():
    gw = _gateway(queue_max=2)
    assert gw._ingest_raw(_trade("a"), now=1.0) is True
    assert gw._ingest_raw(_ticker(), now=1.0) is True
    assert gw._ingest_raw(_trade("c"), now=1.0) is False  # third message: queue full

    m = gw.ingest_metrics(now=1.0)
    assert m["messages_received"] == 3
    assert m["received_by_class"] == {"trade": 2, "ticker": 1}
    assert m["dropped_messages"] == 1
    assert m["dropped_by_class"] == {"trade": 1}
    assert m["dropped_window"] == 1
    assert m["server_errors"]["total"] == 0
    assert m["error_25_total"] == 0


def test_server_error_25_is_counted_separately_from_local_drops():
    gw = _gateway()
    statuses = []

    async def on_status(status):
        statuses.append(status)

    gw._ingest_raw(_server_error(25), now=1.0)
    gw._ingest_raw(_server_error(6, "Already subscribed"), now=1.0)
    item1, item2 = gw._queue.get_nowait(), gw._queue.get_nowait()
    asyncio.run(gw._process_item(item1, on_trade=_noop, on_ticker=_noop, on_status=on_status, now=2.0))
    asyncio.run(gw._process_item(item2, on_trade=_noop, on_ticker=_noop, on_status=on_status, now=2.0))

    m = gw.ingest_metrics(now=2.0)
    assert m["dropped_messages"] == 0
    assert m["server_errors"]["total"] == 2
    assert m["server_errors"]["by_code"] == {"25": 1, "6": 1}
    last = m["server_errors"]["last"]
    assert (last["code"], last["msg"]) == (6, "Already subscribed")
    assert abs(last["at"] - time.time()) < 5  # wall clock, for humans reading the health page
    assert m["error_25_total"] == 1
    assert m["error_25_window"] == 1
    # The pre-existing status surface is untouched - the error text still
    # reaches on_status exactly as before this instrumentation existed.
    assert any("Kalshi WS error 25" in (s.get("error") or "") for s in statuses)


def test_unknown_message_types_fall_into_a_bounded_other_class():
    gw = _gateway()
    gw._ingest_raw(json.dumps({"type": "something_new", "msg": {}}), now=1.0)
    gw._ingest_raw(json.dumps({"msg": {}}), now=1.0)  # no type at all
    asyncio.run(_drain(gw, now=1.0))
    m = gw.ingest_metrics(now=1.0)
    assert m["received_by_class"] == {"other": 2}
    assert m["processed_by_class"] == {"other": 2}


def test_malformed_json_is_counted_and_neither_enqueued_nor_dropped():
    gw = _gateway()
    assert gw._ingest_raw("{not json", now=1.0) is False
    m = gw.ingest_metrics(now=1.0)
    assert m["messages_received"] == 1
    assert m["malformed_messages"] == 1
    assert m["dropped_messages"] == 0
    assert m["received_by_class"] == {}
    assert m["queue"]["depth"] == 0


# --- queue depth / high-water / oldest age / wait ------------------------

def test_queue_high_water_tracks_peak_depth_across_the_connection():
    gw = _gateway()
    for i in range(3):
        gw._ingest_raw(_trade(str(i)), now=1.0)
    assert gw.ingest_metrics(now=1.0)["queue"] == {
        "depth": 3, "capacity": 20000, "high_water": 3, "oldest_message_age_sec": 0.0,
    }
    asyncio.run(_drain(gw, now=1.0))
    gw._ingest_raw(_trade("x"), now=1.0)
    q = gw.ingest_metrics(now=1.0)["queue"]
    assert q["depth"] == 1
    assert q["high_water"] == 3


def test_oldest_message_age_is_measured_from_the_queue_head_with_the_injected_clock():
    gw = _gateway()
    gw._ingest_raw(_trade("a"), now=10.0)
    gw._ingest_raw(_trade("b"), now=11.0)
    assert gw.ingest_metrics(now=12.0)["queue"]["oldest_message_age_sec"] == pytest.approx(2.0)
    item = gw._queue.get_nowait()
    asyncio.run(gw._process_item(item, on_trade=_noop, on_ticker=_noop, on_status=_noop, now=12.0))
    assert gw.ingest_metrics(now=12.0)["queue"]["oldest_message_age_sec"] == pytest.approx(1.0)
    asyncio.run(_drain(gw, now=12.0))
    assert gw.ingest_metrics(now=12.0)["queue"]["oldest_message_age_sec"] == 0.0


def test_queue_wait_is_the_monotonic_gap_between_enqueue_and_dequeue():
    gw = _gateway()
    gw._ingest_raw(_trade("a"), now=100.0)
    gw._ingest_raw(_trade("b"), now=100.0)
    first, second = gw._queue.get_nowait(), gw._queue.get_nowait()
    asyncio.run(gw._process_item(first, on_trade=_noop, on_ticker=_noop, on_status=_noop, now=100.25))
    asyncio.run(gw._process_item(second, on_trade=_noop, on_ticker=_noop, on_status=_noop, now=102.0))

    w = gw.ingest_metrics(now=102.0)["queue_wait"]
    assert w["last_sec"] == pytest.approx(2.0)
    assert w["window"]["count"] == 2
    assert w["window"]["max_sec"] == pytest.approx(2.0)
    assert w["window"]["avg_sec"] == pytest.approx(1.125)
    assert w["lifetime"]["count"] == 2
    assert w["lifetime"]["max_sec"] == pytest.approx(2.0)
    # Fixed log-spaced buckets: bounded, O(1) per message, and enough to
    # bound a p95 without keeping every sample.
    assert w["buckets"] == {"le_1ms": 0, "le_10ms": 0, "le_100ms": 0, "le_1s": 1, "le_10s": 1, "gt_10s": 0}
    assert w["window"]["p95_upper_bound_sec"] == 10.0


def test_queue_wait_p95_upper_bound_is_the_bucket_holding_the_95th_percentile():
    gw = _gateway()
    for i in range(20):
        gw._ingest_raw(_trade(str(i)), now=0.0)
    # 19 sub-millisecond waits and one 5 s wait: p95 lands in the last
    # sub-ms sample, so the bound is 1 ms, not the outlier's 10 s bucket.
    for i in range(20):
        item = gw._queue.get_nowait()
        now = 5.0 if i == 19 else 0.0005
        asyncio.run(gw._process_item(item, on_trade=_noop, on_ticker=_noop, on_status=_noop, now=now))
    w = gw.ingest_metrics(now=5.0)["queue_wait"]
    assert w["window"]["p95_upper_bound_sec"] == 0.001
    assert w["window"]["max_sec"] == pytest.approx(5.0)


def test_queue_wait_p95_is_none_when_nothing_was_processed_this_window():
    gw = _gateway()
    w = gw.ingest_metrics(now=0.0)["queue_wait"]
    assert w["window"] == {"count": 0, "max_sec": None, "avg_sec": None, "p95_upper_bound_sec": None}
    assert w["last_sec"] is None


# --- handler time by class -------------------------------------------------

def test_handler_time_is_recorded_per_message_class():
    gw = _gateway()

    async def slow_trade(_trade):
        await asyncio.sleep(0.02)

    gw._ingest_raw(_trade("a"), now=0.0)
    gw._ingest_raw(_ticker(), now=0.0)
    asyncio.run(_drain(gw, now=0.0, on_trade=slow_trade))

    h = gw.ingest_metrics(now=0.0)["handler_time_by_class"]
    assert set(h) == {"trade", "ticker"}
    assert h["trade"]["window"]["count"] == 1
    assert h["trade"]["window"]["max_ms"] >= 15.0  # asyncio.sleep(0.02) may wake a hair early
    assert h["trade"]["window"]["avg_ms"] >= 15.0
    assert h["trade"]["lifetime"]["count"] == 1
    assert h["ticker"]["window"]["count"] == 1
    assert h["ticker"]["window"]["max_ms"] < 15.0


# --- consumer exceptions: counted, fault-logged once per class per window --

def test_handler_exceptions_are_counted_and_fault_logged_once_per_class_per_window(monkeypatch):
    recorded = []
    monkeypatch.setattr(ws_module.fault_log, "record", lambda *a, **k: recorded.append((a, k)) or True)
    gw = _gateway()

    async def broken_trade(_trade):
        raise ValueError("bad trade")

    for i in range(3):
        gw._ingest_raw(_trade(str(i)), now=0.0)
    asyncio.run(_drain(gw, now=0.0, on_trade=broken_trade))

    m = gw.ingest_metrics(now=0.0)
    assert m["handler_exceptions_by_class"] == {"trade": 3}
    assert m["handler_exceptions_total"] == 3
    assert m["processed_by_class"] == {"trade": 3}  # still counted as consumed
    assert len(recorded) == 1  # a storm collapses to one fault_log write per window
    args, _kwargs = recorded[0]
    assert args[0] == "kalshi_websocket" and args[1] == "handle_message:trade"
    assert isinstance(args[2], ValueError)

    gw.reset_ingest_window()
    gw._ingest_raw(_trade("again"), now=0.0)
    asyncio.run(_drain(gw, now=0.0, on_trade=broken_trade))
    assert len(recorded) == 2  # a new window earns one more write
    assert gw.ingest_metrics(now=0.0)["handler_exceptions_total"] == 4


# --- window reset semantics ------------------------------------------------

def test_reset_ingest_window_clears_window_stats_but_keeps_lifetime_counters():
    gw = _gateway(queue_max=1)
    gw._ingest_raw(_trade("a"), now=0.0)
    gw._ingest_raw(_trade("b"), now=0.0)  # dropped
    item = gw._queue.get_nowait()
    asyncio.run(gw._process_item(item, on_trade=_noop, on_ticker=_noop, on_status=_noop, now=0.5))
    gw._ingest_raw(_server_error(25), now=0.5)
    item = gw._queue.get_nowait()
    asyncio.run(gw._process_item(item, on_trade=_noop, on_ticker=_noop, on_status=_noop, now=0.5))

    before = gw.ingest_metrics(now=0.5)
    assert before["dropped_window"] == 1 and before["error_25_window"] == 1
    assert before["queue_wait"]["window"]["count"] == 2  # the trade and the error message

    gw.reset_ingest_window()
    after = gw.ingest_metrics(now=0.5)
    assert after["dropped_window"] == 0 and after["error_25_window"] == 0
    assert after["queue_wait"]["window"]["count"] == 0
    assert after["queue_wait"]["buckets"] == {k: 0 for k in before["queue_wait"]["buckets"]}
    assert after["handler_time_by_class"]["trade"]["window"]["count"] == 0
    # Lifetime counters survive the reset.
    assert after["dropped_messages"] == 1
    assert after["error_25_total"] == 1
    assert after["queue_wait"]["lifetime"]["count"] == 2
    assert after["handler_time_by_class"]["trade"]["lifetime"]["count"] == 1
    assert after["queue"]["high_water"] == 1


# --- connection lifecycle: reconnects are counted with a reason ----------

def test_disconnects_are_counted_with_reason_and_time():
    gw = _gateway()
    gw._record_disconnect(RuntimeError("socket closed"), now=42.0)
    c = gw.ingest_metrics(now=42.0)["connection"]
    assert c["connects"] == 1
    assert c["reconnects"] == 1
    assert c["last_disconnect"] == {"reason": "RuntimeError: socket closed", "at": 42.0}


def test_run_counts_a_failed_connect_as_a_reconnect_with_its_reason(monkeypatch):
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")
    # Make the gateway believe it has credentials without touching auth.
    gw._private_key = object()
    gw.key_id = "key"
    monkeypatch.setattr(gw, "_auth_headers", lambda: {})

    def failing_connect(*_args, **_kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr(ws_module.websockets, "connect", failing_connect)
    seen = []

    async def on_status(status):
        seen.append(status)
        if status.get("error"):
            gw._stop = True  # run() checks _stop before its backoff sleep

    asyncio.run(gw.run(on_trade=_noop, on_ticker=_noop, on_status=on_status))
    c = gw.ingest_metrics(now=0.0)["connection"]
    assert c["reconnects"] == 1
    assert c["last_disconnect"]["reason"] == "ConnectionError: refused"
    assert any("refused" in (s.get("error") or "") for s in seen)


# --- backward compatibility: _handle_message still accepts raw strings ----

def test_handle_message_still_accepts_a_raw_json_string():
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")
    trades = []

    async def on_trade(t):
        trades.append(t)

    asyncio.run(gw._handle_message(_trade("raw"), on_trade, _noop, None))
    assert trades and trades[0]["trade_id"] == "raw"


def test_ingest_metrics_before_any_connection_reports_an_empty_queue_rather_than_failing():
    gw = KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")
    m = gw.ingest_metrics(now=0.0)
    assert m["queue"] == {"depth": 0, "capacity": 20000, "high_water": 0, "oldest_message_age_sec": 0.0}
    assert m["connection"]["connects"] == 0


# --- enqueue timestamp handoff to application handlers (I2) ---------------

def test_handlers_can_read_the_message_enqueue_timestamp_via_the_contextvar():
    gw = _gateway()
    seen = []

    async def on_trade(_trade):
        seen.append(ws_module.MESSAGE_ENQUEUED_AT.get())

    gw._ingest_raw(_trade("a"), now=123.5)
    item = gw._queue.get_nowait()
    asyncio.run(gw._process_item(item, on_trade=on_trade, on_ticker=_noop, on_status=_noop, now=124.0))

    assert seen == [123.5]
    assert ws_module.MESSAGE_ENQUEUED_AT.get() is None  # reset after the handler, never leaks


def test_contextvar_is_reset_even_when_the_handler_raises(monkeypatch):
    monkeypatch.setattr(ws_module.fault_log, "record", lambda *a, **k: True)
    gw = _gateway()

    async def broken(_trade):
        raise RuntimeError("boom")

    gw._ingest_raw(_trade("a"), now=1.0)
    asyncio.run(gw._process_item(gw._queue.get_nowait(), on_trade=broken, on_ticker=_noop, on_status=_noop, now=2.0))
    assert ws_module.MESSAGE_ENQUEUED_AT.get() is None


# --- reader-side whale gate, shadow mode (realtime data-plane remediation
# P0 Task 3) - counts, never drops, until Task 17 flips it live ------------

def _trade_with_count(trade_id: str, ticker: str, count_fp: str) -> str:
    return json.dumps({"type": "trade", "msg": {"market_ticker": ticker, "trade_id": trade_id, "count_fp": count_fp}})


def test_shadow_gate_counts_sub_threshold_trades_without_dropping_them(monkeypatch):
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}},
    )
    gw = _gateway()
    assert gw._ingest_raw(_trade_with_count("t1", "K1", "1"), now=1.0) is True
    m = gw.ingest_metrics(now=1.0)
    assert m["gate_would_reject"] == 1
    assert gw._queue.qsize() == 1  # still enqueued - shadow mode, not filtering


def test_shadow_gate_does_not_count_a_whale_sized_trade(monkeypatch):
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}},
    )
    gw = _gateway()
    gw._ingest_raw(_trade_with_count("t1", "K1", "500"), now=1.0)
    assert gw.ingest_metrics(now=1.0)["gate_would_reject"] == 0


def test_gate_exception_falls_open_and_still_enqueues(monkeypatch):
    monkeypatch.setattr(ws_module.whale_gate, "passes", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(ws_module.fault_log, "record", lambda *a, **k: True)
    gw = _gateway()
    assert gw._ingest_raw(_trade_with_count("t1", "K1", "500"), now=1.0) is True
    assert gw.ingest_metrics(now=1.0)["gate_exceptions"] == 1
    assert gw._queue.qsize() == 1


# --- _shadow_gate_check's config cache (code-review finding #6) -----------
#
# config_store.get()'s own docstring assumes "a handful of times per tick,
# not per message"; _shadow_gate_check runs once per inbound trade-class
# message on the reader's hot path (thousands/minute exchange-wide),
# breaking that assumption - measured directly (not assumed) at ~11us/call
# in this environment, roughly 3x json.loads' own per-message cost. Fixed
# with a 1-second cache, same shape as series_watcher.py's own
# _quarantine_active().

def test_shadow_gate_check_reuses_the_cached_config_across_messages_within_the_ttl(monkeypatch):
    calls = {"n": 0}

    def _counting_get():
        calls["n"] += 1
        return {"whale_watcher_kalshi": {"min_contracts": 100}}

    monkeypatch.setattr(ws_module.config_store, "get", _counting_get)
    gw = _gateway()

    gw._ingest_raw(_trade_with_count("t1", "K1", "1"), now=1.0)
    gw._ingest_raw(_trade_with_count("t2", "K1", "1"), now=1.0)
    gw._ingest_raw(_trade_with_count("t3", "K1", "1"), now=1.0)

    assert calls["n"] == 1  # one real config_store.get() for all three messages
    assert gw.ingest_metrics(now=1.0)["gate_would_reject"] == 3  # gate still ran correctly each time


def test_shadow_gate_check_refreshes_the_cached_config_after_the_ttl_expires(monkeypatch):
    calls = {"n": 0}

    def _counting_get():
        calls["n"] += 1
        return {"whale_watcher_kalshi": {"min_contracts": 100}}

    monkeypatch.setattr(ws_module.config_store, "get", _counting_get)
    gw = _gateway()

    gw._ingest_raw(_trade_with_count("t1", "K1", "1"), now=1.0)
    assert calls["n"] == 1
    # Force the cache to look stale (more than the 1-second TTL old) without
    # sleeping in real wall-clock time.
    gw._gate_cfg_cache = (gw._gate_cfg_cache[0] - 2.0, gw._gate_cfg_cache[1])
    gw._ingest_raw(_trade_with_count("t2", "K1", "1"), now=1.0)
    assert calls["n"] == 2  # cache was stale - a fresh config_store.get() happened


# --- reader gate live filtering (realtime data-plane remediation P3 Task 17) --

def _trade_full(trade_id: str, ticker: str, count_fp: str, side: str = "yes",
                 yes_price_dollars: str | None = "0.60") -> str:
    msg = {
        "market_ticker": ticker, "trade_id": trade_id, "count_fp": count_fp,
        "taker_outcome_side": side,
    }
    if yes_price_dollars is not None:
        msg["yes_price_dollars"] = yes_price_dollars
    return json.dumps({"type": "trade", "msg": msg})


def test_reader_gate_enabled_drops_sub_threshold_trades_from_the_queue_but_still_captures(monkeypatch):
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}, "realtime_data_plane": {"reader_gate_enabled": True}},
    )
    captured = []
    rejections = []
    monkeypatch.setattr(ws_module.series_watcher, "record_trade", lambda trade, **k: captured.append(trade))
    monkeypatch.setattr(
        ws_module.candidate_log, "record_rejection",
        lambda *a, **k: rejections.append((a, k)),
    )
    gw = _gateway()

    enqueued = gw._ingest_raw(_trade_full("t1", "K1", "1"), now=1.0)

    assert enqueued is False  # gated out
    assert gw._queue.qsize() == 0
    assert len(captured) == 1  # still captured (design spec's capture contract)
    assert len(rejections) == 1
    args, kwargs = rejections[0]
    assert args == ("K1", "whale_watcher", "min_contracts", 1.0, 100.0)
    assert kwargs["side"] == "yes"
    assert kwargs["unit_cost"] == pytest.approx(0.60)
    m = gw.ingest_metrics(now=1.0)
    assert m["gate_would_reject"] == 1
    assert m["prefiltered"]["trade"] == 1


def test_reader_gate_disabled_keeps_shadow_behavior_even_though_the_gate_still_rejects(monkeypatch):
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}, "realtime_data_plane": {"reader_gate_enabled": False}},
    )
    captured = []
    monkeypatch.setattr(ws_module.series_watcher, "record_trade", lambda trade, **k: captured.append(trade))
    gw = _gateway()

    enqueued = gw._ingest_raw(_trade_full("t1", "K1", "1"), now=1.0)

    assert enqueued is True  # unchanged - still shadow mode
    assert gw._queue.qsize() == 1
    assert captured == []  # not recorded here - the consumer's own on_trade path still will be
    m = gw.ingest_metrics(now=1.0)
    assert m["gate_would_reject"] == 1  # shadow counting still runs
    assert m["prefiltered"]["trade"] == 1  # ingest.prefiltered.trade increments either way


def test_reader_gate_enabled_unresolvable_side_still_filters_but_records_nothing(monkeypatch):
    """Matches kalshi_trade_tape.py's own `if side is None: continue` -
    nothing to attribute a hypothetical win/loss to, so recording would
    just be noise. Still filtered (not enqueued) either way - downstream
    would never have produced a candidate from it."""
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}, "realtime_data_plane": {"reader_gate_enabled": True}},
    )
    captured = []
    rejections = []
    monkeypatch.setattr(ws_module.series_watcher, "record_trade", lambda trade, **k: captured.append(trade))
    monkeypatch.setattr(ws_module.candidate_log, "record_rejection", lambda *a, **k: rejections.append((a, k)))
    gw = _gateway()

    raw = json.dumps({"type": "trade", "msg": {"market_ticker": "K1", "trade_id": "t1", "count_fp": "1"}})
    enqueued = gw._ingest_raw(raw, now=1.0)

    assert enqueued is False  # still filtered
    assert captured == []  # nothing to capture with an unreadable side
    assert rejections == []  # nothing to record either


def test_reader_gate_enabled_unparseable_count_records_the_distinct_gate_name(monkeypatch):
    """Conflating "too small" with "couldn't even be read" would mislabel
    population_gate_summary()'s per-gate breakdown - matches
    kalshi_trade_tape.py's own separate unparseable_count bucket."""
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}, "realtime_data_plane": {"reader_gate_enabled": True}},
    )
    rejections = []
    monkeypatch.setattr(ws_module.series_watcher, "record_trade", lambda trade, **k: None)
    monkeypatch.setattr(ws_module.candidate_log, "record_rejection", lambda *a, **k: rejections.append((a, k)))
    gw = _gateway()

    raw = json.dumps({"type": "trade", "msg": {
        "market_ticker": "K1", "trade_id": "t1", "count_fp": "not-a-number", "taker_outcome_side": "yes",
    }})
    enqueued = gw._ingest_raw(raw, now=1.0)

    assert enqueued is False
    assert len(rejections) == 1
    args, kwargs = rejections[0]
    assert args == ("K1", "whale_watcher", "unparseable_count", 0.0, 0.0)
    assert kwargs == {"side": "yes"}  # no unit_cost kwarg - matches kalshi_trade_tape.py's own call


def test_reader_gate_enabled_unparseable_price_records_unknown_unit_cost_not_fabricated(monkeypatch):
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}, "realtime_data_plane": {"reader_gate_enabled": True}},
    )
    rejections = []
    monkeypatch.setattr(ws_module.series_watcher, "record_trade", lambda trade, **k: None)
    monkeypatch.setattr(ws_module.candidate_log, "record_rejection", lambda *a, **k: rejections.append((a, k)))
    gw = _gateway()

    enqueued = gw._ingest_raw(_trade_full("t1", "K1", "1", yes_price_dollars=None), now=1.0)

    assert enqueued is False
    assert len(rejections) == 1
    _, kwargs = rejections[0]
    assert kwargs["unit_cost"] is None  # unknown, not fabricated as 0


def test_reader_gate_enabled_no_side_price_inversion_for_a_no_taker(monkeypatch):
    """yes_price_dollars is always the YES price by convention - a "no"
    taker's real unit cost is 1 - price, not price itself (the exact no-
    side inversion bug class CLAUDE.md's own "Bug pattern to watch for"
    section documents)."""
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"whale_watcher_kalshi": {"min_contracts": 100}, "realtime_data_plane": {"reader_gate_enabled": True}},
    )
    rejections = []
    monkeypatch.setattr(ws_module.series_watcher, "record_trade", lambda trade, **k: None)
    monkeypatch.setattr(ws_module.candidate_log, "record_rejection", lambda *a, **k: rejections.append((a, k)))
    gw = _gateway()

    enqueued = gw._ingest_raw(_trade_full("t1", "K1", "1", side="no", yes_price_dollars="0.60"), now=1.0)

    assert enqueued is False
    _, kwargs = rejections[0]
    assert kwargs["unit_cost"] == pytest.approx(0.40)  # 1 - 0.60, not 0.60


def test_reader_gate_enabled_gate_exception_still_falls_open_and_enqueues(monkeypatch):
    monkeypatch.setattr(
        ws_module.config_store, "get",
        lambda: {"realtime_data_plane": {"reader_gate_enabled": True}},
    )
    monkeypatch.setattr(ws_module.whale_gate, "passes", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(ws_module.fault_log, "record", lambda *a, **k: True)
    gw = _gateway()

    enqueued = gw._ingest_raw(_trade_full("t1", "K1", "500"), now=1.0)

    assert enqueued is True  # never filtered on an exception, even with the gate live
    assert gw._queue.qsize() == 1
    assert gw.ingest_metrics(now=1.0)["gate_exceptions"] == 1


# --- reconnect gap duration (P8 Task 34) ---------------------------------

def test_reconnect_gap_duration_is_computed_on_the_next_connection():
    gw = _gateway()  # first connect, no prior disconnect
    c = gw.ingest_metrics(now=100.0)["connection"]
    assert c.get("last_gap_sec") is None  # never disconnected - no fabricated 0

    gw._record_disconnect(RuntimeError("socket closed"), now=100.0)
    gw._begin_connection(now=107.5)  # reconnect completes 7.5s later
    c = gw.ingest_metrics(now=107.5)["connection"]
    assert c["last_gap_sec"] == pytest.approx(7.5)
    assert c["gap_sec_window"] == pytest.approx(7.5)


def test_reconnect_gap_window_resets_but_lifetime_value_survives():
    gw = _gateway()
    gw._record_disconnect(RuntimeError("x"), now=10.0)
    gw._begin_connection(now=12.0)
    gw.reset_ingest_window()
    c = gw.ingest_metrics(now=13.0)["connection"]
    assert c["last_gap_sec"] == pytest.approx(2.0)  # lifetime: still known
    assert c.get("gap_sec_window") is None  # window: consumed by the sampler


def test_negative_gap_from_clock_skew_is_not_recorded():
    gw = _gateway()
    gw._record_disconnect(RuntimeError("x"), now=50.0)
    gw._begin_connection(now=49.0)  # wall clock went backwards
    c = gw.ingest_metrics(now=50.0)["connection"]
    assert c.get("last_gap_sec") is None
