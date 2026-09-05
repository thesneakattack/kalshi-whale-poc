import sqlite3

c = sqlite3.connect("file:/tmp/bench_dbs/candidate_log.db?mode=ro", uri=True)

print("=== current query (no ticker filter) ===")
print(c.execute(
    "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0"
).fetchall())

print("\n=== ticker-scoped query, NO new index (does existing PK help?) ===")
print(c.execute(
    "EXPLAIN QUERY PLAN SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0 AND ticker = ?",
    ("X",),
).fetchall())

print("\n=== direct UPDATE ... WHERE ticker=? AND resolved=0 (Family 2 shape), no new index ===")
print(c.execute(
    "EXPLAIN QUERY PLAN UPDATE rejected_candidates SET resolved=1, result=?, resolved_at=? "
    "WHERE ticker=? AND resolved=0",
    ("yes", 0.0, "X"),
).fetchall())

print("\n=== rows per ticker distribution (avg / max) ===")
print("avg rows/ticker (unresolved):", c.execute(
    "SELECT CAST(COUNT(*) AS REAL) / COUNT(DISTINCT ticker) FROM rejected_candidates WHERE resolved=0"
).fetchone())
print("max rows for one ticker (unresolved):", c.execute(
    "SELECT ticker, COUNT(*) c FROM rejected_candidates WHERE resolved=0 GROUP BY ticker ORDER BY c DESC LIMIT 5"
).fetchall())

print("\n=== sqlite_master indexes actually present today ===")
print(c.execute(
    "SELECT name, sql FROM sqlite_master WHERE tbl_name='rejected_candidates'"
).fetchall())
c.close()
