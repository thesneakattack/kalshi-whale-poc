"""Issue #601 fix-family comparison - Part 2: correctness (old vs new,
byte-for-byte on a synthetic fixture covering every edge case) and failure
isolation (a deterministic fault injection, not a hand-waved description).

Uses the REAL DDL constants from services/capture_writer.py (never
hand-retyped) so the fixture schema cannot drift from the real one. Runs
entirely against throwaway temp files - never data/candidate_log.db, never
the bench_dbs copies used by the timing benchmark.

SCOPE NOTE on Section 6 (added per PR #603's adversarial review): the
_Unbindable fault-injection models a per-row DATA-poisoning failure
(deterministic, isolated to one parameter's bind step) - a fair stand-in
for settlement_resolver.py's own docstring example ("a locked store DB"),
since a lock-timeout OperationalError also fails atomically per-statement
and clears on retry, same shape as this injection. It does NOT model a
resource-exhaustion failure (disk full, or a long-held lock spanning
several consecutive tickers' worth of wall-clock time) that could fail
MULTIPLE consecutive tickers at once even under the per-ticker-commit
variant - that failure mode isn't isolated by ANY of the families compared
here, including today's shipped code, and is out of this benchmark's scope.
Read the isolation numbers below as "isolated against single-row data
faults," not "isolated against every real production failure mode."
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

WT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WT))
FIXTURE_DIR = Path("/tmp/bench_dbs/fixtures")


def _build_fixture(path: Path) -> None:
    from services import capture_writer
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute(capture_writer.REJECTED_CANDIDATES_DDL_SQL)
    conn.execute(capture_writer.REJECTION_EVENTS_DDL_SQL)
    now = 1000.0
    # rejected_candidates: (ticker, strategy, gate_name, observed_value,
    # threshold_value, side, rejected_at, resolved, result, resolved_at, unit_cost)
    rows = [
        # A: two rows (different gates), both unresolved -> both should resolve "yes"
        ("TICK-A", "strat1", "gate1", 0.5, 0.6, "yes", now, 0, None, None, 0.5),
        ("TICK-A", "strat1", "gate2", 0.7, 0.6, "yes", now, 0, None, None, 0.5),
        # B: already resolved -> must stay untouched even though market_results has B
        ("TICK-B", "strat1", "gate1", 0.5, 0.6, "no", now, 1, "yes", 999.0, 0.4),
        # C: unresolved, market_results gives "" (non-binary) -> must stay unresolved
        ("TICK-C", "strat1", "gate1", 0.5, 0.6, "yes", now, 0, None, None, 0.5),
        # D: unresolved, ticker absent from market_results entirely -> must stay unresolved
        ("TICK-D", "strat1", "gate1", 0.5, 0.6, "yes", now, 0, None, None, 0.5),
        # E: unresolved, resolves "no"
        ("TICK-E", "strat1", "gate1", 0.5, 0.6, "no", now, 0, None, None, 0.5),
    ]
    conn.executemany(
        "INSERT INTO rejected_candidates (ticker, strategy, gate_name, observed_value, "
        "threshold_value, side, rejected_at, resolved, result, resolved_at, unit_cost) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows,
    )
    # rejection_events: (id auto, ticker, strategy, gate_name, observed_value,
    # threshold_value, side, rejected_at, resolved, result, resolved_at, unit_cost)
    event_rows = [
        ("TICK-A", "strat1", "gate1", 0.5, 0.6, "yes", now, 0, None, None, 0.5),
        ("TICK-A", "strat1", "gate2", 0.7, 0.6, "yes", now, 0, None, None, 0.5),
        ("TICK-A", "strat1", "gate1", 0.5, 0.6, "yes", now + 1, 0, None, None, 0.5),  # 2nd A/gate1 event
        ("TICK-B", "strat1", "gate1", 0.5, 0.6, "no", now, 1, "yes", 999.0, 0.4),
        ("TICK-C", "strat1", "gate1", 0.5, 0.6, "yes", now, 0, None, None, 0.5),
        ("TICK-D", "strat1", "gate1", 0.5, 0.6, "yes", now, 0, None, None, 0.5),
        ("TICK-E", "strat1", "gate1", 0.5, 0.6, "no", now, 0, None, None, 0.5),
    ]
    conn.executemany(
        "INSERT INTO rejection_events (ticker, strategy, gate_name, observed_value, "
        "threshold_value, side, rejected_at, resolved, result, resolved_at, unit_cost) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", event_rows,
    )
    conn.commit()
    conn.close()


def _dump_state(path: Path) -> dict:
    conn = sqlite3.connect(path)
    rc = conn.execute(
        "SELECT ticker, strategy, gate_name, resolved, result FROM rejected_candidates "
        "ORDER BY ticker, strategy, gate_name"
    ).fetchall()
    re_ = conn.execute(
        "SELECT ticker, strategy, gate_name, rejected_at, resolved, result FROM rejection_events "
        "ORDER BY ticker, strategy, gate_name, rejected_at"
    ).fetchall()
    conn.close()
    return {"rejected_candidates": rc, "rejection_events": re_}


MARKET_RESULTS = {"TICK-A": "yes", "TICK-B": "yes", "TICK-C": "", "TICK-E": "no"}
# TICK-D deliberately absent from market_results.


def run_old(fixture_path: Path) -> tuple[dict, int]:
    """The REAL, unmodified services.candidate_log.resolve_from_market_results."""
    from services import candidate_log, capture_writer
    candidate_log.DB_PATH = fixture_path
    capture_writer._STORE_PATHS["rejected_candidates"] = fixture_path
    capture_writer._STORE_PATHS["rejection_events"] = fixture_path
    n = candidate_log.resolve_from_market_results(MARKET_RESULTS)
    return _dump_state(fixture_path), n


def run_new_percommit(fixture_path: Path) -> tuple[dict, int]:
    """Family 2+3 prototype: ticker-scoped direct UPDATE, one shared
    connection, per-ticker commit (preserves per-ticker isolation)."""
    now = time.time()
    conn = sqlite3.connect(fixture_path)
    total = 0
    for ticker, result in MARKET_RESULTS.items():
        result = (result or "").strip().lower()
        if result not in ("yes", "no"):
            continue
        cur = conn.execute(
            "UPDATE rejected_candidates SET resolved=1, result=?, resolved_at=? "
            "WHERE ticker=? AND resolved=0", (result, now, ticker),
        )
        conn.execute(
            "UPDATE rejection_events SET resolved=1, result=?, resolved_at=? "
            "WHERE ticker=? AND resolved=0", (result, now, ticker),
        )
        conn.commit()
        total += cur.rowcount
    conn.close()
    return _dump_state(fixture_path), total


def run_new_onetxn(fixture_path: Path) -> tuple[dict, int]:
    """Family 2+3 prototype: ticker-scoped direct UPDATE via executemany,
    single transaction for the whole batch."""
    now = time.time()
    conn = sqlite3.connect(fixture_path)
    params = [(r.strip().lower(), now, t) for t, r in MARKET_RESULTS.items()
              if (r or "").strip().lower() in ("yes", "no")]
    cur = conn.executemany(
        "UPDATE rejected_candidates SET resolved=1, result=?, resolved_at=? "
        "WHERE ticker=? AND resolved=0", params,
    )
    total = cur.rowcount
    conn.executemany(
        "UPDATE rejection_events SET resolved=1, result=?, resolved_at=? "
        "WHERE ticker=? AND resolved=0", params,
    )
    conn.commit()
    conn.close()
    return _dump_state(fixture_path), total


def section5_correctness() -> dict:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    variants = {"old": run_old, "new_percommit": run_new_percommit, "new_onetxn": run_new_onetxn}
    results = {}
    for name, fn in variants.items():
        path = FIXTURE_DIR / f"fixture_{name}.db"
        _build_fixture(path)
        state, n = fn(path)
        results[name] = {"state": state, "resolved_count_returned": n}

    old_state = results["old"]["state"]
    mismatches = {}
    for name in ("new_percommit", "new_onetxn"):
        diffs = {}
        for table in ("rejected_candidates", "rejection_events"):
            if results[name]["state"][table] != old_state[table]:
                diffs[table] = {"old": old_state[table], name: results[name]["state"][table]}
        if results[name]["resolved_count_returned"] != results["old"]["resolved_count_returned"]:
            diffs["resolved_count_returned"] = {
                "old": results["old"]["resolved_count_returned"],
                name: results[name]["resolved_count_returned"],
            }
        mismatches[name] = diffs
    return {
        "old_state": old_state,
        "old_resolved_count_returned": results["old"]["resolved_count_returned"],
        "mismatches": mismatches,
        "identical_to_old": {name: (mismatches[name] == {}) for name in mismatches},
    }


class _Unbindable:
    """An object sqlite3 cannot bind as a parameter - deliberately used to
    make exactly one ticker's UPDATE raise sqlite3.InterfaceError, so the
    batch/no-batch failure-isolation difference can be demonstrated
    deterministically instead of via flaky real file-locking timing."""
    pass


def section6_failure_isolation(n_tickers: int = 50, poison_index: int = 24) -> dict:
    """50 distinct tickers, one poisoned result value (an unbindable object)
    at a fixed position. Runs the same poisoned batch through both the
    single-transaction (Family 2+3, one txn) and per-ticker-commit
    (Family 2+3, per-commit) prototypes and reports how many tickers ended
    up resolved in each - the concrete, measured difference in blast
    radius."""
    tickers = [f"TICK-FAIL-{i}" for i in range(n_tickers)]
    from services import capture_writer
    now_base = 2000.0

    def build(path: Path) -> None:
        _build_fixture(path)  # reuse base rows, then add the fail-test rows
        conn = sqlite3.connect(path)
        rows = [(t, "strat1", "gate1", 0.5, 0.6, "yes", now_base, 0, None, None, 0.5) for t in tickers]
        conn.executemany(
            "INSERT INTO rejected_candidates (ticker, strategy, gate_name, observed_value, "
            "threshold_value, side, rejected_at, resolved, result, resolved_at, unit_cost) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
        conn.commit()
        conn.close()

    results_map = {t: "yes" for t in tickers}
    poisoned_ticker = tickers[poison_index]
    results_map[poisoned_ticker] = _Unbindable()  # will fail to bind

    out = {}

    # --- single transaction (executemany) ---
    path_txn = FIXTURE_DIR / "fixture_fail_onetxn.db"
    build(path_txn)
    conn = sqlite3.connect(path_txn)
    now = time.time()
    params = [(results_map[t] if not isinstance(results_map[t], _Unbindable) else results_map[t], now, t)
              for t in tickers]
    raised = None
    try:
        conn.executemany(
            "UPDATE rejected_candidates SET resolved=1, result=?, resolved_at=? "
            "WHERE ticker=? AND resolved=0", params,
        )
        conn.commit()
    except Exception as exc:
        raised = f"{type(exc).__name__}: {exc}"
        conn.rollback()
    resolved_after = conn.execute(
        "SELECT COUNT(*) FROM rejected_candidates WHERE ticker LIKE 'TICK-FAIL-%' AND resolved=1"
    ).fetchone()[0]
    conn.close()
    out["one_transaction_variant"] = {
        "exception_raised": raised,
        "n_tickers_in_batch": n_tickers,
        "n_resolved_after_failure": resolved_after,
        "expected_if_atomic_rollback": 0,
    }

    # --- per-ticker commit ---
    path_pc = FIXTURE_DIR / "fixture_fail_percommit.db"
    build(path_pc)
    conn = sqlite3.connect(path_pc)
    now = time.time()
    failures = []
    succeeded = []
    for t in tickers:
        result = results_map[t]
        try:
            cur = conn.execute(
                "UPDATE rejected_candidates SET resolved=1, result=?, resolved_at=? "
                "WHERE ticker=? AND resolved=0", (result, now, t),
            )
            conn.commit()
            succeeded.append(t)
        except Exception as exc:
            conn.rollback()
            failures.append({"ticker": t, "error": f"{type(exc).__name__}: {exc}"})
    resolved_after_pc = conn.execute(
        "SELECT COUNT(*) FROM rejected_candidates WHERE ticker LIKE 'TICK-FAIL-%' AND resolved=1"
    ).fetchone()[0]
    conn.close()
    out["per_ticker_commit_variant"] = {
        "n_tickers_in_batch": n_tickers,
        "n_resolved_after_failure": resolved_after_pc,
        "n_failures": len(failures),
        "failures": failures,
        "expected_if_isolated": n_tickers - 1,
    }
    return out


def main():
    result = {}
    result["section5_correctness"] = section5_correctness()
    print("=== Section 5: correctness (old vs new, synthetic fixture) ===")
    print("old resolved_count_returned:", result["section5_correctness"]["old_resolved_count_returned"])
    print("identical_to_old:", result["section5_correctness"]["identical_to_old"])
    if any(not v for v in result["section5_correctness"]["identical_to_old"].values()):
        print("MISMATCHES:", json.dumps(result["section5_correctness"]["mismatches"], indent=1, default=str))

    print("\n=== Section 6: failure isolation (deterministic fault injection, N=50, 1 poisoned) ===")
    result["section6_failure_isolation"] = section6_failure_isolation()
    print(json.dumps(result["section6_failure_isolation"], indent=1, default=str))

    os.makedirs("bench/out", exist_ok=True)
    with open("bench/out/correctness_and_failure_isolation.json", "w") as f:
        json.dump(result, f, indent=1, default=str)
    print("\nwrote bench/out/correctness_and_failure_isolation.json")


if __name__ == "__main__":
    main()
