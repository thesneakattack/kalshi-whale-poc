"""Layer B - direct measurement of decision_bridge's queueing delay on the
REAL services/tick_executor.py pool under synthetic, production-grounded load
(issues #579/#580: "does the whale-decision critical path queue behind
settlement_resolver / fire-and-forget flush work on the shared 2-worker
ThreadPoolExecutor, and for how long?").

What is real here: `services.tick_executor.run()` and its module-level
`_executor` (ThreadPoolExecutor(max_workers=2)) - every submission below goes
through the shipped `run()` into the shipped pool. Instrumentation lives in
this harness only (a wrapper closure stamps submit / worker-start / worker-end
per call and tags it with the caller label); the shipped module is untouched.

What is synthetic: the WORK each caller does is `time.sleep(D)` for a
duration D drawn from a distribution grounded in Layer A's measurements
(bench/calibrate_real_durations.py, run against copies of the real DBs) and
the live app's 24h telemetry (/api/observability/summary tick.phase.*,
whale_pipeline.counter.signals_emitted per 1-min window, /api/health/pipeline
settlement_resolver drain). time.sleep releases the GIL, which is the right
model for SQLite I/O wait (sqlite3 releases the GIL around queries) but NOT
for CPU-bound Python on a worker - stated as an assumption in the report.

Workload shapes (one asyncio task each, mirroring the real call sites):
  settlement   services/settlement_resolver.run_pending via main._settlement_
               resolver_loop: sleep 5s; if pending: one REST batch gap, then
               up to 50 sequential `await run(...)` calls, one per ticker.
  tick         main.trading_loop's four awaited run() calls in order
               (resolve_and_record, flush_trade_capture, flush_secondary,
               series_stats_bulk) with network gaps between, then sleep 30s
               (kalshi.safety_net_interval_sec, streaming mode).
  snapshot     whale_stream_handlers:423 market_history.record_snapshot_from_
               ticker - fire-and-forget create_task(run(...)) per throttled
               ticker message.
  flush        the buffer-full fire-and-forget flush callers (index_feed,
               settlement_edge, game_state, series_watcher) - create_task(run()).
  decision     decision_bridge._handle_signal: await run(claim); evaluate on
               the loop; await run(record_decision). Concurrency capped at 4
               (services/kalshi/websocket.py _TRADE_DISPATCH_CONCURRENCY).
  diag_route   POSITIVE CONTROL ONLY - the two pre-#581 diagnostic routes
               (18-26s each, fired ~13ms apart) that PR #581 removed. Included
               so the harness demonstrably detects the mechanism when it is
               present; not part of any current-state scenario.

Run (host, from the worktree root; tick_executor is stdlib-only):
  python3 bench/bench_tick_executor_contention.py --calib bench/out/calibration.json \
      --scenario all --duration 90 --out bench/out/results.json
"""
import argparse
import asyncio
import json
import math
import random
import statistics
import sys
import threading
import time
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WT))

from services import tick_executor  # noqa: E402  - the REAL module under test

# ---------------------------------------------------------------------------
# Instrumentation (harness-side only)
# ---------------------------------------------------------------------------
_records: list[dict] = []
_records_lock = threading.Lock()
_inflight = 0
_inflight_lock = threading.Lock()


def _pool_queue_depth() -> int:
    # Introspection of the real pool's FIFO work queue for explanation only.
    try:
        return tick_executor._executor._work_queue.qsize()
    except Exception:
        return -1


async def run_tracked(label: str, work):
    """await tick_executor.run(work) through the REAL run()/pool, stamping
    submit -> worker start -> worker end. wait_sec = start - submit is the
    queueing delay this benchmark exists to measure."""
    global _inflight
    t_submit = time.perf_counter()
    queued_ahead = _pool_queue_depth()
    with _inflight_lock:
        busy_at_submit = _inflight
    holder: dict = {}

    def wrapped():
        global _inflight
        t_start = time.perf_counter()
        with _inflight_lock:
            _inflight += 1
        try:
            return work()
        finally:
            t_end = time.perf_counter()
            with _inflight_lock:
                _inflight -= 1
            holder["t_start"], holder["t_end"] = t_start, t_end

    try:
        return await tick_executor.run(wrapped)
    finally:
        rec = {
            "label": label,
            "t_submit": t_submit,
            "t_start": holder.get("t_start"),
            "t_end": holder.get("t_end"),
            "wait_sec": (holder["t_start"] - t_submit) if "t_start" in holder else None,
            "work_sec": (holder["t_end"] - holder["t_start"]) if "t_end" in holder else None,
            "busy_at_submit": busy_at_submit,
            "queued_ahead": queued_ahead,
        }
        with _records_lock:
            _records.append(rec)


def busy(seconds: float):
    def _work():
        if seconds > 0:
            time.sleep(seconds)
        return seconds
    return _work


# ---------------------------------------------------------------------------
# Duration distributions
# ---------------------------------------------------------------------------
def sample(spec) -> float:
    """spec: number | {"const": x} | {"uniform": [a,b]} | {"empirical": [..]}
    | {"lognormal": {"median": m, "p95": p}} | {"mixture": [[weight, spec],...]}"""
    if isinstance(spec, (int, float)):
        return float(spec)
    if "const" in spec:
        return float(spec["const"])
    if "uniform" in spec:
        a, b = spec["uniform"]
        return random.uniform(a, b)
    if "empirical" in spec:
        return random.choice(spec["empirical"])
    if "lognormal" in spec:
        m, p95 = spec["lognormal"]["median"], spec["lognormal"]["p95"]
        sigma = max(1e-6, math.log(p95 / m) / 1.6449)
        return m * math.exp(random.gauss(0.0, sigma))
    if "mixture" in spec:
        r, acc = random.random(), 0.0
        for w, sub in spec["mixture"]:
            acc += w
            if r <= acc:
                return sample(sub)
        return sample(spec["mixture"][-1][1])
    raise ValueError(spec)


async def exp_sleep(rate_per_sec: float):
    if rate_per_sec <= 0:
        await asyncio.sleep(3600)
        return
    await asyncio.sleep(random.expovariate(rate_per_sec))


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------
async def settlement_workload(cfg: dict, stop: asyncio.Event):
    """main._settlement_resolver_loop + settlement_resolver.run_pending shape."""
    pending = float(cfg.get("backlog", 0))
    enqueue_rate = float(cfg.get("enqueue_rate_per_sec", 0.0))
    batch = int(cfg.get("batch_size", 50))
    loop_sleep = float(cfg.get("loop_sleep_sec", 5.0))
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(loop_sleep)
        now = time.perf_counter()
        pending += enqueue_rate * (now - last)
        last = now
        if pending < 1:
            continue
        await asyncio.sleep(sample(cfg.get("rest_batch_sec", {"uniform": [0.08, 0.5]})))
        due = min(batch, int(pending))
        for _ in range(due):
            if stop.is_set():
                return
            await run_tracked("settlement", busy(sample(cfg["resolve_one_sync_sec"])))
            pending -= 1


async def tick_workload(cfg: dict, stop: asyncio.Event):
    """main.trading_loop's four awaited tick_executor.run() calls, in order."""
    await asyncio.sleep(random.uniform(0, 5))
    while not stop.is_set():
        await run_tracked("tick.resolve_and_record", busy(sample(cfg["resolve_and_record_sec"])))
        await asyncio.sleep(sample(cfg.get("network_gap_sec", {"uniform": [0.2, 1.0]})))
        await run_tracked("tick.flush_trade_capture", busy(sample(cfg["flush_trade_capture_sec"])))
        await run_tracked("tick.flush_secondary", busy(sample(cfg["flush_secondary_sec"])))
        await asyncio.sleep(sample(cfg.get("network_gap_sec", {"uniform": [0.2, 1.0]})))
        await run_tracked("tick.series_stats_bulk", busy(sample(cfg["series_stats_bulk_sec"])))
        await asyncio.sleep(float(cfg.get("interval_sec", 30.0)))


async def fire_and_forget_workload(label: str, cfg: dict, stop: asyncio.Event):
    """create_task(tick_executor.run(...)) at a Poisson rate, never awaited
    by the producer - the snapshot / flush caller shape."""
    tasks: set = set()
    while not stop.is_set():
        await exp_sleep(float(cfg["rate_per_sec"]))
        if stop.is_set():
            break
        t = asyncio.create_task(run_tracked(label, busy(sample(cfg["work_sec"]))))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def decision_workload(cfg: dict, stop: asyncio.Event, e2e: list):
    """decision_bridge._handle_signal's two awaited run() calls, under the
    trade-dispatch semaphore (4)."""
    sem = asyncio.Semaphore(int(cfg.get("concurrency", 4)))
    tasks: set = set()

    async def one_signal():
        async with sem:
            t0 = time.perf_counter()
            await run_tracked("decision.claim", busy(sample(cfg["claim_sec"])))
            await asyncio.sleep(sample(cfg.get("evaluate_on_loop_sec", 0.002)))
            await run_tracked("decision.record_decision", busy(sample(cfg["record_decision_sec"])))
            e2e.append(time.perf_counter() - t0)

    while not stop.is_set():
        await exp_sleep(float(cfg["rate_per_sec"]))
        if stop.is_set():
            break
        t = asyncio.create_task(one_signal())
        tasks.add(t)
        t.add_done_callback(tasks.discard)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def diag_route_workload(cfg: dict, stop: asyncio.Event):
    """POSITIVE CONTROL: the two pre-#581 diagnostic routes, each holding a
    worker 18-26s, fired ~13ms apart every poll interval."""
    tasks: set = set()
    while not stop.is_set():
        await asyncio.sleep(float(cfg.get("interval_sec", 15.0)))
        if stop.is_set():
            break
        for _ in range(2):
            t = asyncio.create_task(run_tracked("diag_route", busy(sample(cfg["work_sec"]))))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
            await asyncio.sleep(0.013)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def build_scenarios(calib: dict) -> dict:
    """Durations grounded in Layer A (calib) + 24h live telemetry. Every
    number here is either read from `calib` or annotated with its source."""
    c = calib
    claim = c["claim_sec"]
    record = c["record_decision_sec"]
    snap = c["snapshot_sec"]
    settle_real = c["resolve_one_sync_sec"]              # empirical, Layer A
    settle_heavy = c["resolve_one_sync_heavy_sec"]        # empirical tail-weighted

    # 24h tick.phase telemetry (observability/summary, 869 samples):
    #   resolve_and_record_sec      avg 1.99  max 39.6
    #   capture_flush_and_titles_sec avg 9.25 max 754  (includes network title
    #   fetch + _resolve_settlement_windows; executor-side share ASSUMED ~60%)
    tick_avg = {
        "resolve_and_record_sec": {"lognormal": {"median": 1.6, "p95": 6.0}},
        "flush_trade_capture_sec": {"lognormal": {"median": 1.0, "p95": 4.0}},
        "flush_secondary_sec": {"lognormal": {"median": 2.0, "p95": 8.0}},
        "series_stats_bulk_sec": {"lognormal": {"median": 1.0, "p95": 3.0}},
        "interval_sec": 30.0,
    }
    tick_p95 = {
        "resolve_and_record_sec": {"const": 10.0},
        "flush_trade_capture_sec": {"const": 5.0},
        "flush_secondary_sec": {"const": 20.0},
        "series_stats_bulk_sec": {"const": 5.0},
        "interval_sec": 30.0,
    }
    tick_pinned = {  # the hourly-prune / 754s-max shape: one worker held for minutes
        "resolve_and_record_sec": {"const": 2.0},
        "flush_trade_capture_sec": {"const": 1.0},
        "flush_secondary_sec": {"const": 120.0},
        "series_stats_bulk_sec": {"const": 1.0},
        "interval_sec": 30.0,
    }
    # signals_emitted per 1-min window, 24h: avg 40 (0.67/s), max 417 (7/s);
    # historical extreme 20k/28min (~12/s).
    dec = lambda rate: {"rate_per_sec": rate, "claim_sec": claim, "record_decision_sec": record,
                        "concurrency": 4, "evaluate_on_loop_sec": {"uniform": [0.001, 0.005]}}
    # ticker messages: 1.3/s watchlist now; 10+/s exchange-wide (2026-09-03 note)
    snaps = lambda rate: {"rate_per_sec": rate, "work_sec": snap}
    # buffer-full flushes: executemany of 100-500 rows; ASSUMED 20-100ms
    # realistic, 50-300ms adversarial (series_watcher.db is 34GB).
    flushes = lambda rate, lo, hi: {"rate_per_sec": rate, "work_sec": {"uniform": [lo, hi]}}
    settle_quiet = {"backlog": 12, "enqueue_rate_per_sec": 0.05, "resolve_one_sync_sec": settle_real}
    # #580's measured surge: 0 -> 1,726 pending in 191s (~9.16/s); drain ~2/s
    settle_surge = {"backlog": 1726, "enqueue_rate_per_sec": 0.0, "resolve_one_sync_sec": settle_real}
    settle_surge_heavy = {"backlog": 1726, "enqueue_rate_per_sec": 9.16, "resolve_one_sync_sec": settle_heavy}

    return {
        "S0_baseline_decisions_only": {
            "decision": dec(0.67),
        },
        "S1_realistic_quiet": {
            "settlement": settle_quiet, "tick": tick_avg, "snapshot": snaps(2.0),
            "flush": flushes(0.1, 0.02, 0.1), "decision": dec(0.67),
        },
        "S2_settlement_surge_realistic_signals": {
            "settlement": settle_surge, "tick": tick_avg, "snapshot": snaps(2.0),
            "flush": flushes(0.2, 0.02, 0.1), "decision": dec(0.67),
        },
        "S3_settlement_surge_peak_signals": {
            "settlement": settle_surge, "tick": tick_avg, "snapshot": snaps(2.0),
            "flush": flushes(0.2, 0.02, 0.1), "decision": dec(7.0),
        },
        "S4_adversarial_all_elevated": {
            "settlement": settle_surge_heavy, "tick": tick_p95, "snapshot": snaps(10.0),
            "flush": flushes(1.0, 0.05, 0.3), "decision": dec(12.0),
        },
        "S5_pinned_long_tick_plus_surge": {
            "settlement": settle_surge, "tick": tick_pinned, "snapshot": snaps(2.0),
            "flush": flushes(0.2, 0.02, 0.1), "decision": dec(7.0),
        },
        "S6_POSITIVE_CONTROL_pre581_diag_routes": {
            "settlement": settle_surge, "tick": tick_avg, "snapshot": snaps(2.0),
            "flush": flushes(0.2, 0.02, 0.1), "decision": dec(0.67),
            "diag_route": {"interval_sec": 15.0, "work_sec": {"uniform": [18.0, 26.0]}},
        },
    }


# ---------------------------------------------------------------------------
# Runner / reporting
# ---------------------------------------------------------------------------
def _pct(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(p / 100.0 * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def summarize(records: list[dict], e2e: list, warmup_sec: float, t0: float) -> dict:
    out = {}
    by_label: dict[str, list[dict]] = {}
    for r in records:
        if r["wait_sec"] is None or r["t_submit"] - t0 < warmup_sec:
            continue
        by_label.setdefault(r["label"], []).append(r)
    for label, rs in sorted(by_label.items()):
        waits = sorted(r["wait_sec"] for r in rs)
        works = [r["work_sec"] for r in rs]
        out[label] = {
            "n": len(rs),
            "wait_mean_ms": statistics.fmean(waits) * 1000,
            "wait_p50_ms": _pct(waits, 50) * 1000,
            "wait_p95_ms": _pct(waits, 95) * 1000,
            "wait_p99_ms": _pct(waits, 99) * 1000,
            "wait_max_ms": waits[-1] * 1000,
            "wait_gt_100ms_frac": sum(1 for w in waits if w > 0.1) / len(waits),
            "wait_gt_1s_frac": sum(1 for w in waits if w > 1.0) / len(waits),
            "wait_gt_10s_frac": sum(1 for w in waits if w > 10.0) / len(waits),
            "work_mean_ms": statistics.fmean(works) * 1000,
            "work_total_sec": sum(works),
            "both_workers_busy_at_submit_frac": sum(1 for r in rs if r["busy_at_submit"] >= 2) / len(rs),
            "queued_ahead_mean": statistics.fmean(r["queued_ahead"] for r in rs),
            "queued_ahead_max": max(r["queued_ahead"] for r in rs),
        }
    dec = [r for r in records if r["label"].startswith("decision.") and r["wait_sec"] is not None
           and r["t_submit"] - t0 >= warmup_sec]
    if dec:
        waits = sorted(r["wait_sec"] for r in dec)
        out["DECISION_COMBINED"] = {
            "n": len(dec),
            "wait_mean_ms": statistics.fmean(waits) * 1000,
            "wait_p50_ms": _pct(waits, 50) * 1000,
            "wait_p95_ms": _pct(waits, 95) * 1000,
            "wait_p99_ms": _pct(waits, 99) * 1000,
            "wait_max_ms": waits[-1] * 1000,
            "wait_gt_100ms_frac": sum(1 for w in waits if w > 0.1) / len(waits),
            "wait_gt_1s_frac": sum(1 for w in waits if w > 1.0) / len(waits),
            "wait_gt_10s_frac": sum(1 for w in waits if w > 10.0) / len(waits),
        }
    if e2e:
        s = sorted(e2e)
        out["DECISION_E2E_claim_to_record"] = {
            "n": len(s), "mean_ms": statistics.fmean(s) * 1000, "p50_ms": _pct(s, 50) * 1000,
            "p95_ms": _pct(s, 95) * 1000, "p99_ms": _pct(s, 99) * 1000, "max_ms": s[-1] * 1000,
        }
    return out


async def run_scenario(name: str, sc: dict, duration_sec: float, warmup_sec: float) -> dict:
    global _records, _inflight
    with _records_lock:
        _records = []
    with _inflight_lock:
        _inflight = 0
    stop = asyncio.Event()
    e2e: list = []
    tasks = []
    if "settlement" in sc:
        tasks.append(asyncio.create_task(settlement_workload(sc["settlement"], stop)))
    if "tick" in sc:
        tasks.append(asyncio.create_task(tick_workload(sc["tick"], stop)))
    if "snapshot" in sc:
        tasks.append(asyncio.create_task(fire_and_forget_workload("snapshot", sc["snapshot"], stop)))
    if "flush" in sc:
        tasks.append(asyncio.create_task(fire_and_forget_workload("flush", sc["flush"], stop)))
    if "diag_route" in sc:
        tasks.append(asyncio.create_task(diag_route_workload(sc["diag_route"], stop)))
    if "decision" in sc:
        tasks.append(asyncio.create_task(decision_workload(sc["decision"], stop, e2e)))
    t0 = time.perf_counter()
    await asyncio.sleep(duration_sec)
    stop.set()
    # Let in-flight submissions finish (a pinned worker may take a while).
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=200)
    except asyncio.TimeoutError:
        for t in tasks:
            t.cancel()
    with _records_lock:
        recs = list(_records)
    # Only count records whose work ran during the measured window.
    recs = [r for r in recs if r["t_submit"] - t0 <= duration_sec]
    summary = summarize(recs, e2e, warmup_sec, t0)
    pool_busy = sum(r["work_sec"] for r in recs if r["work_sec"] is not None
                    and r["t_submit"] - t0 >= warmup_sec)
    measured = duration_sec - warmup_sec
    summary["_pool"] = {
        "max_workers": tick_executor._executor._max_workers,
        "measured_window_sec": measured,
        # work submitted inside the window / (2 workers x window); can exceed
        # 1.0 when a long task submitted late in the window overruns it.
        "utilization_frac_of_2_workers": pool_busy / (2 * measured) if measured > 0 else None,
        "submissions": len([r for r in recs if r["t_submit"] - t0 >= warmup_sec]),
    }
    return summary


def default_calib() -> dict:
    """Fallback only if no Layer A file is given - values flagged as assumed."""
    return {
        "_source": "ASSUMED FALLBACK - not measured",
        "claim_sec": {"lognormal": {"median": 0.003, "p95": 0.02}},
        "record_decision_sec": {"lognormal": {"median": 0.003, "p95": 0.02}},
        "snapshot_sec": {"lognormal": {"median": 0.004, "p95": 0.02}},
        "resolve_one_sync_sec": {"lognormal": {"median": 0.25, "p95": 0.8}},
        "resolve_one_sync_heavy_sec": {"mixture": [[0.9, {"lognormal": {"median": 0.25, "p95": 0.8}}],
                                                   [0.1, {"uniform": [2.0, 5.0]}]]},
    }


def calib_from_layer_a(path: Path) -> dict:
    a = json.loads(path.read_text())
    has = a["resolve_one_sync_has_work"]["samples_sec"]
    noop = a["resolve_one_sync_recent_settled"]["samples_sec"]
    # Realistic mix: the live drain rate implies the typical settled ticker
    # is closer to the recent-settled sample; weight 50/50 and let the tail
    # of the has-work set carry the p95.
    mixed = has + noop

    def ln(s):
        return {"lognormal": {"median": max(1e-4, s["p50_sec"]), "p95": max(2e-4, s["p95_sec"])}}

    return {
        "_source": str(path),
        "claim_sec": ln(a["claim_record"]["claim"]),
        "record_decision_sec": ln(a["claim_record"]["record_decision"]),
        "snapshot_sec": ln(a["snapshot_from_ticker"]),
        "resolve_one_sync_sec": {"empirical": mixed},
        # Tail-weighted: every call drawn from the has-work set, plus a 5%
        # chance of a 3-8s call (the "locked store / big rejected_candidates
        # scan" tail the live max-39s resolve_and_record hints at).
        "resolve_one_sync_heavy_sec": {"mixture": [[0.95, {"empirical": has}], [0.05, {"uniform": [3.0, 8.0]}]]},
        "_layer_a_summary": {
            "claim": a["claim_record"]["claim"],
            "record_decision": a["claim_record"]["record_decision"],
            "snapshot": a["snapshot_from_ticker"],
            "resolve_one_sync_has_work": a["resolve_one_sync_has_work"]["resolve_one_sync"],
            "resolve_one_sync_recent_settled": a["resolve_one_sync_recent_settled"]["resolve_one_sync"],
        },
    }


def print_table(name: str, s: dict):
    print(f"\n=== {name} ===  pool util={s['_pool']['utilization_frac_of_2_workers']:.2f} "
          f"submissions={s['_pool']['submissions']} window={s['_pool']['measured_window_sec']:.0f}s")
    hdr = (f"{'label':34s}{'n':>6s}{'wait_mean_ms':>13s}{'p50_ms':>11s}{'p95_ms':>11s}{'p99_ms':>11s}"
           f"{'max_ms':>11s}{'>100ms':>8s}{'>1s':>7s}{'>10s':>7s}{'both_busy':>10s}{'work_mean_ms':>13s}")
    print(hdr)
    for label, v in s.items():
        if label.startswith("_") or label == "DECISION_E2E_claim_to_record":
            continue
        bb = v.get("both_workers_busy_at_submit_frac")
        wm = f"{v['work_mean_ms']:.1f}" if "work_mean_ms" in v else "-"
        print(f"{label:34s}{v['n']:>6d}{v['wait_mean_ms']:>13.1f}{v['wait_p50_ms']:>11.1f}"
              f"{v['wait_p95_ms']:>11.1f}{v['wait_p99_ms']:>11.1f}{v['wait_max_ms']:>11.1f}"
              f"{v['wait_gt_100ms_frac']:>8.3f}{v['wait_gt_1s_frac']:>7.3f}{v['wait_gt_10s_frac']:>7.3f}"
              f"{(f'{bb:.2f}' if bb is not None else '-'):>10s}{wm:>13s}")
    e = s.get("DECISION_E2E_claim_to_record")
    if e:
        print(f"decision e2e (claim->record_decision): n={e['n']} mean={e['mean_ms']:.1f}ms "
              f"p50={e['p50_ms']:.1f}ms p95={e['p95_ms']:.1f}ms p99={e['p99_ms']:.1f}ms max={e['max_ms']:.1f}ms")


async def main_async(args) -> int:
    calib = calib_from_layer_a(Path(args.calib)) if args.calib else default_calib()
    scenarios = build_scenarios(calib)
    names = list(scenarios) if args.scenario == "all" else [s for s in args.scenario.split(",")]
    results = {"calibration": calib, "duration_sec": args.duration, "warmup_sec": args.warmup,
               "scenarios": {}, "scenario_configs": {n: scenarios[n] for n in names}}
    for n in names:
        print(f"\n--- running {n} for {args.duration}s ---", flush=True)
        s = await run_scenario(n, scenarios[n], args.duration, args.warmup)
        results["scenarios"][n] = s
        print_table(n, s)
        sys.stdout.flush()
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(results, indent=1, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", default=None, help="Layer A calibration JSON")
    ap.add_argument("--scenario", default="all")
    ap.add_argument("--duration", type=float, default=90.0)
    ap.add_argument("--warmup", type=float, default=5.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=20260905)
    args = ap.parse_args()
    random.seed(args.seed)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
