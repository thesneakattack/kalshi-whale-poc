"""Follow-up on two surprising Section 2/3 results from
candidate_log_fix_families.py's first run:

1. The naive partial index (`ON rejected_candidates(resolved) WHERE
   resolved = 0`) measured SLOWER than no index at all for the unfiltered
   query, and the query planner picked that new index over the existing PK
   even for a ticker-scoped query, making the previously-fast path slower
   too. Confirm this is real (not a fluke) and check whether ANALYZE fixes
   the planner's choice, and whether a genuinely covering index
   `(resolved, ticker)` avoids the bookmark-lookup cost the plain
   `(resolved)` index still pays.
2. Independently verify sqlite3's executemany() rowcount accumulation
   for UPDATE statements against actual post-state row counts - never
   trust the Python docs' claim without checking it against this exact
   code path.

Runs against a FRESH work copy so it doesn't interact with the ticker
buckets already consumed by candidate_log_fix_families.py.
"""
import json
import os
import sqlite3
import time

WORK2 = "/tmp/bench_dbs/candidate_log_work2.db"


def section_a_covering_index_vs_plain(reps: int = 20) -> dict:
    """Compares three states of the SAME copy of PRISTINE (fresh, since
    Section 2 already mutated the main pristine copy's schema):
      (i) no custom index (baseline)
      (ii) plain partial index on (resolved) only
      (iii) covering partial index on (resolved, ticker)
    for both the unfiltered scan and a ticker-scoped lookup, with and
    without ANALYZE."""
    out = {}
    conn = sqlite3.connect(WORK2)

    def timed_scan(sql, params=()):
        times = []
        for _ in range(reps):
            t0 = time.perf_counter()
            conn.execute(sql, params).fetchall()
            times.append(time.perf_counter() - t0)
        return sum(times) / len(times) * 1000  # mean ms

    noop = "BENCH-A-FOLLOWUP-NOOP"
    real_ticker = conn.execute(
        "SELECT ticker FROM rejected_candidates WHERE resolved=0 LIMIT 1"
    ).fetchone()[0]

    out["0_no_index"] = {
        "plan_unfiltered": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0").fetchall(),
        "plan_ticker": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?",
            (real_ticker,)).fetchall(),
        "mean_ms_unfiltered": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0"),
        "mean_ms_ticker": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?", (real_ticker,)),
    }

    conn.execute("CREATE INDEX idx_plain ON rejected_candidates(resolved) WHERE resolved = 0")
    conn.commit()
    out["1_plain_partial_index_no_analyze"] = {
        "plan_unfiltered": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0").fetchall(),
        "plan_ticker": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?",
            (real_ticker,)).fetchall(),
        "mean_ms_unfiltered": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0"),
        "mean_ms_ticker": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?", (real_ticker,)),
    }

    conn.execute("ANALYZE rejected_candidates")
    conn.commit()
    out["2_plain_partial_index_after_analyze"] = {
        "plan_unfiltered": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0").fetchall(),
        "plan_ticker": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?",
            (real_ticker,)).fetchall(),
        "mean_ms_unfiltered": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0"),
        "mean_ms_ticker": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?", (real_ticker,)),
    }

    conn.execute("DROP INDEX idx_plain")
    conn.execute("CREATE INDEX idx_covering ON rejected_candidates(resolved, ticker) WHERE resolved = 0")
    conn.execute("ANALYZE rejected_candidates")
    conn.commit()
    out["3_covering_partial_index_after_analyze"] = {
        "plan_unfiltered": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0").fetchall(),
        "plan_ticker": conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?",
            (real_ticker,)).fetchall(),
        "mean_ms_unfiltered": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0"),
        "mean_ms_ticker": timed_scan(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved=0 AND ticker=?", (real_ticker,)),
    }
    conn.close()
    return out


def section_b_executemany_rowcount_ground_truth() -> dict:
    """Independently verifies executemany()'s rowcount for our exact UPDATE
    shape against a real post-state COUNT, on a small isolated slice (20
    fresh, never-touched real tickers) - not trusting the Python docs'
    claim about accumulation without checking it here."""
    conn = sqlite3.connect(WORK2)
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM rejected_candidates WHERE resolved=0 "
        "AND ticker LIKE 'K%' ORDER BY ticker DESC LIMIT 20").fetchall()]
    before_counts = {t: conn.execute(
        "SELECT COUNT(*) FROM rejected_candidates WHERE ticker=? AND resolved=0", (t,)
    ).fetchone()[0] for t in tickers}
    expected_total = sum(before_counts.values())

    now = time.time()
    params = [("yes", now, t) for t in tickers]
    cur = conn.executemany(
        "UPDATE rejected_candidates SET resolved=1, result=?, resolved_at=? WHERE ticker=? AND resolved=0",
        params,
    )
    reported_rowcount = cur.rowcount
    conn.commit()

    after_resolved_counts = {t: conn.execute(
        "SELECT COUNT(*) FROM rejected_candidates WHERE ticker=? AND resolved=1 AND result='yes'", (t,)
    ).fetchone()[0] for t in tickers}
    actual_total_resolved = sum(after_resolved_counts.values())
    remaining_unresolved = {t: conn.execute(
        "SELECT COUNT(*) FROM rejected_candidates WHERE ticker=? AND resolved=0", (t,)
    ).fetchone()[0] for t in tickers}
    conn.close()
    return {
        "tickers_tested": len(tickers),
        "before_counts_per_ticker": before_counts,
        "expected_total_from_pre_count": expected_total,
        "executemany_reported_rowcount": reported_rowcount,
        "actual_total_resolved_post_state": actual_total_resolved,
        "remaining_unresolved_should_be_zero": remaining_unresolved,
        "rowcount_matches_actual": reported_rowcount == actual_total_resolved == expected_total,
    }


def main():
    import shutil
    shutil.copy("/tmp/bench_dbs/candidate_log.db", WORK2)
    # candidate_log_fix_families.py's Section 2 already added
    # idx_rejected_candidates_unresolved to the PRISTINE file this is copied
    # from - drop it here so section_a's "0_no_index" baseline is genuinely
    # index-free, matching the real live DB's actual current schema.
    _conn = sqlite3.connect(WORK2)
    _conn.execute("DROP INDEX IF EXISTS idx_rejected_candidates_unresolved")
    _conn.commit()
    _conn.close()

    result = {}
    result["section_a_index_variants"] = section_a_covering_index_vs_plain()
    result["section_b_executemany_rowcount"] = section_b_executemany_rowcount_ground_truth()

    print(json.dumps(result["section_a_index_variants"], indent=1, default=str))
    print(json.dumps(result["section_b_executemany_rowcount"], indent=1, default=str))

    os.makedirs("bench/out", exist_ok=True)
    with open("bench/out/family1_followup.json", "w") as f:
        json.dump(result, f, indent=1, default=str)


if __name__ == "__main__":
    main()
