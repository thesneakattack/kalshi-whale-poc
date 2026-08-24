"""
Synthetic performance regression checks for historically problematic code
paths - Quality Control Plane Task 19 (docs/superpowers/plans/2026-08-24-
quality-control-plane.md). Opt-in only (RUN_PERFORMANCE_REGRESSIONS=1),
same convention as tests/test_browser_e2e.py's RUN_BROWSER_E2E - an
ordinary `pytest` run (Woodpecker's tests-pytest.yml, on every push/PR)
must never pay for 50k/100k-row synthetic datasets on every edit.
.github/workflows/performance.yml (scheduled/manual, per this task's own
"Interfaces: scheduled/manual initially") is the only caller that sets it.

Deliberately narrow, not the plan's full 6-item candidate list: only
functions with REAL documented historical evidence of a regression at
current HEAD, checked directly rather than guessed -

- trade_analytics.compute_summary / advisory_engine.generate_recommendations:
  generate_recommendations' own comment (2026-08-23) describes a real,
  just-fixed bug - compute_summary (an O(n) pass over the full trade
  history) was being computed twice per call in the normal case.
- series_evaluator.evaluate_pending: its own comment describes a real,
  fixed 2026-08-11 incident - a fresh sqlite3.connect() (a real connect
  plus a CREATE TABLE IF NOT EXISTS check) was being paid once per ready
  series instead of once per call, "real overhead... that took the app
  down" under bursty conditions.
- candidate_log.population_gate_summary: the newest of the four
  (rejection_events, added 2026-08-23 specifically to fix a population-
  statistics gap) - no O(n^2) shape in its own implementation (one query,
  one Python pass), but real, growing, unbounded-retention history
  (CLAUDE.md's "accumulated history is a first-class asset" rule) makes it
  the most likely of the four to grow large enough to matter later.

market-history calculations and config override resolution (the plan's
other two candidates) have no such evidence at current HEAD - no O(n^2)/
N+1 comment, no past-incident reference found via grep - and are
deliberately NOT included here. Revisit only if real evidence surfaces,
per this task's own "select only real historical bottleneck candidates"
instruction; a benchmark with no real regression to guard against is
noise, not a guard.

Prefer operation-count invariants over wall-time thresholds (Step 3/4):
series_evaluator and generate_recommendations each get a hard, deterministic
assertion directly encoding the historical fix (one connection per batch,
one compute_summary call per invocation) - these can never flake on a
loaded CI runner. compute_summary/population_gate_summary have no natural
operation-count invariant (already O(n) with no per-row I/O), so those two
get a scaling-ratio check instead (10x the rows should cost roughly 10x
the time, not ~100x) plus a generous absolute ceiling - both deliberately
loose to avoid noise per the design spec's "avoid noise from shared
runners" instruction, not a promise of a specific millisecond figure.
Every measured wall time is also recorded to build/performance-baseline.json
(gitignored, uploaded as a CI artifact) rather than hardcoded as an exact
number to assert against later, per Step 4's own "store baseline metadata
in the artifact, not as dozens of fragile exact numbers" instruction - a
real baseline-ratchet (fail if >2x a stored number) is deliberately left
for a later task once several scheduled runs have accumulated real data
(Step 5: "do not make that decision automatically in this task").
"""
import json
import os
import random
import time
from pathlib import Path

import pytest

from services import candidate_log, series_evaluator, signal_log, trade_analytics
from services.advisory import advisory_engine

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PERFORMANCE_REGRESSIONS") != "1",
    reason="performance regression checks are opt-in (RUN_PERFORMANCE_REGRESSIONS=1)",
)

_SEED = 20260824
_BASELINE_PATH = Path("build") / "performance-baseline.json"


def _record_measurement(name: str, **fields) -> None:
    """Best-effort - a failure to write the artifact must never fail the
    actual regression check it's recording data for."""
    try:
        _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if _BASELINE_PATH.exists():
            try:
                data = json.loads(_BASELINE_PATH.read_text())
            except json.JSONDecodeError:
                data = {}
        data[name] = {"recorded_at": time.time(), **fields}
        _BASELINE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True))
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(series_evaluator, "DB_PATH", tmp_path / "series_evaluator.db")
    monkeypatch.setattr(candidate_log, "DB_PATH", tmp_path / "candidate_log.db")


# --- deterministic dataset builders -----------------------------------

def _make_trade_rows(n: int, num_fingerprints: int = 6, seed: int = _SEED) -> list[dict]:
    rng = random.Random(seed)
    fingerprints = [f"fp{i}" for i in range(num_fingerprints)]
    close_types = ["take_profit", "stop_loss", "settled_win", "settled_loss", "sentiment_reversal"]
    rows = []
    for i in range(n):
        rows.append({
            "ticker": f"KXFAKE{i % 500}",
            "config_fingerprint": rng.choice(fingerprints),
            "entry_confidence": rng.uniform(0.5, 0.95),
            "won": rng.random() < 0.5,
            "close_type": rng.choice(close_types),
            "realized_pnl": rng.uniform(-1.0, 1.0),
            "hold_sec": rng.uniform(10, 7200),
            "left_on_table": rng.uniform(0, 0.15) if rng.random() < 0.3 else None,
            "cost_basis": rng.uniform(0.05, 0.95),
            "fees_paid": rng.uniform(0, 0.01),
        })
    return rows


def _make_advisory_cfg() -> dict:
    return {"strategy": {
        "entry_threshold": 0.6, "longshot_price_threshold": 0.15, "longshot_entry_threshold_bonus": 0.15,
        "take_profit_pct": 0.5, "stop_loss_pct": 0.4, "auto_exit_threshold": 0.6,
        "exit_sentiment_lean_pct": 65, "exit_sentiment_min_signals": 3,
    }}


def _seed_series_status(n: int, now: float) -> None:
    with series_evaluator._connect() as conn:
        conn.executemany(
            "INSERT INTO series_status (series, status, first_seen_at, trades_observed, strike_count) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (f"KXSERIES{i}", series_evaluator._STATUS_OBSERVING, now - 999_999, 50, 0)
                for i in range(n)
            ],
        )
        conn.commit()


def _seed_rejection_events(n: int, seed: int = _SEED) -> None:
    rng = random.Random(seed)
    gates = ["entry_threshold", "min_whale_winrate_pct", "min_contracts", "close_window", "special_market_gate"]
    now = time.time()
    with candidate_log._connect() as conn:
        conn.executemany(
            "INSERT INTO rejection_events "
            "(ticker, strategy, gate_name, observed_value, threshold_value, side, rejected_at, resolved, result, unit_cost) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"KXFAKE{i % 1000}", "follow_the_whale", rng.choice(gates),
                    rng.uniform(0, 1), rng.uniform(0, 1), rng.choice(["yes", "no"]),
                    now - rng.uniform(0, 86400), 1, rng.choice(["yes", "no"]), rng.uniform(0.05, 0.95),
                )
                for i in range(n)
            ],
        )
        conn.commit()


# --- series_evaluator.evaluate_pending: connection-count invariant --------

def test_evaluate_pending_opens_exactly_one_connection_regardless_of_batch_size(monkeypatch):
    """Encodes the 2026-08-11 fix directly: N series becoming ready in the
    same tick must cost ONE series_evaluator._connect() call, not N - the
    exact regression this module's own comment documents having happened
    live. signal_log.signal_count_for_series_since is stubbed out (it has
    its own, separate, real per-series _connect() call this test
    deliberately does not assert against - see ROADMAP.md for that
    residual, un-fixed finding) so this test isolates series_evaluator's
    own connection behavior specifically."""
    monkeypatch.setattr(signal_log, "signal_count_for_series_since", lambda series, since_ts: 5)

    now = time.time()
    n = 2_000
    _seed_series_status(n, now)

    real_connect = series_evaluator._connect
    call_count = {"n": 0}

    def _counting_connect():
        call_count["n"] += 1
        return real_connect()

    monkeypatch.setattr(series_evaluator, "_connect", _counting_connect)

    t0 = time.perf_counter()
    verdicts = series_evaluator.evaluate_pending({"series_evaluator": {}}, now=now)
    elapsed = time.perf_counter() - t0

    assert len(verdicts) == n
    assert call_count["n"] == 1, (
        f"evaluate_pending opened {call_count['n']} connections for {n} ready series - "
        "expected exactly 1 (the 2026-08-11 fix regressed)"
    )
    _record_measurement("series_evaluator.evaluate_pending", n=n, elapsed_sec=round(elapsed, 4), connections=call_count["n"])


# --- advisory_engine.generate_recommendations: no-duplicate-compute -----

def test_generate_recommendations_computes_the_full_dataset_summary_exactly_once(monkeypatch):
    """Encodes the 2026-08-23 fix directly (this function's own comment):
    the FULL-dataset summary (compute_summary(rows), an O(n) pass) was
    being computed twice per call in the normal case - once each inside
    _rejected_candidate_recommendations and _category_conditional_
    recommendations - before the fix hoisted one shared overall_summary
    above both. Only exercised when gate_summaries AND category_rows are
    both supplied (the exact "every real caller passes both together"
    condition the fix's own comment names) - both hand-built here rather
    than fetched from a DB, matching each function's own documented input
    shape (_rejected_candidate_recommendations/_GATE_CONFIG_PATH_AND_
    DIRECTION, _category_conditional_recommendations's category/
    total_closed/win_rate_pct keys).

    Deliberately counts only calls where the argument IS the full `rows`
    list, not every compute_summary call in total - variant_summaries()
    legitimately calls compute_summary() once per config-fingerprint group
    (a distinct, smaller row subset each time, computing something
    genuinely different from the full-dataset summary), which is real,
    intentional, non-redundant work this test must not flag."""
    rows = _make_trade_rows(10_000)
    variants = {fp: {"fingerprint": fp, "first_seen_at": 0.0} for fp in {r["config_fingerprint"] for r in rows}}
    cfg = _make_advisory_cfg()
    gate_summaries = [{
        "strategy": "whale_follow", "gate_name": "entry_threshold",
        "hypothetical_win_rate": 55.0, "hypothetical_win_rate_n": 40,
    }]
    category_rows = [{"category": "Sports", "total_closed": 500, "win_rate_pct": 55.0}]

    real_compute_summary = trade_analytics.compute_summary
    full_dataset_calls = {"n": 0}

    def _counting_compute_summary(rows_arg):
        if len(rows_arg) == len(rows):
            full_dataset_calls["n"] += 1
        return real_compute_summary(rows_arg)

    monkeypatch.setattr(trade_analytics, "compute_summary", _counting_compute_summary)

    t0 = time.perf_counter()
    result = advisory_engine.generate_recommendations(
        rows, cfg, "fp0", variants, min_resolved_trades=30,
        gate_summaries=gate_summaries, category_rows=category_rows,
    )
    elapsed = time.perf_counter() - t0

    assert "recommendations" in result
    assert full_dataset_calls["n"] == 1, (
        f"generate_recommendations computed the full-dataset summary {full_dataset_calls['n']} times - "
        "expected exactly 1 (the 2026-08-23 duplicate-computation fix regressed)"
    )
    _record_measurement(
        "advisory_engine.generate_recommendations",
        n=len(rows), elapsed_sec=round(elapsed, 4), full_dataset_summary_calls=full_dataset_calls["n"],
    )


# --- compute_summary / population_gate_summary: scaling-ratio checks -----

def test_compute_summary_scales_roughly_linearly_not_quadratically():
    small = _make_trade_rows(5_000)
    large = _make_trade_rows(50_000)

    t0 = time.perf_counter()
    trade_analytics.compute_summary(small)
    small_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    trade_analytics.compute_summary(large)
    large_elapsed = time.perf_counter() - t0

    ratio = large_elapsed / max(small_elapsed, 1e-6)
    # 10x the rows should cost roughly 10x the time for a real linear pass,
    # not ~100x (O(n^2)) - a generous multiplier absorbs real noise on a
    # shared CI runner (design spec section 18's own "avoid noise"
    # instruction) rather than asserting a tight ratio.
    assert ratio < 30, f"compute_summary scaled {ratio:.1f}x for a 10x row increase - possible O(n^2)"
    assert large_elapsed < 5.0, f"compute_summary took {large_elapsed:.2f}s for 50k rows (absolute ceiling: 5s)"
    _record_measurement(
        "trade_analytics.compute_summary",
        n_small=len(small), n_large=len(large),
        small_elapsed_sec=round(small_elapsed, 4), large_elapsed_sec=round(large_elapsed, 4),
        scaling_ratio=round(ratio, 2),
    )


def test_population_gate_summary_scales_roughly_linearly_not_quadratically():
    _seed_rejection_events(10_000, seed=_SEED)
    t0 = time.perf_counter()
    small_result = candidate_log.population_gate_summary()
    small_elapsed = time.perf_counter() - t0

    _seed_rejection_events(90_000, seed=_SEED + 1)  # cumulative: 100k total rows now
    t0 = time.perf_counter()
    large_result = candidate_log.population_gate_summary()
    large_elapsed = time.perf_counter() - t0

    ratio = large_elapsed / max(small_elapsed, 1e-6)
    assert ratio < 30, f"population_gate_summary scaled {ratio:.1f}x for a 10x row increase - possible O(n^2)"
    assert large_elapsed < 5.0, f"population_gate_summary took {large_elapsed:.2f}s for 100k rows (absolute ceiling: 5s)"
    assert len(large_result) > 0
    assert len(large_result) == len(small_result)  # same 5 synthetic gates throughout, only volume grew
    _record_measurement(
        "candidate_log.population_gate_summary",
        n_small=10_000, n_large=100_000,
        small_elapsed_sec=round(small_elapsed, 4), large_elapsed_sec=round(large_elapsed, 4),
        scaling_ratio=round(ratio, 2),
    )
