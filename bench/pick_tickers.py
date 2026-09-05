"""Pick disjoint sets of real tickers from the candidate_log.db copy for the
#601 fix-family benchmarks, so every destructive (UPDATE) experiment touches
its own tickers and none interfere with each other or with the read-only
scan measurements. Read-only against the copy (mode=ro) - writes nothing.
"""
import json
import sqlite3

OUT = "/tmp/bench_dbs/ticker_samples.json"


def main() -> None:
    c = sqlite3.connect("file:/tmp/bench_dbs/candidate_log.db?mode=ro", uri=True)
    # Real unresolved tickers, most-recently-rejected first isn't needed -
    # just need distinct real tickers with exactly-known row counts.
    rows = c.execute(
        "SELECT ticker, COUNT(*) c FROM rejected_candidates WHERE resolved = 0 "
        "GROUP BY ticker ORDER BY ticker LIMIT 2000"
    ).fetchall()
    c.close()
    tickers = [t for t, _ in rows]
    counts = {t: cnt for t, cnt in rows}
    assert len(tickers) >= 1000, f"only found {len(tickers)} distinct unresolved tickers"

    # Disjoint slices, 100-200 each, for each destructive/measurement bucket.
    buckets = {
        "section3_update_real": tickers[0:200],
        "section3_update_nonexistent": [f"BENCH-NOPE-{i}" for i in range(100)],
        "section4a_baseline_N50": tickers[200:250],
        "section4b_batch_real_N50": tickers[250:300],
        "section4c_family2_N50": tickers[300:350],
        "section4d_family2plus3_txn_N50": tickers[350:400],
        "section4e_family2plus3_percommit_N50": tickers[400:450],
        "section1_2_scan_noop_ticker": "BENCH-SCAN-PROBE-NONEXISTENT",
    }
    out = {
        "buckets": buckets,
        "row_counts": {t: counts[t] for t in tickers[0:450]},
        "total_distinct_unresolved": len(tickers),
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1)
    print(f"wrote {OUT}: {len(tickers)} candidate tickers scanned, "
          f"buckets sized {[ (k, len(v) if isinstance(v, list) else 1) for k, v in buckets.items() ]}")


if __name__ == "__main__":
    main()
