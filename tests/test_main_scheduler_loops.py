"""P8 Task 36: the five _maybe_* trigger checks plus the calibration/advisory
auto-apply blocks run from their own supervised loops, not from inside
trading_loop's body. Each trigger's own due()/overlap guard is untouched -
only the caller moved. trading_loop() is a giant network-driven while-True
nobody can drive in a test, so the relocation is checked structurally on the
real source (the idiom tests/test_main_tick_executor_wiring.py already uses)
plus real unit coverage of the new loop and the extracted auto-apply."""
import asyncio
import inspect

import pytest

import main


class _Stop(Exception):
    pass


def _run_loop_for(n_sleeps: int, trigger, running: bool, monkeypatch):
    sleeps = {"n": 0}

    async def fake_sleep(_sec):
        sleeps["n"] += 1
        if sleeps["n"] > n_sleeps:
            raise _Stop

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.state["running"] = running
    with pytest.raises(_Stop):
        asyncio.run(main._scheduler_loop(trigger, "unit"))


def test_scheduler_loop_invokes_the_trigger_with_live_config_each_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(main.config_store, "get", lambda: {"marker": 1})

    _run_loop_for(3, lambda cfg: calls.append(cfg), running=True, monkeypatch=monkeypatch)

    assert calls == [{"marker": 1}] * 3  # one call per interval, always the live cfg


def test_scheduler_loop_does_not_fire_while_the_app_is_paused(monkeypatch):
    calls = []

    _run_loop_for(3, lambda cfg: calls.append(cfg), running=False, monkeypatch=monkeypatch)

    assert calls == []  # trading_loop's own pause semantics, preserved


def test_trading_loop_no_longer_hosts_the_relocated_trigger_calls():
    source = inspect.getsource(main.trading_loop)
    for marker in (
        "_maybe_check_signal_resolutions(", "_maybe_run_backup(", "_maybe_run_research(",
        "_maybe_resolve_event_schedules(", "_maybe_scan_catalog_batch(",
        "calibration_history.due(", "advisory_engine.generate_recommendations(",
    ):
        assert marker not in source, f"{marker} still lives inside trading_loop"
    assert "_scheduler_loop" in inspect.getsource(main.lifespan)
    assert [name for name, _ in main._SCHEDULER_TRIGGERS] == [
        "signal_resolution", "backup", "research", "event_schedule", "catalog_scan", "auto_apply",
    ]


def test_maybe_run_auto_apply_is_a_noop_when_both_features_are_off(monkeypatch):
    monkeypatch.setattr(main.calibration_history, "due", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be consulted")))
    monkeypatch.setattr(main.advisory_engine, "generate_recommendations", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))

    main._maybe_run_auto_apply({"confidence_calibration": {"enabled": False}, "advisory": {"enabled": False}})


def test_maybe_run_auto_apply_reaches_the_moved_calibration_block_when_due(monkeypatch):
    reached = {}
    monkeypatch.setattr(main.calibration_history, "due", lambda *a, **k: True)
    monkeypatch.setattr(main.signal_log, "resolved_signals_with_factors", lambda: [])
    def fake_report(rows, min_n, weights):
        reached["called"] = (rows, min_n)
        return {"report": None}
    monkeypatch.setattr(main.confidence_calibration, "generate_calibration_report", fake_report)

    main._maybe_run_auto_apply({
        "confidence_calibration": {"enabled": True, "min_resolved_signals": 7},
        "advisory": {"enabled": False},
    })

    assert reached["called"] == ([], 7)  # the block moved intact and is reachable from its new home


# --- P8 Task 37: candidate_retry.run_pending from its own supervised loop -----

def _drive(n_sleeps: int, coro_fn, running: bool, monkeypatch):
    sleeps = {"n": 0}

    async def fake_sleep(_sec):
        sleeps["n"] += 1
        if sleeps["n"] > n_sleeps:
            raise _Stop

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    main.state["running"] = running
    with pytest.raises(_Stop):
        asyncio.run(coro_fn())


def _wire_candidate_retry(monkeypatch, pending: int, streaming: bool = True):
    calls, closed, constructed = [], [], []

    class FakeClient:
        def __init__(self, base_url, timeout):
            constructed.append(base_url)

        async def close(self):
            closed.append(True)

    async def fake_run_pending(client, provider, handle_signal, cfg, market_results, config_fp, tick_now, **_kw):
        calls.append((client, provider, handle_signal, cfg, market_results, config_fp, tick_now))
        return {}

    monkeypatch.setattr(main, "KalshiPublicGateway", FakeClient)
    monkeypatch.setattr(main.candidate_retry, "run_pending", fake_run_pending)
    monkeypatch.setattr(main.candidate_retry, "snapshot", lambda: {"pending": pending})
    monkeypatch.setattr(main, "_streaming_trade_tape_enabled", lambda: streaming)
    monkeypatch.setattr(main.config_store, "get", lambda: {
        "kalshi": {"base_url": "u", "request_timeout_sec": 1}, "strategy": {"entry_threshold": 0.5},
    })
    monkeypatch.setitem(main.state, "market_results", {"K1": "yes"})
    monkeypatch.setitem(main.state, "candidate_retry_loop", {"running": False, "last_started_at": 0.0})
    return calls, closed, constructed, FakeClient


def test_candidate_retry_loop_runs_run_pending_with_its_own_client_when_work_is_pending(monkeypatch):
    calls, closed, constructed, FakeClient = _wire_candidate_retry(monkeypatch, pending=2)

    _drive(2, main._candidate_retry_loop, running=True, monkeypatch=monkeypatch)

    assert len(calls) == 2 and len(closed) == 2 and len(constructed) == 2  # one client per run, always closed
    client, provider, handle_signal, cfg, market_results, config_fp, tick_now = calls[0]
    assert isinstance(client, FakeClient)
    assert provider is main.whale_provider and handle_signal is main._handle_signal  # scored, not claim-and-dropped
    assert market_results == {"K1": "yes"}
    assert config_fp == main.config_performance.fingerprint(cfg)
    assert main.state["candidate_retry_loop"]["last_started_at"] > 0


def test_candidate_retry_loop_skips_entirely_when_nothing_is_pending(monkeypatch):
    calls, closed, constructed, _ = _wire_candidate_retry(monkeypatch, pending=0)

    _drive(3, main._candidate_retry_loop, running=True, monkeypatch=monkeypatch)

    assert calls == [] and constructed == []  # no client churn on the idle path


def test_candidate_retry_loop_respects_stream_mode_and_pause(monkeypatch):
    calls, *_ = _wire_candidate_retry(monkeypatch, pending=2, streaming=False)
    _drive(2, main._candidate_retry_loop, running=True, monkeypatch=monkeypatch)
    assert calls == []

    calls, *_ = _wire_candidate_retry(monkeypatch, pending=2, streaming=True)
    _drive(2, main._candidate_retry_loop, running=False, monkeypatch=monkeypatch)
    assert calls == []


# --- P8 Task 39: trading_loop's own REST tick slows to a safety-net cadence
# in streaming mode, once check_exits/check_pending_fills/position_netting.
# review/the five schedulers/candidate_retry no longer depend on it as their
# only trigger (Tasks 36-38).

def test_tick_interval_uses_the_safety_net_cadence_when_streaming(monkeypatch):
    monkeypatch.setattr(main, "_streaming_trade_tape_enabled", lambda: True)
    cfg = {"kalshi": {"poll_interval_sec": 6, "safety_net_interval_sec": 30}}

    assert main._tick_interval_sec(cfg) == 30


def test_tick_interval_keeps_poll_interval_sec_when_not_streaming(monkeypatch):
    monkeypatch.setattr(main, "_streaming_trade_tape_enabled", lambda: False)
    cfg = {"kalshi": {"poll_interval_sec": 6, "safety_net_interval_sec": 30}}

    assert main._tick_interval_sec(cfg) == 6  # REST is still the primary path here, unchanged


def test_trading_loop_sleeps_for_the_computed_tick_interval():
    import inspect
    source = inspect.getsource(main.trading_loop)
    assert "await asyncio.sleep(_tick_interval_sec(cfg))" in source
    assert 'await asyncio.sleep(cfg["kalshi"]["poll_interval_sec"])' not in source


# --- consumer-liveness watchdog wiring (issue #145) -------------------------
# The detection/recovery logic itself lives on KalshiStreamGateway and is
# unit-tested directly in tests/test_kalshi_ws_consumer_liveness.py; this
# file only checks the small loop that polls it on a timer and that
# main.lifespan actually starts one per active stream.

def test_stream_consumer_liveness_loop_polls_ensure_consumer_progressing(monkeypatch):
    calls = []

    class FakeGateway:
        async def ensure_consumer_progressing(self):
            calls.append(True)
            return False

    sleeps = {"n": 0}

    async def fake_sleep(_sec):
        sleeps["n"] += 1
        if sleeps["n"] > 3:
            raise _Stop

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        asyncio.run(main._stream_consumer_liveness_loop(FakeGateway()))

    assert len(calls) == 3  # one check per sleep interval


def test_lifespan_starts_a_liveness_loop_for_each_active_stream():
    source = inspect.getsource(main.lifespan)
    assert source.count("_stream_consumer_liveness_loop") == 2  # trade_stream and index_stream
    assert "trade_stream_liveness_task" in source and "index_stream_liveness_task" in source
    assert "trade_stream_liveness_task.cancel()" in source and "index_stream_liveness_task.cancel()" in source
