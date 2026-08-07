"""
Persistent log of every whale signal seen, plus — once a market settles —
whether the whale actually called it right. This is what makes "whale track
record over the last 30 days" a real, growing number instead of something
derived from the last 50 in-memory signals, which reset on every restart and
never knew what happened after the fact.

SQLite file lives at data/signal_log.db — gitignored, never commit it.

Honesty note: resolution checking relies on Kalshi's market `result` field
being "yes"/"no" once settled. That's a reasonable reading of the API but,
like the account-balance field names elsewhere in this app, wasn't
independently confirmed against every market type — see /status.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "signal_log.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            confidence REAL NOT NULL,
            source TEXT NOT NULL,
            seen_at REAL NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            correct INTEGER,
            resolved_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_resolved ON signals (resolved)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_series ON signals (series)")
    return conn


def _series_of(ticker: str) -> str:
    """Kalshi tickers encode a series/category prefix before the first hyphen
    (e.g. "KXOSCARBESTPICTURE-26-..."). Grouping by it is a best-effort proxy
    for "this recurring type of market" — good enough for "how have whales done
    on Oscar-type predictions", not a guarantee every prefix is one clean topic."""
    return ticker.split("-")[0] if ticker else ticker


def log_signal(ticker: str, side: str, size: int, confidence: float, source: str, seen_at: float | None = None):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO signals (ticker, series, side, size, confidence, source, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, _series_of(ticker), side, size, confidence, source, seen_at or time.time()),
        )


def unresolved_batch(limit: int = 3, older_than_sec: float = 600) -> list[dict]:
    """Oldest unresolved signals whose market has had at least `older_than_sec`
    to plausibly settle — avoids re-checking a market seconds after the print,
    and keeps each poll tick's extra API calls small."""
    cutoff = time.time() - older_than_sec
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side FROM signals WHERE resolved = 0 AND seen_at < ? ORDER BY seen_at ASC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [{"id": r[0], "ticker": r[1], "side": r[2]} for r in rows]


def mark_resolved(signal_id: int, correct: bool):
    with _connect() as conn:
        conn.execute(
            "UPDATE signals SET resolved = 1, correct = ?, resolved_at = ? WHERE id = ?",
            (1 if correct else 0, time.time(), signal_id),
        )


def series_stats(ticker: str, days: int = 30) -> dict:
    """Whale accuracy scoped to this market's series/category — e.g. "how have
    whales done on Best Picture predictions", not just "how have they done
    on this one already-mostly-decided market"."""
    series = _series_of(ticker)
    since = time.time() - days * 86400
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE series = ? AND seen_at >= ?", (series, since)
        ).fetchone()[0]
        resolved_count, correct_sum = conn.execute(
            "SELECT COUNT(*), SUM(correct) FROM signals WHERE series = ? AND seen_at >= ? AND resolved = 1",
            (series, since),
        ).fetchone()
    resolved_count = resolved_count or 0
    correct_count = correct_sum or 0
    return {
        "series": series,
        "window_days": days,
        "total_signals": total,
        "resolved": resolved_count,
        "correct": correct_count,
        "win_rate": round(correct_count / resolved_count * 100, 1) if resolved_count else None,
    }


def stats(days: int = 30) -> dict:
    since = time.time() - days * 86400
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM signals WHERE seen_at >= ?", (since,)).fetchone()[0]
        resolved_count, correct_sum = conn.execute(
            "SELECT COUNT(*), SUM(correct) FROM signals WHERE seen_at >= ? AND resolved = 1", (since,)
        ).fetchone()
        first_seen = conn.execute("SELECT MIN(seen_at) FROM signals").fetchone()[0]
    resolved_count = resolved_count or 0
    correct_count = correct_sum or 0
    return {
        "window_days": days,
        "total_signals": total,
        "resolved": resolved_count,
        "correct": correct_count,
        "win_rate": round(correct_count / resolved_count * 100, 1) if resolved_count else None,
        "tracking_since": first_seen,
    }
