"""Layer A - real-work duration calibration for the tick_executor contention
benchmark (issues #579/#580).

Measures, against sqlite backup COPIES of the real data/*.db files (see
bench/copy_dbs.py - never the live files), how long the real functions that
occupy tick_executor's 2 workers actually take:

  - candidate_ledger.claim() / record_decision()   (decision_bridge, awaited,
    the whale-decision critical path)
  - settlement_resolver._resolve_one_sync()        (settlement_resolver, one
    awaited call per settled ticker)
  - market_history.record_snapshot_from_ticker()   (whale_stream_handlers:423,
    fire-and-forget per throttled ticker message)

Every module's DB_PATH (and capture_writer's _STORE_PATHS for candidate_log's
two capture-writer tables) is redirected at the copies BEFORE any call, and
the worktree this runs from has an empty data/ dir (only .gitkeep) - so even
an un-redirected path could not reach a live file. history_push's broadcast
hook is stubbed (in-memory coalescing flag, not DB work, needs the app loop).

Run inside the fastapi container from the worktree root:
  docker exec -w /app/.claude/worktrees/<wt> ddev-kalshi-whale-poc-fastapi \
      nice -n 19 ionice -c3 python3 bench/calibrate_real_durations.py \
      --out bench/out/calibration.json
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
import uuid
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WT))
BENCH_DBS = Path("/tmp/bench_dbs")
SCRATCH = Path("/tmp/bench_scratch")


def _stats(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0}
    s = sorted(samples)

    def pct(p: float) -> float:
        idx = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
        return s[idx]

    return {
        "n": len(s),
        "mean_sec": statistics.fmean(s),
        "p50_sec": pct(50),
        "p90_sec": pct(90),
        "p95_sec": pct(95),
        "p99_sec": pct(99),
        "max_sec": s[-1],
        "min_sec": s[0],
    }


def _redirect_paths() -> dict:
    """Point every module the measured functions touch at the copies."""
    from services import candidate_ledger, candidate_log, capture_writer, fault_log, history_push
    from services import market_history, settlement_edge, signal_log
    from services.market_analyst_agent import _db as ma_db

    assert not any(p.suffix == ".db" for p in (WT / "data").iterdir()), \
        "worktree data/ must hold no .db files - refusing to run"
    for p in ("candidate_ledger.db", "candidate_log.db", "signal_log.db",
              "market_history.db", "settlement_edge.db", "market_analyst.db"):
        assert (BENCH_DBS / p).exists(), f"missing copy {p}"

    SCRATCH.mkdir(exist_ok=True)
    candidate_ledger.DB_PATH = BENCH_DBS / "candidate_ledger.db"
    candidate_log.DB_PATH = BENCH_DBS / "candidate_log.db"
    signal_log.DB_PATH = BENCH_DBS / "signal_log.db"
    market_history.DB_PATH = BENCH_DBS / "market_history.db"
    settlement_edge.DB_PATH = BENCH_DBS / "settlement_edge.db"
    ma_db.DB_PATH = BENCH_DBS / "market_analyst.db"
    capture_writer._STORE_PATHS["rejection_events"] = BENCH_DBS / "candidate_log.db"
    capture_writer._STORE_PATHS["rejected_candidates"] = BENCH_DBS / "candidate_log.db"
    capture_writer._STORE_PATHS["raw_trades"] = SCRATCH / "series_watcher_scratch.db"
    fault_log.DB_PATH = SCRATCH / "fault_log_scratch.db"
    # In-memory coalescing flag + a loop-bound broadcast; not DB work, and
    # there is no app event loop here.
    history_push.mark_history_changed = lambda **_kw: None
    return {
        "candidate_ledger": str(candidate_ledger.DB_PATH),
        "candidate_log": str(candidate_log.DB_PATH),
        "signal_log": str(signal_log.DB_PATH),
        "market_history": str(market_history.DB_PATH),
        "settlement_edge": str(settlement_edge.DB_PATH),
        "market_analyst": str(ma_db.DB_PATH),
    }


def _pick_tickers() -> dict:
    """Real tickers from the copies: ones with pending signal_log / candidate
    rows (the 'has work' case) and recently-settled ones from
    market_history.outcomes (mostly already-resolved -> near no-op case)."""
    out = {}
    with sqlite3.connect(f"file:{BENCH_DBS / 'signal_log.db'}?mode=ro", uri=True) as c:
        out["signals_pending_by_ticker"] = c.execute(
            "SELECT ticker, COUNT(*) FROM signals WHERE resolved = 0 GROUP BY ticker "
            "ORDER BY COUNT(*) DESC LIMIT 80").fetchall()
        out["signals_unresolved_total"] = c.execute(
            "SELECT COUNT(*) FROM signals WHERE resolved = 0").fetchone()[0]
    with sqlite3.connect(f"file:{BENCH_DBS / 'candidate_log.db'}?mode=ro", uri=True) as c:
        out["rejected_candidates_unresolved_total"] = c.execute(
            "SELECT COUNT(*) FROM rejected_candidates WHERE resolved = 0").fetchone()[0]
        out["rejected_candidates_pending_by_ticker"] = c.execute(
            "SELECT ticker, COUNT(*) FROM rejected_candidates WHERE resolved = 0 GROUP BY ticker "
            "ORDER BY COUNT(*) DESC LIMIT 80").fetchall()
        out["rejection_events_unresolved_by_ticker_top"] = c.execute(
            "SELECT ticker, COUNT(*) FROM rejection_events WHERE resolved = 0 GROUP BY ticker "
            "ORDER BY COUNT(*) DESC LIMIT 40").fetchall()
        out["rejection_events_total_approx"] = c.execute(
            "SELECT MAX(rowid) FROM rejection_events").fetchone()[0]
        out["rejection_events_update_plan"] = [
            r[3] for r in c.execute(
                "EXPLAIN QUERY PLAN UPDATE rejection_events SET resolved = 1, result = ?, "
                "resolved_at = ? WHERE ticker = ? AND resolved = 0", ("yes", 0.0, "X")).fetchall()
        ]
    with sqlite3.connect(f"file:{BENCH_DBS / 'market_history.db'}?mode=ro", uri=True) as c:
        out["recent_settled"] = [r[0] for r in c.execute(
            "SELECT ticker FROM outcomes ORDER BY resolved_at DESC LIMIT 120").fetchall()]
    return out


def measure_claim_record(n: int) -> dict:
    from services import candidate_ledger
    claim_t, rec_t = [], []
    ids = []
    for _ in range(n):
        tid = f"bench-{uuid.uuid4()}"
        ids.append(tid)
        t = time.perf_counter()
        ok = candidate_ledger.claim(tid, ticker="BENCH-TICKER")
        claim_t.append(time.perf_counter() - t)
        assert ok
    for tid in ids:
        t = time.perf_counter()
        candidate_ledger.record_decision(tid, "skip")
        rec_t.append(time.perf_counter() - t)
    return {"claim": _stats(claim_t), "record_decision": _stats(rec_t)}


def measure_resolve_one_sync(tickers: list[str], label: str) -> dict:
    from services import settlement_resolver
    from services import candidate_log, market_analyst_agent, market_history, settlement_edge, signal_log
    per_call, per_store = [], {"market_history": [], "settlement_edge": [], "market_analyst": [],
                               "candidate_log": [], "signal_log": []}
    rows_resolved = []
    now = time.time()
    for i, ticker in enumerate(tickers):
        result = "yes" if i % 2 == 0 else "no"
        t = time.perf_counter()
        rows = settlement_resolver._resolve_one_sync(ticker, result, now)
        per_call.append(time.perf_counter() - t)
        rows_resolved.append(rows)
    # Per-store breakdown on a second, disjoint pass would be resolved-already;
    # instead time the five calls individually on the SAME tickers a second
    # time (idempotent WHERE resolved=0 paths -> this is the "no rows left"
    # floor per store, i.e. the fixed per-call cost of each store's query).
    for ticker in tickers[:40]:
        t = time.perf_counter(); market_history.record_outcome(ticker, "yes", resolved_at=now)
        per_store["market_history"].append(time.perf_counter() - t)
        t = time.perf_counter(); settlement_edge.resolve_window(ticker, True)
        per_store["settlement_edge"].append(time.perf_counter() - t)
        t = time.perf_counter(); market_analyst_agent.resolve_from_market_results({ticker: "yes"})
        per_store["market_analyst"].append(time.perf_counter() - t)
        t = time.perf_counter(); candidate_log.resolve_from_market_results({ticker: "yes"})
        per_store["candidate_log"].append(time.perf_counter() - t)
        t = time.perf_counter(); signal_log.resolve_from_market_results(ticker, "yes")
        per_store["signal_log"].append(time.perf_counter() - t)
    return {
        "label": label,
        "resolve_one_sync": _stats(per_call),
        "rows_resolved_total": sum(rows_resolved),
        "rows_resolved_nonzero_calls": sum(1 for r in rows_resolved if r),
        "per_store_fixed_cost_second_pass": {k: _stats(v) for k, v in per_store.items()},
        "samples_sec": per_call,
    }


def measure_snapshot(n: int) -> dict:
    from services import market_history
    ts = []
    now = time.time()
    for i in range(n):
        t = time.perf_counter()
        market_history.record_snapshot_from_ticker(
            f"BENCH-SNAP-{i % 7}", 0.42, spread=0.02, volume_24h=1234.0,
            close_time="2026-09-06T00:00:00Z", now=now + i)
        ts.append(time.perf_counter() - t)
    return _stats(ts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--n-claim", type=int, default=300)
    ap.add_argument("--n-snap", type=int, default=150)
    args = ap.parse_args()

    t_all = time.perf_counter()
    paths = _redirect_paths()
    picks = _pick_tickers()
    has_work = [t for t, _ in picks["signals_pending_by_ticker"]][:60]
    has_work += [t for t, _ in picks["rejected_candidates_pending_by_ticker"] if t not in has_work][:30]
    noop = [t for t in picks["recent_settled"] if t not in has_work][:90]

    result = {
        "generated_at": time.time(),
        "db_paths": paths,
        "copy_sizes_mb": {p.name: round(p.stat().st_size / 1048576, 1) for p in BENCH_DBS.glob("*.db")},
        "table_state": {
            "signals_unresolved_total": picks["signals_unresolved_total"],
            "rejected_candidates_unresolved_total": picks["rejected_candidates_unresolved_total"],
            "rejection_events_total_approx": picks["rejection_events_total_approx"],
            "rejection_events_update_plan": picks["rejection_events_update_plan"],
            "rejection_events_unresolved_top": picks["rejection_events_unresolved_by_ticker_top"][:10],
            "signals_pending_top": picks["signals_pending_by_ticker"][:10],
        },
        "claim_record": measure_claim_record(args.n_claim),
        "snapshot_from_ticker": measure_snapshot(args.n_snap),
        "resolve_one_sync_has_work": measure_resolve_one_sync(has_work, "tickers with pending signal/candidate rows"),
        "resolve_one_sync_recent_settled": measure_resolve_one_sync(noop, "recently settled tickers (mostly already resolved)"),
    }
    result["elapsed_sec"] = time.perf_counter() - t_all
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=1, default=str)

    def fmt(s):
        return (f"n={s['n']} mean={s['mean_sec']*1000:.1f}ms p50={s['p50_sec']*1000:.1f}ms "
                f"p95={s['p95_sec']*1000:.1f}ms p99={s['p99_sec']*1000:.1f}ms max={s['max_sec']*1000:.1f}ms")

    print("claim:            ", fmt(result["claim_record"]["claim"]))
    print("record_decision:  ", fmt(result["claim_record"]["record_decision"]))
    print("snapshot_from_tkr:", fmt(result["snapshot_from_ticker"]))
    for key in ("resolve_one_sync_has_work", "resolve_one_sync_recent_settled"):
        r = result[key]
        print(f"{key}: ", fmt(r["resolve_one_sync"]),
              f"rows_resolved={r['rows_resolved_total']} nonzero_calls={r['rows_resolved_nonzero_calls']}")
        for store, s in r["per_store_fixed_cost_second_pass"].items():
            print(f"    {store:16s}", fmt(s))
    print("table_state:", json.dumps(result["table_state"], default=str)[:1500])
    print(f"elapsed {result['elapsed_sec']:.1f}s -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
