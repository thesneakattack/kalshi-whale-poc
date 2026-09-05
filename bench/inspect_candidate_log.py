import sqlite3

c = sqlite3.connect("file:/tmp/bench_dbs/candidate_log.db?mode=ro", uri=True)
print("schema rejected_candidates:", c.execute(
    "SELECT sql FROM sqlite_master WHERE tbl_name='rejected_candidates' AND type IN ('table','index')"
).fetchall())
print()
print("unresolved rejected_candidates:", c.execute(
    "SELECT COUNT(*) FROM rejected_candidates WHERE resolved=0").fetchone())
print("total rejected_candidates:", c.execute(
    "SELECT COUNT(*) FROM rejected_candidates").fetchone())
print("total rejection_events (approx via MAX rowid):", c.execute(
    "SELECT MAX(rowid) FROM rejection_events").fetchone())
print("unresolved rejection_events:", c.execute(
    "SELECT COUNT(*) FROM rejection_events WHERE resolved=0").fetchone())
print()
print("EXPLAIN current SELECT:", c.execute(
    "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0").fetchall())
print("distinct tickers unresolved:", c.execute(
    "SELECT COUNT(DISTINCT ticker) FROM rejected_candidates WHERE resolved=0").fetchone())
print("distinct tickers total:", c.execute(
    "SELECT COUNT(DISTINCT ticker) FROM rejected_candidates").fetchone())
print()
print("signals schema:")
c2 = sqlite3.connect("file:/tmp/bench_dbs/signal_log.db?mode=ro", uri=True)
print(c2.execute(
    "SELECT sql FROM sqlite_master WHERE tbl_name='signals' AND type IN ('table','index')").fetchall())
print("EXPLAIN signal_log query:", c2.execute(
    "EXPLAIN QUERY PLAN SELECT id, side FROM signals WHERE ticker = ? AND resolved = 0",
    ("X",)).fetchall())
print("total signals:", c2.execute("SELECT COUNT(*) FROM signals").fetchone())
print("unresolved signals:", c2.execute("SELECT COUNT(*) FROM signals WHERE resolved=0").fetchone())
c2.close()
c.close()
