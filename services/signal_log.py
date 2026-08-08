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


def series_of(ticker: str) -> str:
    """Kalshi tickers encode a series/category prefix before the first hyphen
    (e.g. "KXOSCARBESTPICTURE-26-..."). Grouping by it is a best-effort proxy
    for "this recurring type of market" — good enough for "how have whales done
    on Oscar-type predictions", not a guarantee every prefix is one clean topic.
    Public (not underscore-prefixed) since strategy_engine.py's manual
    excluded_series gate needs the exact same series definition the
    automatic win-rate filter already uses — one definition, not two that
    could quietly drift apart."""
    return ticker.split("-")[0] if ticker else ticker


def log_signal(ticker: str, side: str, size: int, confidence: float, source: str, seen_at: float | None = None):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO signals (ticker, series, side, size, confidence, source, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticker, series_of(ticker), side, size, confidence, source, seen_at or time.time()),
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
    series = series_of(ticker)
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


def clear_all():
    """Wipes the entire whale track record - every logged signal and its
    resolution outcome. Only ever triggered deliberately (Config tab's Danger
    Zone): this is the "how have whales actually done on real markets" history,
    normally kept intact across paper/shadow resets on purpose."""
    with _connect() as conn:
        conn.execute("DELETE FROM signals")


def recent(limit: int = 50, offset: int = 0, resolved_only: bool = False) -> list[dict]:
    """Individual signals, newest first - the browsable signal history
    (ROADMAP.md Phase 0.5). WhaleScanr's framing, copied directly: "every
    flag and how it settled, misses included" - not just the rolled-up
    win-rate percentage `stats()` already provides. `correct` is None for
    anything not yet resolved (still in flight), not conflated with a
    resolved-and-wrong 0."""
    cols = ["id", "ticker", "series", "side", "size", "confidence", "source", "seen_at", "resolved", "correct", "resolved_at"]
    where = "WHERE resolved = 1 " if resolved_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM signals {where}ORDER BY seen_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def total_count(resolved_only: bool = False) -> int:
    where = "WHERE resolved = 1" if resolved_only else ""
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM signals {where}").fetchone()[0]


def _size_ratio_ok(a: float, b: float, max_ratio: float) -> bool:
    lo, hi = min(a, b), max(a, b)
    return lo > 0 and (hi / lo) <= max_ratio


def _summarize_cluster(signals: list[dict]) -> dict:
    total_size = sum(s["size"] for s in signals)
    avg_confidence = sum(s["confidence"] for s in signals) / len(signals)
    span_sec = signals[-1]["seen_at"] - signals[0]["seen_at"]
    # More prints, tighter timing = more likely one actor accumulating, not
    # coincidence - capped well under 1.0 since this is inference on
    # anonymous data, never a claim of verified identity.
    cluster_confidence = 0.3 + 0.15 * (len(signals) - 1) - min(span_sec / 3600 * 0.1, 0.3)
    cluster_confidence = round(min(max(cluster_confidence, 0.1), 0.95), 2)
    return {
        "ticker": signals[0]["ticker"],
        "side": signals[0]["side"],
        "print_count": len(signals),
        "total_size": total_size,
        "avg_confidence": round(avg_confidence, 2),
        "cluster_confidence": cluster_confidence,
        "span_sec": round(span_sec),
        "first_seen": signals[0]["seen_at"],
        "last_seen": signals[-1]["seen_at"],
    }


def find_clusters(hours: int = 24, time_window_min: int = 30, max_size_ratio: float = 4.0) -> list[dict]:
    """Groups recent signals into probable-same-actor "clusters" using
    statistical/behavioral similarity, WhaleScanr's real approach
    (researched directly, see ROADMAP.md) to a genuine constraint this app
    already respects: Kalshi's real trade tape is anonymous, no usernames
    or account data exists, confirmed directly on their site. So this never
    claims verified identity - `cluster_confidence` is capped at 0.95 and
    is inference on top of already-good data, nothing more.

    A cluster is a same-ticker, same-side run of signals where each one is
    within time_window_min of the previous one AND within max_size_ratio of
    it (so a lone 500-contract print doesn't get lumped in with an
    unrelated 50,000-contract one just because they share a ticker/side).
    Single, non-clustered signals aren't returned - a "cluster" of one
    print isn't accumulation, it's just a print."""
    since = time.time() - hours * 3600
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, size, confidence, seen_at FROM signals "
            "WHERE seen_at >= ? ORDER BY ticker, side, seen_at",
            (since,),
        ).fetchall()
    cols = ["id", "ticker", "side", "size", "confidence", "seen_at"]
    signals = [dict(zip(cols, r)) for r in rows]

    clusters = []
    current: list[dict] = []
    for s in signals:
        if current and (
            s["ticker"] == current[-1]["ticker"]
            and s["side"] == current[-1]["side"]
            and (s["seen_at"] - current[-1]["seen_at"]) <= time_window_min * 60
            and _size_ratio_ok(s["size"], current[-1]["size"], max_size_ratio)
        ):
            current.append(s)
        else:
            if len(current) > 1:
                clusters.append(_summarize_cluster(current))
            current = [s]
    if len(current) > 1:
        clusters.append(_summarize_cluster(current))

    clusters.sort(key=lambda c: c["total_size"], reverse=True)
    return clusters


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
