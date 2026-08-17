"""
Non-destructive protection of the analytics dataset from deliberate test
windows.

Direct request (2026-08-17): "i want to protect against tests that corrupt
and keeping logs where valuable insights are gained pristine."

The problem this solves, from a real, measured incident earlier the same
day: `whale_watcher_kalshi.min_notional_usd_by_series.KXBTC15M` was dropped
to $1 for 28 minutes to probe end-to-end latency (watchlist → whale stream
→ position open → management). That is a legitimate experiment and it
worked - but it emitted 19,995 signals at 49.3% accuracy into the same
table the 30-day headline reads, dragging the displayed whale win rate from
its true **74.2%** down to **64.8%**, and with it every sample-size-gated
heuristic that reads signal_log (confidence_calibration, all_series_stats,
the min_whale_winrate_pct entry gate).

The only remedy available at the time was deletion, and it was used:
`data/reset_log.db` records 19,995 signal rows destroyed to move that
number back. It worked, but it traded away real history to fix a display
problem - directly against CLAUDE.md's "accumulated history is a
first-class asset, not disposable state."

This module makes that trade unnecessary. A quarantined range is:
  - **non-destructive** - rows stay, fully queryable, nothing is dropped
  - **reversible** - `release()` puts them straight back into the stats
  - **attributed** - the range carries a reason and a label, so a year from
    now "why is there a hole in 08-16?" has an answer on disk rather than
    in someone's memory
  - **opt-in** - the `excluded` column defaults to 0, so every existing row
    and every writer that doesn't know about this counts exactly as before

Two ways in:
  1. **Prospectively** - `start(label, reason)` before a test, `stop()`
     after. While active, `main.py` passes `excluded=True` into
     `signal_log.log_signal`, so test rows are born inert and no cleanup is
     ever needed.
  2. **Retroactively** - `quarantine(after, before, ...)` for a window you
     only recognised as a test afterwards, which is how the 08-16 case was
     actually found (by diffing `config_performance.applied_changes`
     against the signal timeline).

Own SQLite file, standard idiom (CLAUDE.md's persistence section).
"""
import sqlite3
import time
from pathlib import Path

from services import signal_log

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "quarantine.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quarantine_ranges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            reason TEXT,
            range_start REAL NOT NULL,
            range_end REAL,
            rows_marked INTEGER,
            created_at REAL NOT NULL,
            released_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_q_open ON quarantine_ranges (range_end, released_at)")
    return conn


def start(label: str, reason: str | None = None, now: float | None = None) -> dict:
    """Open an experiment window. Signals logged from here until stop() are
    written with excluded=1 and never enter the statistics.

    Deliberately does NOT stop the strategy from trading on them - the
    whole point of a latency test is that the full pipeline runs. It only
    changes whether the resulting rows count as evidence."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        open_row = conn.execute(
            "SELECT id, label FROM quarantine_ranges WHERE range_end IS NULL AND released_at IS NULL"
        ).fetchone()
        if open_row:
            return {"already_active": True, "id": open_row[0], "label": open_row[1]}
        cur = conn.execute(
            "INSERT INTO quarantine_ranges (label, reason, range_start, created_at) VALUES (?,?,?,?)",
            (label, reason, now, now),
        )
        return {"already_active": False, "id": cur.lastrowid, "label": label, "range_start": now}


def stop(now: float | None = None) -> dict:
    """Close the open experiment window and retroactively mark anything
    logged inside it - belt and braces. Rows written while active are
    already excluded=1 via the write path, but marking the closed range too
    means the window is still correctly excluded even if some writer didn't
    consult is_active() (a new call site, a background task holding a stale
    config)."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, range_start FROM quarantine_ranges WHERE range_end IS NULL AND released_at IS NULL"
        ).fetchone()
        if not row:
            return {"active": False}
        qid, start_ts = row
        marked = signal_log.mark_excluded_range(start_ts, now, excluded=True)
        conn.execute(
            "UPDATE quarantine_ranges SET range_end = ?, rows_marked = ? WHERE id = ?",
            (now, marked, qid),
        )
        return {"active": False, "id": qid, "range_start": start_ts, "range_end": now, "rows_marked": marked}


def is_active(now: float | None = None) -> bool:
    """Cheap enough to call on the hot signal path - one indexed lookup on
    a table that holds a handful of rows."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM quarantine_ranges WHERE range_end IS NULL AND released_at IS NULL LIMIT 1"
        ).fetchone()
    return row is not None


def quarantine(after: float, before: float, label: str, reason: str | None = None,
               now: float | None = None) -> dict:
    """Retroactively exclude an already-recorded window. This is the path
    used for a test you only recognise as one after the fact - e.g. by
    diffing config_performance.applied_changes against the signal
    timeline."""
    now = now if now is not None else time.time()
    marked = signal_log.mark_excluded_range(after, before, excluded=True)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO quarantine_ranges (label, reason, range_start, range_end, rows_marked, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (label, reason, after, before, marked, now),
        )
        return {"id": cur.lastrowid, "label": label, "range_start": after,
                "range_end": before, "rows_marked": marked}


def release(quarantine_id: int, now: float | None = None) -> dict:
    """Undo a quarantine - the rows go straight back into every statistic.
    This is what makes quarantining safe to do on a hunch: being wrong
    costs nothing, unlike deletion."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT range_start, range_end, released_at FROM quarantine_ranges WHERE id = ?",
            (quarantine_id,),
        ).fetchone()
        if not row:
            return {"found": False}
        start_ts, end_ts, released = row
        if released is not None:
            return {"found": True, "already_released": True}
        if end_ts is None:
            return {"found": True, "still_active": True,
                    "detail": "stop() the window before releasing it"}
        restored = signal_log.mark_excluded_range(start_ts, end_ts, excluded=False)
        conn.execute("UPDATE quarantine_ranges SET released_at = ? WHERE id = ?", (now, quarantine_id))
        return {"found": True, "id": quarantine_id, "rows_restored": restored}


def ranges(include_released: bool = False) -> list[dict]:
    where = "" if include_released else "WHERE released_at IS NULL"
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM quarantine_ranges {where} ORDER BY range_start DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    """How much of the dataset is currently quarantined - surfaced so a
    hole in the history is always visible rather than silently assumed."""
    with sqlite3.connect(signal_log.DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        excluded = conn.execute("SELECT COUNT(*) FROM signals WHERE excluded = 1").fetchone()[0]
    return {
        "total_signals": total,
        "excluded_signals": excluded,
        "excluded_pct": round(100.0 * excluded / total, 2) if total else 0.0,
        "active_ranges": len([r for r in ranges() if r.get("range_end") is None]),
        "quarantined_ranges": len(ranges()),
    }
