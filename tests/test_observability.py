"""
services/observability/observability.py - bounded, low-frequency persistence
of runtime metrics the app already computes in memory (tick timing, WS
message counters, ...), docs/superpowers/plans/2026-08-24-quality-control-
plane.md Task 9. capture_from_runtime/maybe_capture take `state` (and the
trade/index stream objects) as explicit arguments rather than importing
services.app_state directly, so this module never triggers app_state's
eager PaperBroker/RiskManager/ShadowTrader construction - unlike
services/backup/backup.py, no real-data-import guard is needed here for
the pure observability.py tests.

The maybe_capture cold-start seeding tests below mirror tests/test_backup.py's
own shape on purpose: services/backup/backup.py's README.md documents a
real live bug (2026-08-23) where trusting in-memory-only last-run state made
every uvicorn --reload cycle fire an immediate, unnecessary re-run. Task 9's
own plan explicitly calls out the same restart-safety requirement for
observability sampling, so it gets the same regression coverage up front
instead of waiting to rediscover the bug live.

Task 10 (docs/superpowers/plans/2026-08-24-quality-control-plane.md) adds
runtime_findings() below - the same "unknown/insufficient evidence is
better than a fabricated verdict" discipline as everywhere else in this
app, applied to the QCP's shared QualityFinding model instead of
diagnostics.py's own Check: a rule that can't judge (no data yet, feature
not enabled, one isolated blip) omits a finding rather than asserting
health OR failure.
"""
import time
import types

import pytest

from services.observability import observability


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(observability, "DB_PATH", tmp_path / "observability.db")
    yield


def _fake_stream(dropped_messages=0, messages_received=0, enabled=True):
    return types.SimpleNamespace(
        dropped_messages=dropped_messages, messages_received=messages_received, enabled=enabled,
    )


# --- schema / persistence ----------------------------------------------

def test_schema_creation_is_additive_across_repeated_connects():
    observability.record_sample("tick.duration_sec", 1.0, observed_at=1000.0)
    observability._connect().close()  # simulates a second process start / uvicorn reload
    observability.record_sample("tick.duration_sec", 2.0, observed_at=1001.0)

    rows = observability.history("tick.duration_sec", since_ts=0.0)
    assert [r["value"] for r in rows] == [1.0, 2.0]


def test_history_returns_samples_in_ascending_observed_at_order():
    observability.record_sample("tick.duration_sec", 3.0, observed_at=1002.0)
    observability.record_sample("tick.duration_sec", 1.0, observed_at=1000.0)
    observability.record_sample("tick.duration_sec", 2.0, observed_at=1001.0)

    rows = observability.history("tick.duration_sec", since_ts=0.0)
    assert [r["observed_at"] for r in rows] == [1000.0, 1001.0, 1002.0]


def test_history_respects_since_ts_and_limit():
    for i in range(5):
        observability.record_sample("tick.duration_sec", float(i), observed_at=1000.0 + i)

    rows = observability.history("tick.duration_sec", since_ts=1002.0)
    assert [r["value"] for r in rows] == [2.0, 3.0, 4.0]

    capped = observability.history("tick.duration_sec", since_ts=0.0, limit=2)
    assert len(capped) == 2


def test_record_samples_bulk_writes_one_row_per_metric():
    observability.record_samples_bulk({"a.metric": 1.0, "b.metric": 2.0}, observed_at=1000.0)

    assert observability.history("a.metric", since_ts=0.0)[0]["value"] == 1.0
    assert observability.history("b.metric", since_ts=0.0)[0]["value"] == 2.0


# --- capture_from_runtime: pure mapping, no I/O -------------------------

def test_capture_from_runtime_maps_stable_metric_names():
    state = {
        "last_tick_duration_sec": 1.25,
        "last_tick_rate_limit_hits": 0,
        "tick_phase_timings": {"market_fetch": 0.4, "exit_management": 0.1},
        "trade_stream_perf": {"messages_per_sec": 180.0, "avg_handler_ms": 1.7},
    }
    trade_stream = _fake_stream(dropped_messages=3, messages_received=500)
    index_stream = _fake_stream(dropped_messages=0, messages_received=20)

    metrics = observability.capture_from_runtime({}, state, trade_stream, index_stream)

    assert metrics["tick.duration_sec"] == 1.25
    assert metrics["tick.rate_limit_hits"] == 0.0
    assert metrics["tick.phase.market_fetch_sec"] == 0.4
    assert metrics["tick.phase.exit_management_sec"] == 0.1
    assert metrics["trade_stream.messages_per_sec"] == 180.0
    assert metrics["trade_stream.avg_handler_ms"] == 1.7
    assert metrics["trade_stream.dropped_messages"] == 3.0
    assert metrics["trade_stream.messages_received"] == 500.0
    assert metrics["index_stream.dropped_messages"] == 0.0
    assert metrics["index_stream.messages_received"] == 20.0


def test_capture_from_runtime_omits_missing_sources_instead_of_fabricating_zero():
    state = {
        "last_tick_duration_sec": None,
        "last_tick_rate_limit_hits": None,
        "tick_phase_timings": {},
        "trade_stream_perf": None,
    }

    metrics = observability.capture_from_runtime({}, state, None, None)

    assert metrics == {}


# --- kalshi_rest.* (QCP Task 15) -----------------------------------------
#
# capture_from_runtime reads state["last_tick_http_metrics"], an already-
# computed since-last-tick snapshot main.py's trading_loop stashes via
# http_client.http_metrics_snapshot(reset=True) - capture_from_runtime
# itself must stay a pure read (see its own updated docstring) since it's
# reused as-is by both the periodic persisted sampler and the on-demand
# GET /api/observability/current route.


def test_capture_from_runtime_flattens_kalshi_rest_metrics_per_endpoint():
    state = {
        "last_tick_http_metrics": {
            "get_markets": {"calls": 12, "successes": 11, "errors": 0, "rate_limited": 1, "avg_latency_ms": 83.2},
            "create_order_v2": {"calls": 1, "successes": 1, "errors": 0, "rate_limited": 0, "avg_latency_ms": 210.5},
        },
    }

    metrics = observability.capture_from_runtime({}, state, None, None)

    assert metrics["kalshi_rest.get_markets.calls"] == 12.0
    assert metrics["kalshi_rest.get_markets.errors"] == 0.0
    assert metrics["kalshi_rest.get_markets.rate_limited"] == 1.0
    assert metrics["kalshi_rest.get_markets.avg_latency_ms"] == 83.2
    assert metrics["kalshi_rest.create_order_v2.calls"] == 1.0
    assert metrics["kalshi_rest.create_order_v2.avg_latency_ms"] == 210.5


def test_capture_from_runtime_omits_kalshi_rest_avg_latency_when_there_were_no_successes():
    state = {
        "last_tick_http_metrics": {
            "get_markets": {"calls": 3, "successes": 0, "errors": 0, "rate_limited": 3, "avg_latency_ms": None},
        },
    }

    metrics = observability.capture_from_runtime({}, state, None, None)

    assert metrics["kalshi_rest.get_markets.calls"] == 3.0
    assert metrics["kalshi_rest.get_markets.rate_limited"] == 3.0
    assert "kalshi_rest.get_markets.avg_latency_ms" not in metrics


def test_capture_from_runtime_has_no_kalshi_rest_metrics_when_nothing_called_this_tick():
    metrics = observability.capture_from_runtime({}, {"last_tick_http_metrics": {}}, None, None)
    assert metrics == {}

    metrics = observability.capture_from_runtime({}, {}, None, None)
    assert metrics == {}


# --- maybe_capture: interval gate + restart-safe cold-start seeding -----

_CFG = {"observability": {"enabled": True, "sample_interval_sec": 60}}
_STATE = {
    "last_tick_duration_sec": 1.0, "last_tick_rate_limit_hits": 0, "tick_phase_timings": {},
    "trade_stream_perf": None,
}


def _fresh_state():
    return {**_STATE, "observability": {"last_sample_at": 0.0}}


def test_maybe_capture_cold_start_does_not_refire_when_a_recent_sample_is_already_persisted():
    observability.record_sample("tick.duration_sec", 1.0, observed_at=time.time() - 5)
    state = _fresh_state()

    observability.maybe_capture(_CFG, state, None, None)

    assert observability.history("tick.duration_sec", since_ts=0.0)[-1]["value"] == 1.0  # no new sample yet
    assert state["observability"]["last_sample_at"] > 0.0  # seeded from persisted history, not left at 0.0


def test_maybe_capture_cold_start_fires_when_persisted_history_is_older_than_the_interval():
    observability.record_sample("tick.duration_sec", 1.0, observed_at=time.time() - 999_999)
    state = _fresh_state()

    observability.maybe_capture(_CFG, state, None, None)

    rows = observability.history("tick.duration_sec", since_ts=0.0)
    assert len(rows) == 2  # the seeded row plus a real new capture


def test_maybe_capture_fires_promptly_on_a_genuinely_fresh_install_with_no_history():
    state = _fresh_state()

    observability.maybe_capture(_CFG, state, None, None)

    assert observability.history("tick.duration_sec", since_ts=0.0)


def test_maybe_capture_skips_within_interval_once_already_sampled_this_process():
    state = _fresh_state()
    observability.maybe_capture(_CFG, state, None, None)
    first_count = len(observability.history("tick.duration_sec", since_ts=0.0))

    observability.maybe_capture(_CFG, state, None, None)

    assert len(observability.history("tick.duration_sec", since_ts=0.0)) == first_count


def test_maybe_capture_disabled_never_fires_regardless_of_history():
    state = _fresh_state()

    observability.maybe_capture({"observability": {"enabled": False}}, state, None, None)

    assert observability.history("tick.duration_sec", since_ts=0.0) == []


# --- prune ---------------------------------------------------------------

def test_prune_removes_samples_older_than_retention_and_keeps_the_rest():
    now = time.time()
    observability.record_sample("tick.duration_sec", 1.0, observed_at=now - 400 * 3600)  # older than 336h
    observability.record_sample("tick.duration_sec", 2.0, observed_at=now - 1 * 3600)  # recent

    observability.prune(retention_hours=336, now=now)

    remaining = observability.history("tick.duration_sec", since_ts=0.0)
    assert [r["value"] for r in remaining] == [2.0]


# --- summary ---------------------------------------------------------------

def test_summary_aggregates_per_metric_within_the_window():
    now = time.time()
    observability.record_sample("tick.duration_sec", 1.0, observed_at=now - 10)
    observability.record_sample("tick.duration_sec", 3.0, observed_at=now - 5)
    observability.record_sample("tick.duration_sec", 100.0, observed_at=now - 999_999)  # outside window

    result = observability.summary(hours=1, now=now)

    assert result["tick.duration_sec"]["count"] == 2
    assert result["tick.duration_sec"]["min"] == 1.0
    assert result["tick.duration_sec"]["max"] == 3.0
    assert result["tick.duration_sec"]["avg"] == 2.0


# --- runtime_findings: anomaly rules (QCP Task 10) -----------------------

_POLL_CFG = {"kalshi": {"poll_interval_sec": 6}}


def _finding_ids(findings):
    return {f.finding_id for f in findings}


# tick duration vs. configured poll interval

def test_tick_duration_finding_warns_when_it_exceeds_the_poll_interval():
    state = {"last_tick_duration_sec": 9.5}
    findings = observability.runtime_findings(_POLL_CFG, state, None, None)

    matches = [f for f in findings if f.check == "tick-duration"]
    assert len(matches) == 1
    assert matches[0].severity == "warning"
    assert matches[0].confidence == "high"
    assert matches[0].evidence == {"last_tick_duration_sec": 9.5, "poll_interval_sec": 6}


def test_tick_duration_finding_absent_when_within_the_poll_interval():
    state = {"last_tick_duration_sec": 3.0}
    findings = observability.runtime_findings(_POLL_CFG, state, None, None)

    assert not [f for f in findings if f.check == "tick-duration"]


def test_tick_duration_finding_absent_when_no_duration_recorded_yet():
    state = {"last_tick_duration_sec": None}
    findings = observability.runtime_findings(_POLL_CFG, state, None, None)

    assert not [f for f in findings if f.check == "tick-duration"]


# non-zero dropped WS messages

def test_dropped_messages_finding_is_an_error_when_nonzero():
    trade_stream = _fake_stream(dropped_messages=5)
    findings = observability.runtime_findings(_POLL_CFG, {}, trade_stream, None)

    matches = [f for f in findings if f.check == "ws-dropped-messages"]
    assert len(matches) == 1
    assert matches[0].severity == "error"
    assert matches[0].confidence == "high"
    assert matches[0].scope == "trade_stream"


def test_dropped_messages_finding_covers_index_stream_independently():
    index_stream = _fake_stream(dropped_messages=2)
    findings = observability.runtime_findings(_POLL_CFG, {}, None, index_stream)

    matches = [f for f in findings if f.check == "ws-dropped-messages"]
    assert len(matches) == 1
    assert matches[0].scope == "index_stream"


def test_dropped_messages_finding_absent_when_zero_or_stream_missing():
    findings = observability.runtime_findings(
        _POLL_CFG, {}, _fake_stream(dropped_messages=0), None,
    )
    assert not [f for f in findings if f.check == "ws-dropped-messages"]


# stream disconnected while the app is otherwise running

def test_stream_disconnected_finding_warns_when_enabled_but_not_connected():
    state = {"trade_stream_status": {"enabled": True, "connected": False}}
    findings = observability.runtime_findings(_POLL_CFG, state, None, None)

    matches = [f for f in findings if f.check == "stream-connection"]
    assert len(matches) == 1
    assert matches[0].severity == "warning"
    assert matches[0].scope == "trade_stream"


def test_stream_disconnected_finding_absent_when_connected():
    state = {"trade_stream_status": {"enabled": True, "connected": True}}
    findings = observability.runtime_findings(_POLL_CFG, state, None, None)

    assert not [f for f in findings if f.check == "stream-connection"]


def test_stream_disconnected_finding_absent_when_feature_not_enabled():
    """A stream nobody turned on is expected to be disconnected - not an
    anomaly, per the same "insufficient evidence" discipline as everywhere
    else here."""
    state = {"trade_stream_status": {"enabled": False, "connected": False}}
    findings = observability.runtime_findings(_POLL_CFG, state, None, None)

    assert not [f for f in findings if f.check == "stream-connection"]


def test_stream_disconnected_finding_absent_with_no_status_evidence_yet():
    """index_stream_status has no app_state.py default - it's simply absent
    until the stream's on_status callback fires at least once. Absence must
    read as "no evidence yet," never as "disconnected.\""""
    findings = observability.runtime_findings(_POLL_CFG, {}, None, None)

    assert not [f for f in findings if f.check == "stream-connection"]


# repeated recent rate-limit hits, not one isolated hit

def test_repeated_rate_limit_hits_finding_absent_with_no_persisted_history():
    findings = observability.runtime_findings(_POLL_CFG, {}, None, None)

    assert not [f for f in findings if f.check == "rate-limit-hits"]


def test_repeated_rate_limit_hits_finding_absent_for_one_isolated_hit():
    now = time.time()
    for i, value in enumerate([0, 0, 3, 0, 0]):
        observability.record_sample("tick.rate_limit_hits", float(value), observed_at=now - i * 60)

    findings = observability.runtime_findings(_POLL_CFG, {}, None, None, now=now)

    assert not [f for f in findings if f.check == "rate-limit-hits"]


def test_repeated_rate_limit_hits_finding_warns_when_hits_recur():
    now = time.time()
    for i, value in enumerate([2, 0, 1, 0, 4]):
        observability.record_sample("tick.rate_limit_hits", float(value), observed_at=now - i * 60)

    findings = observability.runtime_findings(_POLL_CFG, {}, None, None, now=now)

    matches = [f for f in findings if f.check == "rate-limit-hits"]
    assert len(matches) == 1
    assert matches[0].severity == "warning"
    assert matches[0].evidence["nonzero_count"] == 3


def test_runtime_findings_composes_every_applicable_rule():
    now = time.time()
    observability.record_sample("tick.rate_limit_hits", 1.0, observed_at=now - 60)
    observability.record_sample("tick.rate_limit_hits", 1.0, observed_at=now - 120)
    observability.record_sample("tick.rate_limit_hits", 1.0, observed_at=now - 180)
    state = {
        "last_tick_duration_sec": 9.5,
        "trade_stream_status": {"enabled": True, "connected": False},
    }
    trade_stream = _fake_stream(dropped_messages=1)

    findings = observability.runtime_findings(_POLL_CFG, state, trade_stream, None, now=now)

    assert _finding_ids(findings) == {
        "observability:tick-duration-exceeded:trading_loop",
        "observability:ws-dropped-messages:trade_stream",
        "observability:stream-disconnected:trade_stream",
        "observability:repeated-rate-limit-hits:kalshi_client",
    }


# --- candidate_retry abandonment (realtime data-plane remediation P2 Task 13) ---

def test_candidate_retry_abandoned_finding_absent_when_zero(monkeypatch):
    from services import candidate_retry
    monkeypatch.setattr(candidate_retry, "_window_abandoned", 0)
    findings = observability.runtime_findings(_POLL_CFG, {}, None, None)
    assert not [f for f in findings if f.check == "candidate-retry-abandoned"]


def test_candidate_retry_abandoned_finding_warns_when_nonzero(monkeypatch):
    from services import candidate_retry
    monkeypatch.setattr(candidate_retry, "_window_abandoned", 2)
    findings = observability.runtime_findings(_POLL_CFG, {}, None, None)

    matches = [f for f in findings if f.check == "candidate-retry-abandoned"]
    assert len(matches) == 1
    assert matches[0].severity == "warning"
    assert matches[0].confidence == "high"
    assert matches[0].evidence == {"abandoned": 2}


# --- WebSocket ingest queue-health metrics (realtime data-plane task I1) ---

def _fake_ingest_metrics() -> dict:
    return {
        "messages_received": 500, "dropped_messages": 4, "dropped_window": 1, "malformed_messages": 2,
        "received_by_class": {"trade": 480, "ticker": 20},
        "processed_by_class": {"trade": 476, "ticker": 20},
        "dropped_by_class": {"trade": 4},
        "discarded_on_reconnect_by_class": {"ticker": 7},
        "handler_exceptions_total": 3, "handler_exceptions_by_class": {"trade": 3},
        "handler_timeouts_total": 1, "handler_timeouts_by_class": {"trade": 1},
        "queue": {"depth": 12, "capacity": 20000, "high_water": 900, "oldest_message_age_sec": 0.75},
        "queue_wait": {
            "last_sec": 0.2,
            "lifetime": {"count": 496, "max_sec": 9.0, "avg_sec": 0.3},
            "window": {"count": 100, "max_sec": 2.5, "avg_sec": 0.4, "p95_upper_bound_sec": 1.0},
            "buckets": {"le_1ms": 10, "le_10ms": 40, "le_100ms": 30, "le_1s": 15, "le_10s": 5, "gt_10s": 0},
        },
        "handler_time_by_class": {
            "trade": {"window": {"count": 96, "avg_ms": 2.5, "max_ms": 40.0},
                      "lifetime": {"count": 476, "avg_ms": 2.1, "max_ms": 793.0}},
            "ticker": {"window": {"count": 4, "avg_ms": 0.5, "max_ms": 0.9},
                       "lifetime": {"count": 20, "avg_ms": 0.4, "max_ms": 1.0}},
        },
        "server_errors": {"total": 2, "by_code": {"25": 1, "6": 1}, "last": None},
        "error_25_total": 1, "error_25_window": 1,
        "connection": {"connects": 3, "reconnects": 2, "last_disconnect": None},
    }


def _fake_stream_with_ingest(**kwargs):
    stream = _fake_stream(**kwargs)
    stream.ingest_metrics = lambda: _fake_ingest_metrics()
    stream.reset_calls = 0

    def reset_ingest_window():
        stream.reset_calls += 1

    stream.reset_ingest_window = reset_ingest_window
    return stream


def test_capture_from_runtime_flattens_ws_ingest_metrics_under_the_stream_prefix():
    trade_stream = _fake_stream_with_ingest(dropped_messages=4, messages_received=500)

    metrics = observability.capture_from_runtime({}, {}, trade_stream, None)

    assert metrics["trade_stream.ingest.received.trade"] == 480.0
    assert metrics["trade_stream.ingest.received.ticker"] == 20.0
    assert metrics["trade_stream.ingest.processed.trade"] == 476.0
    assert metrics["trade_stream.ingest.dropped.trade"] == 4.0
    assert "trade_stream.ingest.dropped.ticker" not in metrics  # zero counts omitted, not fabricated
    assert metrics["trade_stream.ingest.discarded_on_reconnect.ticker"] == 7.0  # #209: a reconnect discard is not a drop
    assert "trade_stream.ingest.discarded_on_reconnect.trade" not in metrics
    assert metrics["trade_stream.ingest.dropped_window"] == 1.0
    assert metrics["trade_stream.ingest.malformed_messages"] == 2.0
    assert metrics["trade_stream.ingest.handler_exceptions"] == 3.0
    assert metrics["trade_stream.ingest.handler_timeouts"] == 1.0
    assert metrics["trade_stream.ingest.queue_depth"] == 12.0
    assert metrics["trade_stream.ingest.queue_high_water"] == 900.0
    assert metrics["trade_stream.ingest.oldest_message_age_sec"] == 0.75
    assert metrics["trade_stream.ingest.queue_wait.window_max_sec"] == 2.5
    assert metrics["trade_stream.ingest.queue_wait.window_avg_sec"] == 0.4
    assert metrics["trade_stream.ingest.queue_wait.window_p95_upper_bound_sec"] == 1.0
    assert metrics["trade_stream.ingest.queue_wait.window_count"] == 100.0
    assert metrics["trade_stream.ingest.queue_wait.bucket.le_10ms"] == 40.0
    assert metrics["trade_stream.ingest.handler.trade.window_avg_ms"] == 2.5
    assert metrics["trade_stream.ingest.handler.trade.window_max_ms"] == 40.0
    assert metrics["trade_stream.ingest.handler.ticker.window_count"] == 4.0
    assert metrics["trade_stream.ingest.server_errors"] == 2.0
    assert metrics["trade_stream.ingest.error_25_window"] == 1.0
    assert metrics["trade_stream.ingest.reconnects"] == 2.0
    # Pre-existing names are unchanged.
    assert metrics["trade_stream.dropped_messages"] == 4.0
    assert metrics["trade_stream.messages_received"] == 500.0


def test_capture_from_runtime_omits_window_latency_metrics_when_the_window_is_empty():
    trade_stream = _fake_stream_with_ingest()
    empty = _fake_ingest_metrics()
    empty["queue_wait"]["window"] = {"count": 0, "max_sec": None, "avg_sec": None, "p95_upper_bound_sec": None}
    empty["handler_time_by_class"]["ticker"]["window"] = {"count": 0, "avg_ms": None, "max_ms": None}
    trade_stream.ingest_metrics = lambda: empty

    metrics = observability.capture_from_runtime({}, {}, trade_stream, None)

    assert metrics["trade_stream.ingest.queue_wait.window_count"] == 0.0
    assert "trade_stream.ingest.queue_wait.window_max_sec" not in metrics
    assert "trade_stream.ingest.queue_wait.window_p95_upper_bound_sec" not in metrics
    assert "trade_stream.ingest.handler.ticker.window_avg_ms" not in metrics
    assert metrics["trade_stream.ingest.handler.trade.window_avg_ms"] == 2.5


def test_capture_from_runtime_still_works_for_streams_without_ingest_metrics():
    metrics = observability.capture_from_runtime({}, {}, _fake_stream(dropped_messages=1, messages_received=9), None)
    assert metrics["trade_stream.dropped_messages"] == 1.0


# --- subscription-set churn (realtime data-plane investigation, new
# hypothesis found 2026-08-26: "the way the websocket subscriptions per
# Market change after every Market discovery scan is also a major
# problem") ------------------------------------------------------------

def test_capture_from_runtime_flattens_subscription_churn_when_nonzero():
    trade_stream = _fake_stream_with_ingest()
    metrics_dict = _fake_ingest_metrics()
    metrics_dict["subscription_churn"] = {
        "syncs_total": 5, "syncs_window": 2,
        "tickers_added_total": 12, "tickers_added_window": 3,
        "tickers_removed_total": 9, "tickers_removed_window": 1,
    }
    trade_stream.ingest_metrics = lambda: metrics_dict

    metrics = observability.capture_from_runtime({}, {}, trade_stream, None)

    assert metrics["trade_stream.ingest.subscription_churn.syncs_window"] == 2.0
    assert metrics["trade_stream.ingest.subscription_churn.tickers_added_window"] == 3.0
    assert metrics["trade_stream.ingest.subscription_churn.tickers_removed_window"] == 1.0


def test_capture_from_runtime_omits_subscription_churn_when_no_syncs_this_window():
    trade_stream = _fake_stream_with_ingest()  # _fake_ingest_metrics() carries no subscription_churn key at all

    metrics = observability.capture_from_runtime({}, {}, trade_stream, None)

    assert "trade_stream.ingest.subscription_churn.syncs_window" not in metrics
    assert "trade_stream.ingest.subscription_churn.tickers_added_window" not in metrics
    assert "trade_stream.ingest.subscription_churn.tickers_removed_window" not in metrics


def test_maybe_capture_resets_ingest_windows_only_after_a_sample_is_persisted():
    cfg = {"observability": {"enabled": True, "sample_interval_sec": 60}}
    trade_stream = _fake_stream_with_ingest()
    index_stream = _fake_stream_with_ingest()
    state = {"observability": {"last_sample_at": time.time()}}

    observability.maybe_capture(cfg, state, trade_stream, index_stream)  # within interval - no sample
    assert trade_stream.reset_calls == 0 and index_stream.reset_calls == 0

    state["observability"]["last_sample_at"] = time.time() - 999
    observability.maybe_capture(cfg, state, trade_stream, index_stream)  # sample persisted
    assert trade_stream.reset_calls == 1 and index_stream.reset_calls == 1
    assert observability.history("trade_stream.ingest.queue_depth", since_ts=0)[0]["value"] == 12.0


def test_server_error_25_finding_warns_when_kalshi_reported_subscription_overflow_this_window():
    trade_stream = _fake_stream_with_ingest()
    findings = observability.runtime_findings(_POLL_CFG, {}, trade_stream, None)
    ids = {f.finding_id for f in findings}
    assert "observability:ws-server-error-25:trade_stream" in ids
    finding = next(f for f in findings if f.finding_id == "observability:ws-server-error-25:trade_stream")
    assert finding.severity == "warning"
    assert finding.evidence["error_25_window"] == 1


def test_server_error_25_finding_absent_when_no_overflow_reported_or_no_ingest_metrics():
    quiet = _fake_stream_with_ingest()
    im = _fake_ingest_metrics()
    im["error_25_window"] = 0
    quiet.ingest_metrics = lambda: im
    ids = {f.finding_id for f in observability.runtime_findings(_POLL_CFG, {}, quiet, _fake_stream())}
    assert not any("ws-server-error-25" in i for i in ids)


# --- whale pipeline stage timing (realtime data-plane task I2) -------------

def test_capture_from_runtime_flattens_whale_pipeline_perf(monkeypatch):
    from services import whale_pipeline_perf as wpp
    fresh = wpp.WhalePipelinePerf()
    monkeypatch.setattr(wpp, "perf", fresh)
    fresh.record_stage("provider", 0.004)
    fresh.record_stage("provider", 0.010)
    fresh.record_stage("receive_to_decision", 0.3)
    fresh.record_count("trades", 40)
    fresh.record_count("candidates", 2)

    metrics = observability.capture_from_runtime({}, {}, None, None)

    assert metrics["whale_pipeline.stage.provider.window_count"] == 2.0
    assert metrics["whale_pipeline.stage.provider.window_avg_ms"] == 7.0
    assert metrics["whale_pipeline.stage.provider.window_max_ms"] == 10.0
    assert metrics["whale_pipeline.stage.capture.window_count"] == 0.0
    assert "whale_pipeline.stage.capture.window_avg_ms" not in metrics
    assert metrics["whale_pipeline.counter.trades"] == 40.0
    assert metrics["whale_pipeline.counter.candidates"] == 2.0
    assert metrics["whale_pipeline.receive_to_decision.window_p95_upper_bound_sec"] == 1.0
    assert metrics["whale_pipeline.receive_to_decision.bucket.le_1s"] == 1.0


def test_maybe_capture_resets_the_whale_pipeline_window_after_persisting(monkeypatch):
    from services import whale_pipeline_perf as wpp
    fresh = wpp.WhalePipelinePerf()
    monkeypatch.setattr(wpp, "perf", fresh)
    fresh.record_count("trades", 5)
    state = {"observability": {"last_sample_at": time.time() - 999}}

    observability.maybe_capture({"observability": {"enabled": True, "sample_interval_sec": 60}}, state, None, None)

    assert observability.history("whale_pipeline.counter.trades", since_ts=0)[0]["value"] == 5.0
    assert fresh.snapshot()["counters"]["window"]["trades"] == 0
    assert fresh.snapshot()["counters"]["lifetime"]["trades"] == 5


# --- REST latency by caller class (realtime data-plane task I5) ------------

def _fake_rest_latency():
    def agg(count, avg, mx):
        return {"window": {"count": count, "avg_ms": avg, "max_ms": mx},
                "lifetime": {"count": count, "avg_ms": avg, "max_ms": mx}}
    return {
        "by_class": {
            "critical_whale": {
                "calls": 300, "attempts": 400, "rate_limited": 100, "errors": 7,  # lifetime
                "window": {"calls": 3, "attempts": 4, "rate_limited": 1, "errors": 0},
                "limiter_wait": agg(4, 120.0, 300.0), "network": agg(4, 80.0, 200.0),
                "backoff": agg(1, 500.0, 500.0), "total": agg(3, 400.0, 900.0),
            },
            "background_catalog": {
                "calls": 1000, "attempts": 1000, "rate_limited": 0, "errors": 100,
                "window": {"calls": 10, "attempts": 10, "rate_limited": 0, "errors": 1},
                "limiter_wait": agg(10, 900.0, 2500.0), "network": agg(10, 60.0, 90.0),
                "backoff": agg(0, None, None), "total": agg(10, 960.0, 2600.0),
            },
        },
        "by_endpoint": {"get_markets": {"calls": 9, "rate_limited": 1, "errors": 0},
                        "get_milestones": {"calls": 4, "rate_limited": 0, "errors": 1}},
        "limiter": {
            "read": {"waiters": 2, "waiters_high_water": 7, "tokens": 0.5},
            "write": {"waiters": 0, "waiters_high_water": 0, "tokens": 1.0},
        },
    }


def test_capture_from_runtime_flattens_rest_latency_by_caller_class(monkeypatch):
    monkeypatch.setattr(observability.http_client, "rest_latency_snapshot", _fake_rest_latency)

    metrics = observability.capture_from_runtime({}, {}, None, None)

    # Window counts (summable across persisted samples), never the lifetime ones.
    assert metrics["kalshi_rest_class.critical_whale.calls"] == 3.0
    assert metrics["kalshi_rest_class.critical_whale.attempts"] == 4.0
    assert metrics["kalshi_rest_class.critical_whale.rate_limited"] == 1.0
    assert metrics["kalshi_rest_class.critical_whale.errors"] == 0.0
    assert metrics["kalshi_rest_endpoint.get_markets.calls"] == 9.0
    assert metrics["kalshi_rest_endpoint.get_markets.rate_limited"] == 1.0
    assert metrics["kalshi_rest_endpoint.get_milestones.errors"] == 1.0
    assert metrics["kalshi_rest_class.critical_whale.limiter_wait.window_avg_ms"] == 120.0
    assert metrics["kalshi_rest_class.critical_whale.limiter_wait.window_max_ms"] == 300.0
    assert metrics["kalshi_rest_class.critical_whale.network.window_avg_ms"] == 80.0
    assert metrics["kalshi_rest_class.critical_whale.backoff.window_avg_ms"] == 500.0
    assert metrics["kalshi_rest_class.critical_whale.total.window_max_ms"] == 900.0
    assert metrics["kalshi_rest_class.background_catalog.limiter_wait.window_max_ms"] == 2500.0
    assert "kalshi_rest_class.background_catalog.backoff.window_avg_ms" not in metrics  # empty window omitted
    assert metrics["kalshi_rest_limiter.read.waiters"] == 2.0
    assert metrics["kalshi_rest_limiter.read.waiters_high_water"] == 7.0
    assert metrics["kalshi_rest_limiter.write.waiters_high_water"] == 0.0


def test_capture_from_runtime_omits_rest_latency_when_nothing_has_called_yet(monkeypatch):
    monkeypatch.setattr(observability.http_client, "rest_latency_snapshot",
                        lambda: {"by_class": {}, "limiter": _fake_rest_latency()["limiter"]})
    metrics = observability.capture_from_runtime({}, {}, None, None)
    assert not any(k.startswith("kalshi_rest_class.") or k.startswith("kalshi_rest_limiter.") for k in metrics)


def test_maybe_capture_resets_the_rest_latency_window_after_persisting(monkeypatch):
    monkeypatch.setattr(observability.http_client, "rest_latency_snapshot", _fake_rest_latency)
    resets = []
    monkeypatch.setattr(observability.http_client, "reset_rest_latency_window", lambda: resets.append(1))
    state = {"observability": {"last_sample_at": time.time() - 999}}

    observability.maybe_capture({"observability": {"enabled": True, "sample_interval_sec": 60}}, state, None, None)

    assert resets == [1]
    assert observability.history("kalshi_rest_class.critical_whale.calls", since_ts=0)[0]["value"] == 3.0


def test_loop_watchdog_metrics_flow_into_the_snapshot():
    from services import loop_watchdog
    loop_watchdog.reset_window()
    # simulate a stall having been recorded without running the real task
    loop_watchdog._stall_max_ms, loop_watchdog._stall_count, loop_watchdog._samples = 300.0, 1, 10
    try:
        metrics = observability.capture_from_runtime({}, {}, None, None)
        assert metrics["loop_watchdog.stall_max_ms"] == 300.0
        assert metrics["loop_watchdog.stall_count"] == 1.0
        assert metrics["loop_watchdog.samples"] == 10.0
    finally:
        loop_watchdog.reset_window()


def test_loop_watchdog_metrics_omitted_when_never_sampled():
    from services import loop_watchdog
    loop_watchdog.reset_window()
    metrics = observability.capture_from_runtime({}, {}, None, None)
    assert not any(k.startswith("loop_watchdog.") for k in metrics)


def test_maybe_capture_resets_the_loop_watchdog_window_after_persisting(monkeypatch):
    from services import loop_watchdog
    monkeypatch.setattr(observability.http_client, "rest_latency_snapshot", _fake_rest_latency)
    monkeypatch.setattr(observability.http_client, "reset_rest_latency_window", lambda: None)
    resets = []
    monkeypatch.setattr(loop_watchdog, "reset_window", lambda: resets.append(1))
    state = {"observability": {"last_sample_at": time.time() - 999}}

    observability.maybe_capture({"observability": {"enabled": True, "sample_interval_sec": 60}}, state, None, None)

    assert resets == [1]


def test_candidate_retry_metrics_flow_into_the_snapshot(monkeypatch):
    from services import candidate_retry
    monkeypatch.setattr(candidate_retry, "_pending", {"t1": {}})
    monkeypatch.setattr(candidate_retry, "_window_retried", 3)
    monkeypatch.setattr(candidate_retry, "_window_recovered", 2)
    monkeypatch.setattr(candidate_retry, "_window_abandoned", 1)

    metrics = observability.capture_from_runtime({}, {}, None, None)

    assert metrics["candidate_retry.pending"] == 1.0
    assert metrics["candidate_retry.retried"] == 3.0
    assert metrics["candidate_retry.recovered"] == 2.0
    assert metrics["candidate_retry.abandoned"] == 1.0


def test_candidate_retry_metrics_omitted_when_nothing_pending_or_happened(monkeypatch):
    from services import candidate_retry
    monkeypatch.setattr(candidate_retry, "_pending", {})
    monkeypatch.setattr(candidate_retry, "_window_retried", 0)
    monkeypatch.setattr(candidate_retry, "_window_recovered", 0)
    monkeypatch.setattr(candidate_retry, "_window_abandoned", 0)

    metrics = observability.capture_from_runtime({}, {}, None, None)

    assert not any(k.startswith("candidate_retry.") for k in metrics)  # same "no evidence, no rows" contract


def test_candidate_retry_metrics_present_when_something_is_pending_even_with_zero_window_activity(monkeypatch):
    from services import candidate_retry
    monkeypatch.setattr(candidate_retry, "_pending", {"t1": {}})
    monkeypatch.setattr(candidate_retry, "_window_retried", 0)
    monkeypatch.setattr(candidate_retry, "_window_recovered", 0)
    monkeypatch.setattr(candidate_retry, "_window_abandoned", 0)

    metrics = observability.capture_from_runtime({}, {}, None, None)

    assert metrics["candidate_retry.pending"] == 1.0
    assert metrics["candidate_retry.retried"] == 0.0


def test_maybe_capture_resets_the_candidate_retry_window_after_persisting(monkeypatch):
    from services import candidate_retry
    monkeypatch.setattr(observability.http_client, "rest_latency_snapshot", _fake_rest_latency)
    monkeypatch.setattr(observability.http_client, "reset_rest_latency_window", lambda: None)
    resets = []
    monkeypatch.setattr(candidate_retry, "reset_window", lambda: resets.append(1))
    state = {"observability": {"last_sample_at": time.time() - 999}}

    observability.maybe_capture({"observability": {"enabled": True, "sample_interval_sec": 60}}, state, None, None)

    assert resets == [1]


# --- capture writer thread metrics/finding (realtime data-plane remediation P3 Task 14) ---

class _FakeAliveThread:
    def is_alive(self) -> bool:
        return True


class _FakeDeadThread:
    def is_alive(self) -> bool:
        return False


def test_capture_writer_metrics_flow_into_the_snapshot_when_alive(monkeypatch):
    from services import capture_writer
    monkeypatch.setattr(capture_writer, "_thread", _FakeAliveThread())
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [("row",)]})
    monkeypatch.setattr(capture_writer, "_last_flush_at", {"raw_trades": time.time() - 0.25})

    metrics = observability.capture_from_runtime({}, {}, None, None)

    assert metrics["writer.depth.raw_trades"] == 1.0
    assert 200 <= metrics["writer.last_flush_age_ms.raw_trades"] <= 400


def test_capture_writer_metrics_omitted_when_never_started(monkeypatch):
    from services import capture_writer
    monkeypatch.setattr(capture_writer, "_thread", None)

    metrics = observability.capture_from_runtime({}, {}, None, None)

    assert not [k for k in metrics if k.startswith("writer.")]


def test_capture_writer_dead_finding_absent_when_never_started(monkeypatch):
    from services import capture_writer
    monkeypatch.setattr(capture_writer, "_thread", None)

    findings = observability.runtime_findings(_POLL_CFG, {}, None, None)

    assert not [f for f in findings if f.check == "capture-writer-alive"]


def test_capture_writer_dead_finding_absent_when_alive(monkeypatch):
    from services import capture_writer
    monkeypatch.setattr(capture_writer, "_thread", _FakeAliveThread())

    findings = observability.runtime_findings(_POLL_CFG, {}, None, None)

    assert not [f for f in findings if f.check == "capture-writer-alive"]


def test_capture_writer_dead_finding_critical_when_started_but_not_alive(monkeypatch):
    from services import capture_writer
    monkeypatch.setattr(capture_writer, "_thread", _FakeDeadThread())
    monkeypatch.setattr(capture_writer, "_buffers", {"raw_trades": [("row",), ("row2",)]})

    findings = observability.runtime_findings(_POLL_CFG, {}, None, None)

    matches = [f for f in findings if f.check == "capture-writer-alive"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    assert matches[0].evidence == {"depth": {"raw_trades": 2}}


# --- P8 Task 34: reconnect gap duration + per-position ticker cadence ------

def test_capture_from_runtime_flattens_reconnect_gap_when_one_completed_this_window():
    trade_stream = _fake_stream_with_ingest()
    metrics_dict = _fake_ingest_metrics()
    metrics_dict["connection"] = {
        "connects": 2, "reconnects": 1,
        "last_disconnect": {"reason": "x", "at": 100.0},
        "last_gap_sec": 7.5, "gap_sec_window": 7.5,
    }
    trade_stream.ingest_metrics = lambda: metrics_dict

    metrics = observability.capture_from_runtime({}, {}, trade_stream, None)

    assert metrics["trade_stream.ingest.last_gap_sec"] == 7.5


def test_capture_from_runtime_omits_reconnect_gap_when_none_this_window():
    trade_stream = _fake_stream_with_ingest()
    metrics_dict = _fake_ingest_metrics()
    metrics_dict["connection"] = {
        "connects": 2, "reconnects": 1,
        "last_disconnect": {"reason": "x", "at": 100.0},
        "last_gap_sec": 7.5, "gap_sec_window": None,  # window already consumed
    }
    trade_stream.ingest_metrics = lambda: metrics_dict

    metrics = observability.capture_from_runtime({}, {}, trade_stream, None)

    assert "trade_stream.ingest.last_gap_sec" not in metrics


def test_capture_from_runtime_flattens_open_position_ticker_cadence():
    now = 1000.0
    state = {
        "open_position_tickers": {"K1", "K2", "K3"},
        "open_position_ticker_seen_at": {"K1": 990.0, "K2": 940.0, "GONE": 100.0},
    }

    metrics = observability.capture_from_runtime({}, state, None, None, now=now)

    # GONE is not an open position - excluded, not counted
    assert metrics["position_ticker.tracked_count"] == 2.0
    assert metrics["position_ticker.oldest_update_age_sec"] == 60.0
    assert metrics["position_ticker.newest_update_age_sec"] == 10.0
    # K3 is open but has never received a ticker message - counted separately,
    # never fabricated as an age of "now - 0"
    assert metrics["position_ticker.never_seen_count"] == 1.0


def test_capture_from_runtime_omits_position_ticker_cadence_with_no_positions():
    metrics = observability.capture_from_runtime({}, {"open_position_tickers": set()}, None, None, now=1000.0)
    assert not any(k.startswith("position_ticker.") for k in metrics)


def test_maybe_capture_prunes_closed_positions_from_ticker_cadence_after_persisting():
    cfg = {"observability": {"enabled": True, "sample_interval_sec": 60}}
    state = {
        "observability": {"last_sample_at": time.time() - 999},
        "open_position_tickers": {"K1"},
        "open_position_ticker_seen_at": {"K1": time.time() - 5, "CLOSED": time.time() - 500},
    }

    observability.maybe_capture(cfg, state, None, None)

    assert set(state["open_position_ticker_seen_at"]) == {"K1"}  # CLOSED pruned, K1 kept
