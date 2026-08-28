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
