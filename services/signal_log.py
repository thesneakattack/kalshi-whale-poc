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
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "signal_log.db"


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # data/signal_log.db is a live file the running dev server reads/writes
    # (CLAUDE.md) - CREATE TABLE IF NOT EXISTS alone doesn't add a column to
    # an existing table with existing rows, so a new column needs an
    # explicit, idempotent ALTER TABLE guarded by a check - same pattern
    # services/paper_broker.py already established for config_fingerprint.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


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
    # services/confidence_calibration.py's whole input - the individual
    # confidence factors, not just the blended score, so a future pass can
    # ask "which factors actually predicted a correct call" instead of only
    # ever seeing the number they were already blended into. Added after
    # the table above already shipped with live rows, hence the guarded
    # ALTER TABLE rather than a column in the CREATE statement. Nullable:
    # only real providers that compute a breakdown populate it (see
    # services/whalewatchers/kalshi_trade_tape.py) - simulator-sourced rows
    # leave it null, and calibration explicitly filters to real ones anyway.
    _add_column_if_missing(conn, "signals", "factors_json", "TEXT")
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


def log_signal(
    ticker: str, side: str, size: int, confidence: float, source: str,
    seen_at: float | None = None, factors: dict | None = None,
):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO signals (ticker, series, side, size, confidence, source, seen_at, factors_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker, series_of(ticker), side, size, confidence, source, seen_at or time.time(),
                json.dumps(factors) if factors is not None else None,
            ),
        )


def recent_sides_for_ticker(ticker: str, since_ts: float) -> list[str]:
    """Every side ("yes"/"no") logged for this exact ticker since since_ts -
    services/whalewatchers/kalshi_trade_tape.py's input for scoring whether
    a new print agrees with recent ones on the same market (composite_confidence_
    breakdown's agreement_factor). Ticker-scoped, not series-scoped like
    series_stats - "did whales agree on THIS market" is a narrower, more
    literal question than "how do whales usually do on this type of
    market."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT side FROM signals WHERE ticker = ? AND seen_at >= ?", (ticker, since_ts),
        ).fetchall()
    return [r[0] for r in rows]


def cluster_factor(ticker: str, side: str, size: float, since_ts: float, max_size_ratio: float = 4.0) -> float:
    """How much this (not-yet-logged) trade looks like it's extending an
    active same-actor accumulation pattern, rather than standing alone as an
    isolated large print - the live, per-signal analog of find_clusters()
    below, feeding composite_confidence_breakdown's cluster_factor (see
    services/whale_simulator.py). Barclay & Warner's stealth-trading finding
    (docs/prediction-market-strategy-alignment-plan.md Part 2.1) is why this
    exists: the strongest real evidence on which large trades are actually
    informed says sophisticated informed traders deliberately split into a
    run of similar-sized prints rather than one conspicuous block - so a
    print that's part of such a run is a stronger signal than an equally
    large one with nothing else like it nearby, not a weaker one.

    Unlike recent_sides_for_ticker/agreement_factor's "no history = neutral"
    idiom, "no similar-sized recent prints" is itself informative here (an
    isolated print, exactly the profile a pure notional-size threshold
    already treats as its only signal) - so this returns 0.0, not 0.5, when
    nothing qualifies. Scales toward 1.0 as more size-compatible prints pile
    up, capped at 3 (matching find_clusters' own "more prints = more likely
    real accumulation" intuition without trying to reproduce its full
    sequential-run algorithm here - this is a cheaper, real-time proxy for
    one trade, not a retrospective full-history scan)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT size FROM signals WHERE ticker = ? AND side = ? AND seen_at >= ?",
            (ticker, side, since_ts),
        ).fetchall()
    matches = sum(1 for (s,) in rows if _size_ratio_ok(s, size, max_size_ratio))
    return min(matches / 3, 1.0)


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


def signal_count_for_series_since(series: str, since_ts: float) -> int:
    """How many whale-qualifying signals this series has produced since
    since_ts - the numerator services/series_evaluator.py needs for its
    qualifying-rate verdict. Mirrors series_stats()'s query shape but
    scoped to an arbitrary timestamp (a series' own first_seen_at) rather
    than a fixed days window, and without the win/loss resolution logic
    series_stats needs - series_evaluator only cares about *how many*
    prints qualified, not whether they were later correct."""
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM signals WHERE series = ? AND seen_at >= ?", (series, since_ts)
        ).fetchone()[0]


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


def resolved_signals_with_factors() -> list[dict]:
    """services/confidence_calibration.py's entire input: resolved signals
    that carry a real per-factor confidence breakdown. factors_json IS NOT
    NULL is the filter, not a source string match - only real providers
    (services/whalewatchers/kalshi_trade_tape.py) ever populate it, so this
    naturally excludes every simulator-sourced row without needing a second,
    possibly-drifting definition of "real" to maintain. No date/limit
    scoping - the calibration gate cares about total resolved count, not
    recency, and this table is small enough (one row per signal, not per
    tick) that a full scan is cheap."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT confidence, correct, factors_json FROM signals "
            "WHERE resolved = 1 AND factors_json IS NOT NULL",
        ).fetchall()
    results = []
    for confidence, correct, factors_json in rows:
        try:
            factors = json.loads(factors_json)
        except (TypeError, ValueError):
            continue  # malformed row - skip rather than crash the whole report
        results.append({"confidence": confidence, "correct": bool(correct), "factors": factors})
    return results


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
