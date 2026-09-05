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
        "_maybe_resolve_event_schedules(", "_maybe_scan_catalog_batch(", "_maybe_scan_mve_batch(",
        "calibration_history.due(", "advisory_engine.generate_recommendations(",
    ):
        assert marker not in source, f"{marker} still lives inside trading_loop"
    assert "_scheduler_loop" in inspect.getsource(main.lifespan)
    assert [name for name, _ in main._SCHEDULER_TRIGGERS] == [
        "signal_resolution", "backup", "backup_large", "research", "event_schedule", "catalog_scan",
        "mve_scan", "milestone_scan", "auto_apply",
    ]


def test_backup_large_trigger_is_registered_in_scheduler_triggers():
    from main import _SCHEDULER_TRIGGERS
    from services.backup import _maybe_run_large_backup

    names_to_triggers = dict(_SCHEDULER_TRIGGERS)
    assert names_to_triggers["backup_large"] is _maybe_run_large_backup


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
    # main no longer owns a whale_provider name of its own (#565) - it resolves
    # the one live instance through app_state, same as every other path.
    assert provider is main.get_whale_provider() and handle_signal is main._handle_signal
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


# --- _ticker_flush_loop (issue #576) -----------------------------------
# The drain mechanism itself (batch cap, interleaving safety) is unit-tested
# directly against services/kalshi/websocket.py's flush_pending_tickers();
# this file only checks the small loop that polls it on a timer and that
# main.lifespan actually starts one, scoped to trade_stream only.

def test_ticker_flush_loop_polls_flush_pending_tickers(monkeypatch):
    calls = []

    class FakeGateway:
        async def flush_pending_tickers(self):
            calls.append(True)
            return 0

    sleeps = {"n": 0}

    async def fake_sleep(_sec):
        sleeps["n"] += 1
        if sleeps["n"] > 3:
            raise _Stop

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    with pytest.raises(_Stop):
        asyncio.run(main._ticker_flush_loop(FakeGateway()))

    assert len(calls) == 3  # one flush per sleep interval


def test_lifespan_starts_the_ticker_flush_loop_for_trade_stream_only():
    """Scoped deliberately: index_stream never calls set_market_tickers, so
    its own ticker-coalescing map is provably always empty (see
    _ticker_flush_loop's own docstring) - wiring a second, permanently
    no-op instance there would be unjustified complexity, not defense in
    depth."""
    source = inspect.getsource(main.lifespan)
    assert source.count("_ticker_flush_loop") == 1
    assert "trade_stream_ticker_flush_task" in source
    assert "trade_stream_ticker_flush_task.cancel()" in source
    assert "index_stream_ticker_flush" not in source


# --- _index_feed_backfill_loop (issue #260) ---------------------------------
# The gap-detection/REST-fetch logic itself lives in and is unit-tested
# directly against services/index_feed/backfill.py; this file only checks
# the small loop that polls it on a timer, wires the gateway's own
# credentials/base_url into the real fetch function, and that
# main.lifespan actually starts one.

class _FakeIndexGateway:
    def __init__(self, credentials=("key-1", "priv-1"), base_url="https://external-api.kalshi.com/trade-api/v2",
                 index_ids=("BRTI", "ETHUSD_RTI"), connection_metrics=None):
        self._credentials = credentials
        self.base_url = base_url
        self.index_ids = list(index_ids)
        self._connection_metrics = connection_metrics or {"reconnects": 0}

    def signing_credentials(self):
        return self._credentials

    def ingest_metrics(self):
        return {"connection": self._connection_metrics}


def test_index_feed_backfill_loop_skips_entirely_without_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(main.index_feed_backfill, "check_and_backfill", lambda *a, **k: calls.append(True))
    gateway = _FakeIndexGateway(credentials=None)

    _drive(3, lambda: main._index_feed_backfill_loop(gateway), running=True, monkeypatch=monkeypatch)

    assert calls == []


def test_index_feed_backfill_loop_calls_check_and_backfill_with_the_gateways_connection_metrics_and_index_ids(monkeypatch):
    seen = []

    async def fake_check_and_backfill(connection_metrics, fetch_history, index_ids):
        seen.append((connection_metrics, index_ids))
        return []

    monkeypatch.setattr(main.index_feed_backfill, "check_and_backfill", fake_check_and_backfill)
    metrics = {"reconnects": 1, "last_disconnect": {"reason": "x", "at": 1.0}, "last_gap_sec": 2.0}
    gateway = _FakeIndexGateway(connection_metrics=metrics, index_ids=["BRTI"])

    _drive(1, lambda: main._index_feed_backfill_loop(gateway), running=True, monkeypatch=monkeypatch)

    assert seen == [(metrics, ["BRTI"])]


def test_index_feed_backfill_loops_fetch_wrapper_uses_the_gateways_own_credentials_and_base_url(monkeypatch):
    """The fetch_history callable check_and_backfill receives must reach
    the real REST layer with THIS gateway's own signing credentials/base
    URL - not a hardcoded or mismatched one, since index_stream and
    trade_stream each load their own credentials independently."""
    captured_fetch = {}

    async def fake_check_and_backfill(connection_metrics, fetch_history, index_ids):
        captured_fetch["fn"] = fetch_history
        return []

    fetch_calls = []

    async def fake_fetch_cfbenchmarks_history(index_id, start_ts, end_ts, *, key_id, private_key, base_url):
        fetch_calls.append({"index_id": index_id, "start_ts": start_ts, "end_ts": end_ts,
                             "key_id": key_id, "private_key": private_key, "base_url": base_url})
        return []

    monkeypatch.setattr(main.index_feed_backfill, "check_and_backfill", fake_check_and_backfill)
    monkeypatch.setattr(main.index_feed_backfill, "fetch_cfbenchmarks_history", fake_fetch_cfbenchmarks_history)
    gateway = _FakeIndexGateway(credentials=("key-9", "priv-9"), base_url="https://demo.example/trade-api/v2")

    _drive(1, lambda: main._index_feed_backfill_loop(gateway), running=True, monkeypatch=monkeypatch)
    asyncio.run(captured_fetch["fn"]("BRTI", 10.0, 20.0))

    assert fetch_calls == [{"index_id": "BRTI", "start_ts": 10.0, "end_ts": 20.0,
                            "key_id": "key-9", "private_key": "priv-9", "base_url": "https://demo.example/trade-api/v2"}]


def test_lifespan_starts_the_index_feed_backfill_loop():
    source = inspect.getsource(main.lifespan)
    assert "_index_feed_backfill_loop" in source
    assert "index_feed_backfill_task" in source
    assert "index_feed_backfill_task.cancel()" in source
    # Scoped to index_ids (CF Benchmarks), not underlying_tickers (Pyth) -
    # there is no documented REST passthrough to backfill Pyth from.
    assert "index_stream.index_ids" in source


# --- _settlement_resolver_loop (P4 Tasks 19+24) ----------------------------
# Mirrors _candidate_retry_loop's shape: own supervised loop, own client per
# run, idle path is one snapshot() read. No streaming gate on purpose - the
# pending dict only fills from the WS settled handler, but items already
# enqueued must still drain if streaming is toggled off before they resolve.

def _wire_settlement_resolver(monkeypatch, pending: int):
    calls, closed, constructed = [], [], []

    class FakeClient:
        def __init__(self, base_url, timeout):
            constructed.append(base_url)

        async def close(self):
            closed.append(True)

    async def fake_run_pending(client, **_kw):
        calls.append(client)
        return {"resolved": 1, "still_pending": 0, "resolved_rows": 3}

    monkeypatch.setattr(main, "KalshiPublicGateway", FakeClient)
    monkeypatch.setattr(main.settlement_resolver, "run_pending", fake_run_pending)
    monkeypatch.setattr(main.settlement_resolver, "snapshot", lambda: {"pending": pending})
    monkeypatch.setattr(main.config_store, "get", lambda: {
        "kalshi": {"base_url": "u", "request_timeout_sec": 1},
    })
    monkeypatch.setitem(main.state, "settlement_resolver_loop", {"running": False, "last_started_at": 0.0})
    monkeypatch.setitem(main.state, "lifecycle_stream_stats", {
        "events_by_type": {}, "close_time_updates_applied": 0, "last_event_at": None,
        "catalog_updates_applied": 0, "outcomes_resolved_via_lifecycle": 0,
    })
    return calls, closed, constructed, FakeClient


def test_settlement_resolver_loop_drains_and_feeds_the_lifecycle_stat(monkeypatch):
    calls, closed, constructed, FakeClient = _wire_settlement_resolver(monkeypatch, pending=2)

    _drive(2, main._settlement_resolver_loop, running=True, monkeypatch=monkeypatch)

    assert len(calls) == 2 and len(closed) == 2 and len(constructed) == 2  # one client per run, always closed
    assert isinstance(calls[0], FakeClient)
    # resolved_rows (store rows, the counter's historical meaning) reaches
    # the same stat the inline settled branch used to increment.
    assert main.state["lifecycle_stream_stats"]["outcomes_resolved_via_lifecycle"] == 6
    assert main.state["settlement_resolver_loop"]["last_started_at"] > 0


def test_settlement_resolver_loop_skips_entirely_when_nothing_is_pending(monkeypatch):
    calls, closed, constructed, _ = _wire_settlement_resolver(monkeypatch, pending=0)

    _drive(3, main._settlement_resolver_loop, running=True, monkeypatch=monkeypatch)

    assert calls == [] and constructed == []  # no client churn on the idle path


def test_settlement_resolver_loop_respects_pause(monkeypatch):
    calls, *_ = _wire_settlement_resolver(monkeypatch, pending=2)

    _drive(2, main._settlement_resolver_loop, running=False, monkeypatch=monkeypatch)

    assert calls == []


def test_settlement_resolver_loop_is_supervised_from_lifespan():
    source = inspect.getsource(main.lifespan)
    assert "_settlement_resolver_loop" in source


# --- #214: auto-apply refuses to write on a known completeness defect -----

def _wire_calibration_auto_apply(monkeypatch, degraded: bool):
    monkeypatch.setattr(main.calibration_history, "due", lambda *a, **k: True)
    monkeypatch.setattr(main.calibration_history, "record_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(main.signal_log, "resolved_signals_with_factors", lambda: [])
    monkeypatch.setattr(main.confidence_calibration, "generate_calibration_report", lambda rows, min_n, weights: {
        "report": {
            "resolved_count": 200, "suggested_weights": {"depth_factor": 0.6},
            "ranked_by_discrimination": ["depth_factor"],
            "per_factor": [{"factor": "depth_factor", "gap_pts": 12.0}],
        },
    })
    monkeypatch.setattr(main.confidence_calibration, "blended_weights_for_auto_apply",
                         lambda current, suggested: {"depth_factor": 0.6})
    monkeypatch.setattr(main.config_performance, "last_applied_at", lambda source: None)
    monkeypatch.setattr(main.config_performance, "fingerprint", lambda cfg: "fp")
    logged = []
    monkeypatch.setattr(main.config_performance, "log_applied_change", lambda **kw: logged.append(kw))
    updates = []
    monkeypatch.setattr(main.config_store, "update", lambda patch: updates.append(patch))
    monkeypatch.setattr(main.config_store, "get", lambda: {})
    bumps = []
    monkeypatch.setattr(main, "bump_generation", lambda: bumps.append(True))
    monkeypatch.setattr(main.evidence_provenance, "current_completeness_state",
                         lambda: {"degraded": degraded, "defects": [], "checked_at": 0.0})
    return updates, logged, bumps


_CALIBRATION_CFG = {
    "confidence_calibration": {
        "enabled": True, "auto_apply_enabled": True, "min_resolved_signals": 7,
        "auto_apply_min_resolved_signals": 150, "auto_apply_cooldown_sec": 86400,
    },
    "advisory": {"enabled": False},
    "whale_confidence_weights": {"depth_factor": 0.5},
}


def test_calibration_auto_apply_writes_new_weights_when_evidence_is_clean(monkeypatch):
    updates, logged, bumps = _wire_calibration_auto_apply(monkeypatch, degraded=False)

    main._maybe_run_auto_apply(_CALIBRATION_CFG)

    assert updates == [{"whale_confidence_weights": {"depth_factor": 0.6}}]
    assert len(logged) == 1 and logged[0]["auto_applied"] is True
    assert bumps == [True]


def test_calibration_auto_apply_refuses_to_write_when_evidence_is_degraded(monkeypatch):
    updates, logged, bumps = _wire_calibration_auto_apply(monkeypatch, degraded=True)

    main._maybe_run_auto_apply(_CALIBRATION_CFG)

    assert updates == []  # known completeness defect open - refuse the automatic write
    assert logged == []
    assert bumps == []


def _wire_advisory_auto_apply(monkeypatch, degraded: bool):
    qualifying_rec = {
        "id": "rec-1", "config_path": "strategy.entry_threshold",
        "current_value": 0.5, "suggested_value": 0.6, "rationale": "test recommendation",
        "confidence_label": "higher", "n": 100,
    }
    monkeypatch.setattr(main.advisory_engine, "generate_recommendations",
                         lambda *a, **k: {"recommendations": [qualifying_rec]})
    monkeypatch.setattr(main, "_series_evaluator_rows_for_advisory", lambda cfg: [])
    monkeypatch.setattr(main.regime_analytics, "by_category", lambda rows: [])
    monkeypatch.setattr(main.candidate_log, "gate_summary", lambda: {})
    monkeypatch.setattr(main.config_performance, "last_applied_at", lambda source: None)
    monkeypatch.setattr(main.config_performance, "fingerprint", lambda cfg: "fp")
    monkeypatch.setattr(main.config_performance, "all_last_applied_by_path", lambda: {})
    # all_variants() would otherwise do a real (if test-isolated) DB read
    # this test has no reason to depend on - generate_recommendations
    # itself is mocked below and never inspects its `variants` argument.
    monkeypatch.setattr(main.config_performance, "all_variants", lambda: [])
    monkeypatch.setattr(main.trade_analytics, "build_trade_history", lambda rows: [])
    logged = []
    monkeypatch.setattr(main.config_performance, "log_applied_change", lambda **kw: logged.append(kw))
    updates = []
    monkeypatch.setattr(main.config_store, "update", lambda patch: updates.append(patch))
    monkeypatch.setattr(main.config_store, "get", lambda: {"strategy": {"entry_threshold": 0.5}})
    bumps = []
    monkeypatch.setattr(main, "bump_generation", lambda: bumps.append(True))
    monkeypatch.setattr(main.evidence_provenance, "current_completeness_state",
                         lambda: {"degraded": degraded, "defects": [], "checked_at": 0.0})
    return updates, logged, bumps


_ADVISORY_CFG = {
    "advisory": {
        "enabled": True, "auto_apply_enabled": True,
        # min_resolved_trades_per_variant is read via adv_cfg["..."] (bracket
        # indexing, not .get()) when building the generate_recommendations
        # call - omitting it here raises KeyError before the mocked
        # generate_recommendations ever runs.
        "min_resolved_trades_per_variant": 10,
        "auto_apply_min_confidence": "higher", "auto_apply_min_n": 25, "auto_apply_cooldown_sec": 86400,
    },
    "confidence_calibration": {"enabled": False},
}


def test_advisory_auto_apply_writes_when_evidence_is_clean(monkeypatch):
    updates, logged, bumps = _wire_advisory_auto_apply(monkeypatch, degraded=False)

    main._maybe_run_auto_apply(_ADVISORY_CFG)

    assert updates == [{"strategy": {"entry_threshold": 0.6}}]
    assert len(logged) == 1 and logged[0]["auto_applied"] is True
    assert bumps == [True]


def test_advisory_auto_apply_refuses_to_write_when_evidence_is_degraded(monkeypatch):
    updates, logged, bumps = _wire_advisory_auto_apply(monkeypatch, degraded=True)

    main._maybe_run_auto_apply(_ADVISORY_CFG)

    assert updates == []  # known completeness defect open - refuse the automatic write
    assert logged == []
    assert bumps == []


def test_maybe_run_auto_apply_passes_declined_ids(monkeypatch):
    """Task 3a of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md: this is the ONE unsupervised generate_recommendations()
    call site (no human in the loop between a suggestion and it being
    applied) - per advisory_engine.py's own declined_ids docstring
    (":926-929", "a suggestion a human already clicked 'no thanks' on
    doesn't come back with the exact same evidence behind it"), this is
    exactly the path that most needs to honor a decline, and previously
    didn't."""
    calls = []
    monkeypatch.setattr(main.advisory_engine, "generate_recommendations",
                         lambda *a, **k: calls.append(k) or {"recommendations": []})
    monkeypatch.setattr(main, "_series_evaluator_rows_for_advisory", lambda cfg: [])
    monkeypatch.setattr(main.regime_analytics, "by_category", lambda rows: [])
    monkeypatch.setattr(main.candidate_log, "gate_summary", lambda: {})
    monkeypatch.setattr(main.config_performance, "last_applied_at", lambda source: None)
    monkeypatch.setattr(main.config_performance, "fingerprint", lambda cfg: "fp")
    monkeypatch.setattr(main.config_performance, "all_last_applied_by_path", lambda: {})
    monkeypatch.setattr(main.config_performance, "all_variants", lambda: [])
    monkeypatch.setattr(main.trade_analytics, "build_trade_history", lambda rows: [])
    monkeypatch.setattr(main.evidence_provenance, "current_completeness_state",
                         lambda: {"degraded": False, "defects": [], "checked_at": 0.0})
    monkeypatch.setattr(main.suggestion_decisions, "declined_ids", lambda: {"decl-1", "decl-2"})

    main._maybe_run_auto_apply(_ADVISORY_CFG)

    assert len(calls) == 1
    assert calls[0].get("declined_ids") == {"decl-1", "decl-2"}
