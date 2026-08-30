"""
Per-store row count and last-write age for GET /api/health/pipeline, at a
cost that does not grow with the store.

Why this module exists (issue #210, 2026-08-30). The route used to run, per
store, from inside an `async def` and with no thread hop:

    with sqlite3.connect(db_path) as conn:
        n, last = conn.execute(f"SELECT COUNT(*), MAX({col}) FROM {table}").fetchone()

Measured against the live stores that same day:

  * SQLite has no O(1) `COUNT(*)`; it counts b-tree entries. On
    data/series_watcher.db (22.3 GB) `SELECT COUNT(*) FROM raw_trades`
    took **70.5 s** for 30,787,297 rows. index_ticks (1.07M rows) took
    0.61 s - about 2.3 us/row on a cold page cache.
  * `MAX(col)` is only cheap when an index *leads* with col. None of the
    capture stores index their timestamp first (raw_trades has
    `(ticker, observed_at)` and `(series, observed_at)`), so the aggregate
    scans a whole covering index: `SELECT MAX(observed_at) FROM raw_trades`
    took **7.5 s**.
  * Both ran on the event loop, so that was ~78 s during which the trading
    loop, the WebSocket readers and every other request were stopped.
    `last_tick_duration_sec` reached 81.3 s while the endpoint was polled -
    the diagnostic was degrading the app it measures.

The fix keeps every metric and changes how it is obtained. `MAX(rowid)` is
a single b-tree seek to the rightmost entry (measured 0.1 ms on the 30.8M
row table), and the newest rows by rowid are reachable in the same one
seek. So above a row limit the probe answers from the rowid index and says,
in the payload, that it did - an approximation labelled as approximate is
fine here, an approximation presented as exact is the bug this repo already
shipped twice. Below the limit nothing changes: exact COUNT(*), exact
MAX(col), labelled exact. `exact=True` (the route's `?exact_rows=true`)
forces the exact form everywhere, so the expensive answer is still
available on demand rather than deleted.

Connections are opened read-only (`mode=ro`) and closed. The old
`with sqlite3.connect(...)` was a *transaction* context manager, not a
closing one, so the endpoint leaked one connection per store per request,
against a read-write handle on live capture DBs.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

# Above this many rowids the probe stops issuing O(n) aggregates.
# Basis (2026-08-30, cold page cache): a full covering-index scan costs
# ~2.3 us/row - 30.79M rows took 70.5 s, 1.65M rows took 0.61 s. At
# 2,000,000 the worst case an exact probe can cost is ~4.6 s cold and
# ~0.1 s warm, and every store except raw_trades stays on the exact path
# today. Raise it only against a fresh measurement, not a hunch.
EXACT_ROW_LIMIT = 2_000_000

# How many of the newest rows (by rowid) the approximate last-write probe
# reads. Wide enough that a store written by several concurrent producers
# still has its newest timestamp inside the window; measured at 0.6 ms on
# raw_trades.
RECENT_WINDOW = 5_000

_APPROX_ROWS_NOTE = (
    "approximate: MAX(rowid), the highest rowid ever assigned. An upper bound on the "
    "current row count - deleted and replaced rows keep their rowid spent. Exact for an "
    "append-only store. Ask for ?exact_rows=true to pay for a real COUNT(*)."
)
_APPROX_LAST_WRITE_NOTE = (
    "approximate: the newest timestamp among the last {window} rows by rowid, not a "
    "table-wide MAX. Exact whenever the most recent write is also the most recent rowid, "
    "which is every append-only store; a store that UPDATEs old rows in place can hide a "
    "newer timestamp behind an older rowid."
)


def _read_only_uri(db_path) -> str:
    return Path(db_path).resolve().as_uri() + "?mode=ro"


def store_stats(
    db_path,
    table: str,
    col: str,
    now: float,
    *,
    exact: bool = False,
    row_limit: int = EXACT_ROW_LIMIT,
    recent_window: int = RECENT_WINDOW,
) -> dict:
    """Row count and last-write age for one store, with the method used.

    Always returns `rows`, `rows_exact`, `rows_method`, `last_write_sec_ago`,
    `last_write_exact`, `last_write_method` and `probe_ms`, or `error` plus
    `probe_ms` if the store cannot be read - a missing or unreadable store
    stays an explicit error, never a fabricated zero. `rows_note` /
    `last_write_note` appear only on the approximate path, so the label
    travels with the value that needs it.

    Blocking sqlite3: call it through `asyncio.to_thread`, never from a
    coroutine directly. That is half of what #210 was.
    """
    started = time.perf_counter()
    conn = None
    try:
        conn = sqlite3.connect(_read_only_uri(db_path), uri=True)
        high = conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0]
        cheap = not exact and high is not None and high > row_limit
        if cheap:
            out = {
                "rows": high,
                "rows_exact": False,
                "rows_method": "max_rowid",
                "rows_note": _APPROX_ROWS_NOTE,
                "last_write_method": "recent_rowid_window",
                "last_write_exact": False,
                "last_write_note": _APPROX_LAST_WRITE_NOTE.format(window=recent_window),
            }
            last = conn.execute(
                f"SELECT MAX({col}) FROM (SELECT {col} FROM {table} ORDER BY rowid DESC LIMIT ?)",
                (recent_window,),
            ).fetchone()[0]
        else:
            out = {
                "rows": conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                "rows_exact": True,
                "rows_method": "count",
                "last_write_method": "max",
                "last_write_exact": True,
            }
            last = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()[0]
        out["last_write_sec_ago"] = round(now - last, 1) if last else None
    except Exception as exc:
        out = {"error": str(exc)}
    finally:
        if conn is not None:
            conn.close()
    out["probe_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
    return out
