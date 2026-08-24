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
own shape on purpose: services/backup/backup.py's CHEATSHEET.md documents a
real live bug (2026-08-23) where trusting in-memory-only last-run state made
every uvicorn --reload cycle fire an immediate, unnecessary re-run. Task 9's
own plan explicitly calls out the same restart-safety requirement for
observability sampling, so it gets the same regression coverage up front
instead of waiting to rediscover the bug live.
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
