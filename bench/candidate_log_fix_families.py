"""Issue #601 - three competing fix families for candidate_log.py's
resolve_from_market_results unindexed-scan bottleneck, measured against a
real backup copy of data/candidate_log.db (bench/copy_dbs.py; never the
live file - see that script's own docstring for the single-step-backup
reasoning).

Sections:
  1. Baseline (today's shipped shape): SELECT ... WHERE resolved = 0, no
     ticker filter, via both raw SQL and the real, unmodified
     services.candidate_log.resolve_from_market_results().
  2. Family 1: CREATE INDEX ... ON rejected_candidates(resolved)
     WHERE resolved = 0 (a partial index) - same query, index-assisted.
  3. Ticker-scoped access: does WHERE ticker = ? need a NEW index at all,
     given the table's own PRIMARY KEY (ticker, strategy, gate_name)?
     (raw SELECT, then Family 2's direct UPDATE, with correctness checks
     on cursor.rowcount).
  4. Family 3: batching multiple due tickers' DB work into one call vs.
     N separate calls - four variants compared at a realistic batch size
     (50, matching main.py's real settlement_resolver.run_pending
     batch_size default, confirmed by reading main.py/settlement_resolver.py
     directly, not assumed).

Every write happens against /tmp/bench_dbs/candidate_log_work.db, a plain
`cp` of the read-only backup copy (bench/copy_dbs.py) - never the live
data/candidate_log.db, and never the pristine backup copy either (that one
stays read-only via mode=ro connections, used only for non-mutating scan
timings). Ticker sets are pre-partitioned and disjoint (bench/pick_tickers.py
-> /tmp/bench_dbs/ticker_samples.json) so no experiment's writes are visible
to another's timing.

This file measures. It does not modify services/candidate_log.py or any
other shipped file - every "Family 2/3" code path below is a bench-local
prototype function, not an edit to the real module.

KNOWN CAVEAT (found in self-review, not fixed by re-running - the schema
mutation itself is the point of Section 2, so a rerun would reproduce the
same ordering): Section 2 adds the Family-1 partial index to PRISTINE with
CREATE INDEX (schema-only, no row data touched) and never drops it again.
Section 3 then runs its ticker-scoped SELECT timing (section3_ticker_scoped_
select) against that SAME already-indexed PRISTINE file - so its
"real_ticker_stats"/"absent_ticker_stats" numbers (~47-51ms) reflect the
query planner's (bad) choice of the new Family-1 index over the table's own
PRIMARY KEY, NOT the "existing PK, no new index" baseline the section's
name/docstring claims. The uncontaminated number for that comparison
(existing PK, no Family-1 index present, ANALYZE not yet run) is
bench/family1_followup.py's "0_no_index" -> mean_ms_ticker: 0.038ms,
measured on a copy where the Family-1 index was explicitly dropped first.
Section 3's own UPDATE numbers (section3_family2_direct_update) are NOT
affected by this - they run against WORK, a separate copy made before
Section 2 ever touched PRISTINE. See the PR body / bench/out/README-shaped
summary for which number is authoritative for which claim.
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WT))

PRISTINE = "/tmp/bench_dbs/candidate_log.db"
WORK = "/tmp/bench_dbs/candidate_log_work.db"
SAMPLES_PATH = "/tmp/bench_dbs/ticker_samples.json"


def _stats(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0}
    s = sorted(samples)

    def pct(p):
        idx = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
        return s[idx]

    return {
        "n": len(s), "mean_ms": statistics.fmean(s) * 1000,
        "p50_ms": pct(50) * 1000, "p95_ms": pct(95) * 1000,
        "p99_ms": pct(99) * 1000, "max_ms": s[-1] * 1000, "min_ms": s[0] * 1000,
    }


def load_samples() -> dict:
    with open(SAMPLES_PATH) as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Section 1: baseline (today's shipped shape)
# --------------------------------------------------------------------------

def section1_baseline_raw_sql(reps: int, noop_ticker: str) -> dict:
    """Pure SQL cost of today's SELECT ... WHERE resolved = 0 (no ticker
    filter), against the pristine read-only copy. Repeated on a ticker with
    zero matching rows (BENCH-SCAN-PROBE-NONEXISTENT) so nothing mutates and
    every rep pays the identical full-scan cost - the exact point #601
    makes: the scan happens "regardless of whether the one ticker being
    checked has any rows at all"."""
    import sqlite3
    conn = sqlite3.connect(f"file:{PRISTINE}?mode=ro", uri=True)
    times = []
    for _ in range(reps):
        t = time.perf_counter()
        rows = conn.execute("SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0").fetchall()
        to_resolve = [r for r in rows if r[1] == noop_ticker]  # mirrors the Python filter step
        times.append(time.perf_counter() - t)
    conn.close()
    return {"stats": _stats(times), "rows_scanned_per_call": len(rows), "matched": len(to_resolve)}


def section1_baseline_real_function(reps: int, noop_ticker: str) -> dict:
    """The real, unmodified services.candidate_log.resolve_from_market_results,
    DB_PATH redirected to the pristine copy, called with a single nonexistent
    ticker each time (same "zero rows match, pay the scan anyway" case as
    section1_baseline_raw_sql, but through the actual shipped code path -
    flush_now x2, _connect()'s DDL, the Python filter loop, the executemany
    guard)."""
    from services import candidate_log, capture_writer
    candidate_log.DB_PATH = Path(PRISTINE)
    capture_writer._STORE_PATHS["rejected_candidates"] = Path(PRISTINE)
    capture_writer._STORE_PATHS["rejection_events"] = Path(PRISTINE)
    times = []
    resolved_counts = []
    for _ in range(reps):
        t = time.perf_counter()
        n = candidate_log.resolve_from_market_results({noop_ticker: "yes"})
        times.append(time.perf_counter() - t)
        resolved_counts.append(n)
    return {"stats": _stats(times), "resolved_counts": resolved_counts}


# --------------------------------------------------------------------------
# Section 2: Family 1 - partial index
# --------------------------------------------------------------------------

def section2_add_partial_index_and_replan() -> dict:
    """Adds the partial index to the PRISTINE copy (schema-only mutation,
    touches no row data - CREATE INDEX doesn't change `resolved`/`result`/
    `resolved_at` for any row) and captures the query plan SQLite actually
    picks afterward, since 89% of rows are unresolved=0 (measured in
    Section 0 below) - a partial index on a non-selective predicate is not
    guaranteed to be chosen by the planner, and must be checked, not
    assumed."""
    import sqlite3
    conn = sqlite3.connect(PRISTINE)  # read-write: DDL only, no data touched
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rejected_candidates_unresolved "
        "ON rejected_candidates(resolved) WHERE resolved = 0"
    )
    conn.commit()
    plan_no_ticker = conn.execute(
        "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0"
    ).fetchall()
    plan_ticker = conn.execute(
        "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0 AND ticker = ?",
        ("X",),
    ).fetchall()
    conn.close()
    return {"plan_no_ticker_filter": plan_no_ticker, "plan_with_ticker_filter": plan_ticker}


# section1_baseline_raw_sql / section1_baseline_real_function are reused
# post-index by the caller (same functions, same pristine DB, now indexed).


# --------------------------------------------------------------------------
# Section 3: ticker-scoped access - existing PK vs. Family 2's direct UPDATE
# --------------------------------------------------------------------------

def section3_ticker_scoped_select(reps_real: list[str], reps_nonexistent: list[str]) -> dict:
    """Read-only: SELECT ... WHERE resolved = 0 AND ticker = ?, against the
    PRISTINE copy (mode=ro - guaranteed no mutation), for real tickers with
    rows and guaranteed-absent ones. No new index involved - this is
    whatever query plan the table's existing PRIMARY KEY (ticker, strategy,
    gate_name) gives it today."""
    import sqlite3
    conn = sqlite3.connect(f"file:{PRISTINE}?mode=ro", uri=True)
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates "
        "WHERE resolved = 0 AND ticker = ?", ("X",)
    ).fetchall()
    times_real, times_absent = [], []
    for t in reps_real:
        t0 = time.perf_counter()
        conn.execute(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0 AND ticker = ?", (t,)
        ).fetchall()
        times_real.append(time.perf_counter() - t0)
    for t in reps_nonexistent:
        t0 = time.perf_counter()
        conn.execute(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0 AND ticker = ?", (t,)
        ).fetchall()
        times_absent.append(time.perf_counter() - t0)
    conn.close()
    return {"query_plan": plan, "real_ticker_stats": _stats(times_real), "absent_ticker_stats": _stats(times_absent)}


def section3_family2_direct_update(real_tickers: list[str], nonexistent_tickers: list[str],
                                    expected_counts: dict) -> dict:
    """Family 2's mechanism: UPDATE rejected_candidates SET resolved=1,
    result=?, resolved_at=? WHERE ticker=? AND resolved=0 - no SELECT, no
    Python row materialization. Runs against the WORK copy (mutates - each
    ticker here is used exactly once across the whole benchmark, see
    bench/pick_tickers.py's disjoint buckets). Verifies cursor.rowcount
    against an independently-queried expected count for every real ticker,
    and checks it is exactly 0 for every nonexistent one - the concrete
    "does rowcount actually behave correctly here" check the task calls
    for, not assumed from the sqlite3 docs."""
    import sqlite3
    conn = sqlite3.connect(WORK)
    now = time.time()
    times_real, times_absent = [], []
    rowcount_mismatches = []
    for t in real_tickers:
        t0 = time.perf_counter()
        cur = conn.execute(
            "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? "
            "WHERE ticker = ? AND resolved = 0",
            ("yes", now, t),
        )
        conn.commit()
        times_real.append(time.perf_counter() - t0)
        if cur.rowcount != expected_counts.get(t):
            rowcount_mismatches.append({"ticker": t, "rowcount": cur.rowcount, "expected": expected_counts.get(t)})
    for t in nonexistent_tickers:
        t0 = time.perf_counter()
        cur = conn.execute(
            "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? "
            "WHERE ticker = ? AND resolved = 0",
            ("yes", now, t),
        )
        conn.commit()
        times_absent.append(time.perf_counter() - t0)
        if cur.rowcount != 0:
            rowcount_mismatches.append({"ticker": t, "rowcount": cur.rowcount, "expected": 0})
    conn.close()
    return {
        "real_ticker_stats": _stats(times_real),
        "absent_ticker_stats": _stats(times_absent),
        "rowcount_mismatches": rowcount_mismatches,
    }


# --------------------------------------------------------------------------
# Section 4: Family 3 - batching multiple due tickers into one call
# --------------------------------------------------------------------------

def section4a_baseline_N_separate_calls(tickers: list[str]) -> dict:
    """Today's real shape: settlement_resolver.py calls
    resolve_from_market_results({ticker: result}) once per due ticker,
    sequentially. Uses the REAL shipped function (DB_PATH -> WORK copy),
    each of the N tickers here used exactly once."""
    from services import candidate_log, capture_writer
    candidate_log.DB_PATH = Path(WORK)
    capture_writer._STORE_PATHS["rejected_candidates"] = Path(WORK)
    capture_writer._STORE_PATHS["rejection_events"] = Path(WORK)
    t0 = time.perf_counter()
    resolved_total = 0
    per_call = []
    for t in tickers:
        tc = time.perf_counter()
        resolved_total += candidate_log.resolve_from_market_results({t: "yes"})
        per_call.append(time.perf_counter() - tc)
    total = time.perf_counter() - t0
    return {"total_sec": total, "resolved_total": resolved_total, "per_call_stats": _stats(per_call),
            "n_tickers": len(tickers)}


def section4b_batch_one_call_existing_function(tickers: list[str], results: dict) -> dict:
    """Family 3, ZERO code change to candidate_log.py: the real shipped
    resolve_from_market_results() already accepts an arbitrary-size dict -
    it is only settlement_resolver.py's call site that currently passes it
    one ticker at a time. This calls the SAME real function ONCE with all N
    (ticker, result) pairs, proving the batching redesign can live entirely
    in settlement_resolver.py's loop structure."""
    from services import candidate_log, capture_writer
    candidate_log.DB_PATH = Path(WORK)
    capture_writer._STORE_PATHS["rejected_candidates"] = Path(WORK)
    capture_writer._STORE_PATHS["rejection_events"] = Path(WORK)
    market_results = {t: results.get(t, "yes") for t in tickers}
    t0 = time.perf_counter()
    resolved_total = candidate_log.resolve_from_market_results(market_results)
    total = time.perf_counter() - t0
    return {"total_sec": total, "resolved_total": resolved_total, "n_tickers": len(tickers)}


def _family2_single_ticker_call(ticker: str, result: str, now: float) -> int:
    """Bench-local prototype of "Family 2 alone": same per-call shape as
    today (own flush_now x2, own connect, own commit) but the SQL is the
    direct ticker-scoped UPDATE instead of SELECT-all-unresolved. This is
    the minimal-diff version of Family 2: only candidate_log.py's SQL
    changes, settlement_resolver.py's per-ticker loop stays exactly as-is."""
    import sqlite3
    from services import capture_writer
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    conn = sqlite3.connect(WORK)
    cur = conn.execute(
        "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? "
        "WHERE ticker = ? AND resolved = 0",
        (result, now, ticker),
    )
    n = cur.rowcount
    conn.execute(
        "UPDATE rejection_events SET resolved = 1, result = ?, resolved_at = ? "
        "WHERE ticker = ? AND resolved = 0",
        (result, now, ticker),
    )
    conn.commit()
    conn.close()
    return n


def section4c_family2_N_separate_calls(tickers: list[str]) -> dict:
    """Family 2 alone: N separate calls (settlement_resolver.py's loop is
    UNCHANGED), but each call's SQL is the ticker-scoped direct UPDATE
    instead of the full unindexed SELECT."""
    from services import capture_writer
    capture_writer._STORE_PATHS["rejected_candidates"] = Path(WORK)
    capture_writer._STORE_PATHS["rejection_events"] = Path(WORK)
    now = time.time()
    t0 = time.perf_counter()
    resolved_total = 0
    per_call = []
    for t in tickers:
        tc = time.perf_counter()
        resolved_total += _family2_single_ticker_call(t, "yes", now)
        per_call.append(time.perf_counter() - tc)
    total = time.perf_counter() - t0
    return {"total_sec": total, "resolved_total": resolved_total, "per_call_stats": _stats(per_call),
            "n_tickers": len(tickers)}


def section4d_family2plus3_one_txn(tickers: list[str], results: dict) -> dict:
    """Family 2 + Family 3 combined, single transaction: ONE flush_now x2,
    ONE connect(), then executemany() of the ticker-scoped UPDATE across all
    N tickers, committed once. Fastest possible shape - but see Section 6
    for what this does to per-ticker failure isolation (a single connection-
    level failure mid-executemany rolls back the WHOLE batch, not just the
    poisoned ticker)."""
    import sqlite3
    from services import capture_writer
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    now = time.time()
    conn = sqlite3.connect(WORK)
    t0 = time.perf_counter()
    params = [(results.get(t, "yes"), now, t) for t in tickers]
    cur = conn.executemany(
        "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? "
        "WHERE ticker = ? AND resolved = 0", params,
    )
    resolved_total = cur.rowcount  # verified below whether executemany accumulates rowcount
    conn.executemany(
        "UPDATE rejection_events SET resolved = 1, result = ?, resolved_at = ? "
        "WHERE ticker = ? AND resolved = 0", params,
    )
    conn.commit()
    total = time.perf_counter() - t0
    conn.close()
    return {"total_sec": total, "resolved_total": resolved_total, "n_tickers": len(tickers)}


def section4e_family2plus3_percommit(tickers: list[str], results: dict) -> dict:
    """Family 2 + Family 3, per-ticker commits inside one shared connection:
    ONE flush_now x2, ONE connect() (amortizing the fixed per-call overhead
    Family 3 targets), but each ticker's UPDATE still gets its own
    execute()+commit() and its own try/except - so one poisoned ticker's
    exception is caught and skipped without rolling back or blocking the
    others, preserving today's per-ticker isolation property while still
    amortizing flush_now/connect overhead across the whole batch."""
    import sqlite3
    from services import capture_writer
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    now = time.time()
    conn = sqlite3.connect(WORK)
    t0 = time.perf_counter()
    resolved_total = 0
    failures = []
    for t in tickers:
        try:
            result = results.get(t, "yes")
            cur = conn.execute(
                "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? "
                "WHERE ticker = ? AND resolved = 0", (result, now, t),
            )
            conn.execute(
                "UPDATE rejection_events SET resolved = 1, result = ?, resolved_at = ? "
                "WHERE ticker = ? AND resolved = 0", (result, now, t),
            )
            conn.commit()
            resolved_total += cur.rowcount
        except Exception as exc:
            failures.append({"ticker": t, "error": str(exc)})
    total = time.perf_counter() - t0
    conn.close()
    return {"total_sec": total, "resolved_total": resolved_total, "n_tickers": len(tickers), "failures": failures}


def main() -> None:
    samples = load_samples()
    buckets = samples["buckets"]
    row_counts = samples["row_counts"]
    noop_ticker = buckets["section1_2_scan_noop_ticker"]

    result = {"generated_at": time.time(), "total_distinct_unresolved_at_snapshot": samples["total_distinct_unresolved"]}

    # --- Section 0: ground truth (re-derived here, not trusted from the issue) ---
    import sqlite3
    conn = sqlite3.connect(f"file:{PRISTINE}?mode=ro", uri=True)
    unresolved = conn.execute("SELECT COUNT(*) FROM rejected_candidates WHERE resolved=0").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM rejected_candidates").fetchone()[0]
    result["section0_ground_truth"] = {
        "unresolved_rows": unresolved, "total_rows": total,
        "pct_unresolved": round(100 * unresolved / total, 1),
        "plan_before_index": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0").fetchall(),
    }
    conn.close()

    print("=== Section 1: baseline (today's shipped shape) ===")
    result["section1_baseline_raw_sql"] = section1_baseline_raw_sql(30, noop_ticker)
    print("raw SQL:", result["section1_baseline_raw_sql"]["stats"])
    result["section1_baseline_real_function"] = section1_baseline_real_function(30, noop_ticker)
    print("real function:", result["section1_baseline_real_function"]["stats"])

    print("\n=== Section 2: Family 1 - add partial index, re-measure ===")
    result["section2_index_plan"] = section2_add_partial_index_and_replan()
    print("plan after index (no ticker filter):", result["section2_index_plan"]["plan_no_ticker_filter"])
    result["section2_raw_sql_post_index"] = section1_baseline_raw_sql(30, noop_ticker)
    print("raw SQL post-index:", result["section2_raw_sql_post_index"]["stats"])
    result["section2_real_function_post_index"] = section1_baseline_real_function(30, noop_ticker)
    print("real function post-index:", result["section2_real_function_post_index"]["stats"])

    print("\n=== Section 3: ticker-scoped access (existing PK, no new index) ===")
    real_t = buckets["section3_update_real"]
    absent_t = buckets["section3_update_nonexistent"]
    result["section3_select"] = section3_ticker_scoped_select(real_t, absent_t)
    print("plan:", result["section3_select"]["query_plan"])
    print("real ticker SELECT:", result["section3_select"]["real_ticker_stats"])
    print("absent ticker SELECT:", result["section3_select"]["absent_ticker_stats"])

    expected_counts = {t: row_counts[t] for t in real_t}
    result["section3_family2_update"] = section3_family2_direct_update(real_t, absent_t, expected_counts)
    print("real ticker UPDATE:", result["section3_family2_update"]["real_ticker_stats"])
    print("absent ticker UPDATE:", result["section3_family2_update"]["absent_ticker_stats"])
    print("rowcount mismatches:", result["section3_family2_update"]["rowcount_mismatches"])

    print("\n=== Section 4: Family 3 - batching (N=50, matching main.py's real batch_size default) ===")
    result["section4a_baseline"] = section4a_baseline_N_separate_calls(buckets["section4a_baseline_N50"])
    print("4a baseline N-separate-calls total_sec:", result["section4a_baseline"]["total_sec"],
          "resolved:", result["section4a_baseline"]["resolved_total"])

    result["section4b_batch_existing_fn"] = section4b_batch_one_call_existing_function(
        buckets["section4b_batch_real_N50"], {})
    print("4b Family3-only (existing fn, 1 call) total_sec:", result["section4b_batch_existing_fn"]["total_sec"],
          "resolved:", result["section4b_batch_existing_fn"]["resolved_total"])

    result["section4c_family2_only"] = section4c_family2_N_separate_calls(buckets["section4c_family2_N50"])
    print("4c Family2-only (N calls, ticker-scoped SQL) total_sec:", result["section4c_family2_only"]["total_sec"],
          "resolved:", result["section4c_family2_only"]["resolved_total"])

    result["section4d_family2plus3_txn"] = section4d_family2plus3_one_txn(
        buckets["section4d_family2plus3_txn_N50"], {})
    print("4d Family2+3 (1 txn) total_sec:", result["section4d_family2plus3_txn"]["total_sec"],
          "resolved:", result["section4d_family2plus3_txn"]["resolved_total"])

    result["section4e_family2plus3_percommit"] = section4e_family2plus3_percommit(
        buckets["section4e_family2plus3_percommit_N50"], {})
    print("4e Family2+3 (per-ticker commit) total_sec:", result["section4e_family2plus3_percommit"]["total_sec"],
          "resolved:", result["section4e_family2plus3_percommit"]["resolved_total"],
          "failures:", result["section4e_family2plus3_percommit"]["failures"])

    out_path = "bench/out/candidate_log_fix_families.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=1, default=str)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
