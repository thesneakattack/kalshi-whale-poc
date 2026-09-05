"""Smoke test for tests/bench_ticker_flush_576.py — the archived issue #576
benchmark artifact (committed per the standing "commit benchmark scripts
somewhere durable" lesson, docs/next-action.md 2026-09-05). Lives under
tests/, not tools/, matching tests/test_kalshi_ws_two_consumers.py's own
precedent for constructing KalshiStreamGateway with a raw host string —
tools/quality_audit's kalshi-boundary-host check is deliberately scoped to
exclude tests/ (fixtures legitimately need the real endpoint string), so a
manual benchmark harness that reuses that same pattern belongs here too.

This is not a re-run of the full benchmark (which seeds millions of
synthetic trades and runs multiple 15s windows — a manual, on-demand
exercise, not a CI-scoped test). It confirms the archived script still
imports and its `run_scenario()`/`calibrate_drain_rate()` functions still
execute against the real `KalshiStreamGateway` API (flush_pending_tickers,
_active_callbacks, ticker_flush_runs/_total, coalesced_tickers) without
error, at a scale small enough to run in under a second — so a future
change to `services/kalshi/websocket.py` that breaks the archived script's
API surface is caught here, rather than only being discovered the next time
someone actually needs to re-run the real benchmark.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_ticker_flush_576 as bench  # noqa: E402


def test_run_scenario_executes_against_the_real_gateway_api():
    result = asyncio.run(bench.run_scenario(
        label="smoke",
        backlog_trades=100,
        n_markets=3,
        ticker_update_interval_sec=0.05,
        run_duration_sec=0.3,
        flush_interval_sec=None,
    ))
    assert result["label"] == "smoke"
    assert result["ticker_flush_runs"] == 0  # no flush task in this scenario
    assert isinstance(result["coalesced_tickers"], int)


def test_run_scenario_with_a_flush_interval_actually_runs_the_flush_loop():
    result = asyncio.run(bench.run_scenario(
        label="smoke_flush",
        backlog_trades=100,
        n_markets=3,
        ticker_update_interval_sec=0.05,
        run_duration_sec=0.3,
        flush_interval_sec=0.05,
    ))
    assert result["ticker_flush_runs"] > 0


def test_measure_idle_overhead_reports_zero_process_item_calls_when_idle():
    result = asyncio.run(bench.measure_idle_overhead(interval_sec=0.01, n_ticks=5))
    assert result["process_item_calls_from_idle_flush"] == 0
    assert result["flush_calls"] == 5
