"""
Is a series (parent market - see signal_log.series_of) actually worth
watching for whale-signal purposes, and an escalating back-off to stop one
being repeatedly re-added, re-evaluated, and re-removed from the automatic
watchlist. Direct request: the watchlist is recomputed from scratch every
tick (main.py's KalshiClient.round_robin_select) with zero memory across
ticks - nothing dampens a series flapping in and out near a volume/live-
status cutoff.

Two halves, deliberately different timing:
1. BEFORE re-admission: a cheap check (ineligible_series) filters the
   discovery candidate pool each tick - this is what actually stops the
   flapping. Free, no whale-signal data needed.
2. AFTER admission: the real whale-worthy VERDICT (evaluate_pending) can
   only be rendered once a series has had real trade-tape data to observe
   - "is this series' real trade activity actually capable of clearing the
   whale notional threshold at all" can't be judged with zero history.

Whale-worthy = qualifying RATE, not raw count: real trades observed on the
series vs. how many cleared the notional threshold (services/whalewatchers/
kalshi_trade_tape.py's own filter). The numerator reuses signal_log as-is
(every qualifying print is already logged there); the denominator
(trades_observed) is new - nothing before this tracked how many real trades
were SEEN, only how many qualified.

Approval is sticky (never auto-revoked), matching how strategy.
excluded_series is already a settled, human-legible judgment rather than
something that silently flips back. A rejected series serves an escalating
backoff (doubling each consecutive rejection, capped) before it's eligible
to be re-observed - or a human can manually re-evaluate it early via
reset(), which is a deliberate fresh start (clears strike_count too), not a
continuation of prior escalation.

Same one-file-per-concern SQLite persistence idiom as every other
services/*.py module - see CLAUDE.md.
"""
import contextlib
import sqlite3
import time
from pathlib import Path

from services import db, signal_log

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "series_evaluator.db"

_STATUS_OBSERVING = "observing"
_STATUS_APPROVED = "approved"
_STATUS_REJECTED = "rejected"


def _init_series_status(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_status (
            series TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            first_seen_at REAL NOT NULL,
            trades_observed INTEGER NOT NULL DEFAULT 0,
            strike_count INTEGER NOT NULL DEFAULT 0,
            last_evaluated_at REAL,
            next_eligible_at REAL
        )
        """
    )


db.register_schema("series_status", _init_series_status)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect(). WAL mode
    (2026-08-11, real live incident: rollback-journal mode serializes ALL
    writers and readers against each other for the whole transaction) is
    set by db.connect() itself, same as every other migrated module.
    tests/test_performance_regressions.py captures `series_evaluator.
    _connect` by reference (real_connect = series_evaluator._connect) to
    count calls - unaffected, since `with _connect() as conn:`'s syntax is
    identical whether _connect() returns a raw connection or a context
    manager."""
    with db.connect(DB_PATH, tables=("series_status",)) as conn:
        yield conn


def record_trade_observed(series: str, now: float | None = None) -> None:
    """Called once per newly-seen real trade on this series (see
    services/whalewatchers/kalshi_trade_tape.py's fetch_signals loop, which
    already touches every real trade once, pass or fail on the notional
    check). Only actually counts while a series is in its 'observing'
    window - an 'approved' series has nothing left to observe for, and a
    'rejected' series still serving its backoff shouldn't accrue trades
    toward a verdict it isn't eligible to receive yet.

    Lazily reactivates a rejected-but-past-next_eligible_at row into a
    fresh observing window right here, on the next real trade seen for it -
    no separate reactivation hook needed elsewhere. Reactivation keeps the
    existing strike_count (this is automatic continuation of the same
    escalation, not a human-initiated fresh start - see reset() for that)."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, next_eligible_at FROM series_status WHERE series = ?", (series,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO series_status (series, status, first_seen_at, trades_observed) VALUES (?, ?, ?, 1)",
                (series, _STATUS_OBSERVING, now),
            )
            return
        status, next_eligible_at = row
        if status == _STATUS_REJECTED:
            if next_eligible_at is not None and now >= next_eligible_at:
                # Backoff served - reactivate into a fresh observing window,
                # strike_count carried forward on purpose (see docstring).
                conn.execute(
                    "UPDATE series_status SET status = ?, first_seen_at = ?, trades_observed = 1, "
                    "next_eligible_at = NULL WHERE series = ?",
                    (_STATUS_OBSERVING, now, series),
                )
            # else: still serving backoff - this trade doesn't count toward anything yet.
            return
        if status == _STATUS_APPROVED:
            return  # sticky, nothing left to observe for
        conn.execute(
            "UPDATE series_status SET trades_observed = trades_observed + 1 WHERE series = ?", (series,)
        )


def record_trades_observed_bulk(counts_by_series: dict[str, int], now: float | None = None) -> None:
    """Same effect as calling record_trade_observed() once per trade, but
    one connection for the whole batch instead of one per trade - direct,
    confirmed-live incident (2026-08-11): removing the trade-tape cap
    ("i want trade tape to be unlimited, never capped") multiplied real
    per-tick trade volume by 10-30x, and record_trade_observed's per-call
    _connect() (a fresh sqlite3.connect() plus a CREATE TABLE IF NOT
    EXISTS check every single time) is synchronous, blocking Python's
    single-threaded asyncio event loop for its full duration - thousands
    of those in one tick froze the whole app for several minutes, not a
    network/rate-limit issue as first suspected. counts_by_series is
    "how many raw trades this tick observed per series" (pre-aggregated
    by the caller) - collapses what used to be N per-trade round trips
    into at most len(counts_by_series) per-series ones. Reactivation
    (rejected, backoff served) credits the WHOLE batch count to the fresh
    observing window, not just 1 - all of them genuinely occurred inside
    it."""
    if not counts_by_series:
        return
    now = now if now is not None else time.time()
    with _connect() as conn:
        for series, count in counts_by_series.items():
            row = conn.execute(
                "SELECT status, next_eligible_at FROM series_status WHERE series = ?", (series,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO series_status (series, status, first_seen_at, trades_observed) VALUES (?, ?, ?, ?)",
                    (series, _STATUS_OBSERVING, now, count),
                )
                continue
            status, next_eligible_at = row
            if status == _STATUS_REJECTED:
                if next_eligible_at is not None and now >= next_eligible_at:
                    conn.execute(
                        "UPDATE series_status SET status = ?, first_seen_at = ?, trades_observed = ?, "
                        "next_eligible_at = NULL WHERE series = ?",
                        (_STATUS_OBSERVING, now, count, series),
                    )
                continue
            if status == _STATUS_APPROVED:
                continue
            conn.execute(
                "UPDATE series_status SET trades_observed = trades_observed + ? WHERE series = ?", (count, series)
            )


def ineligible_series(now: float | None = None) -> set[str]:
    """Series currently serving an escalating backoff - the BEFORE check
    that filters the discovery candidate pool each tick, run before
    round_robin_select. One batch query, not per-series, so this stays
    cheap regardless of watchlist size."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT series FROM series_status WHERE status = ? AND next_eligible_at > ?",
            (_STATUS_REJECTED, now),
        ).fetchall()
    return {r[0] for r in rows}


def evaluate_pending(cfg: dict, now: float | None = None) -> list[dict]:
    """Renders a verdict for every series whose observation window is
    complete. Sample-size-hedged the same way confidence_calibration.py/
    advisory_engine.py already gate their own reports: needs both a minimum
    elapsed time AND a minimum trade count before judging, with a hard
    max_observation_sec cap that forces a verdict regardless (a series with
    literally zero real trade activity by then is a clearer "not worthy"
    signal than a low rate, so that's an automatic reject rather than an
    indefinite wait). Returns the list of verdicts rendered this call
    (mainly for tests - callers don't need to do anything with it)."""
    now = now if now is not None else time.time()
    se_cfg = cfg.get("series_evaluator") or {}
    min_observation_sec = se_cfg.get("min_observation_sec", 3600)
    min_trades_observed = se_cfg.get("min_trades_observed", 20)
    max_observation_sec = se_cfg.get("max_observation_sec", 21600)
    min_qualify_rate = se_cfg.get("min_qualify_rate", 0.01)
    backoff_base_sec = se_cfg.get("backoff_base_sec", 1800)
    backoff_multiplier = se_cfg.get("backoff_multiplier", 2.0)
    backoff_max_sec = se_cfg.get("backoff_max_sec", 86400)

    # One connection for the whole call, not one per ready series - same
    # fix record_trades_observed_bulk already applied to this file's own
    # per-trade write path (2026-08-11 incident: a fresh _connect(), which
    # is a real sqlite3.connect() plus a CREATE TABLE IF NOT EXISTS check
    # every time, is real overhead to pay per row rather than once).
    # evaluate_pending runs every tick when series_evaluator.enabled
    # (ROADMAP.md's "per-module data-consumption audit" gap-check,
    # 2026-08-22), so a tick where several series become ready at once -
    # e.g. after a backfill, or several series admitted around the same
    # time - would otherwise reopen the connection once per verdict.
    # conn.commit() after each UPDATE keeps this call's original per-
    # verdict durability (a crash mid-loop still keeps every verdict
    # already written), rather than trading that away for one commit at
    # the end.
    with _connect() as conn:
        rows = conn.execute(
            "SELECT series, first_seen_at, trades_observed, strike_count FROM series_status WHERE status = ?",
            (_STATUS_OBSERVING,),
        ).fetchall()

        verdicts = []
        for series, first_seen_at, trades_observed, strike_count in rows:
            elapsed = now - first_seen_at
            ready = (elapsed >= min_observation_sec and trades_observed >= min_trades_observed) \
                or elapsed >= max_observation_sec
            if not ready:
                continue

            if trades_observed == 0:
                approved = False
                rate = 0.0
            else:
                qualified = signal_log.signal_count_for_series_since(series, first_seen_at)
                rate = qualified / trades_observed
                approved = rate >= min_qualify_rate

            if approved:
                conn.execute(
                    "UPDATE series_status SET status = ?, last_evaluated_at = ?, next_eligible_at = NULL "
                    "WHERE series = ?",
                    (_STATUS_APPROVED, now, series),
                )
            else:
                new_strike_count = strike_count + 1
                backoff_sec = min(backoff_base_sec * (backoff_multiplier ** strike_count), backoff_max_sec)
                conn.execute(
                    "UPDATE series_status SET status = ?, strike_count = ?, last_evaluated_at = ?, "
                    "next_eligible_at = ? WHERE series = ?",
                    (_STATUS_REJECTED, new_strike_count, now, now + backoff_sec, series),
                )
            conn.commit()
            verdicts.append({
                "series": series, "approved": approved, "rate": rate,
                "trades_observed": trades_observed,
            })
    return verdicts


def overview() -> list[dict]:
    """Every series ever evaluated, independent of what's on the current
    tick's watchlist - doubles as the log the user wants, no separate table
    needed. Feeds the Config/History-tab UI panel."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT series, status, first_seen_at, trades_observed, strike_count, "
            "last_evaluated_at, next_eligible_at FROM series_status ORDER BY series"
        ).fetchall()
    return [
        {
            "series": series, "status": status, "first_seen_at": first_seen_at,
            "trades_observed": trades_observed, "strike_count": strike_count,
            "last_evaluated_at": last_evaluated_at, "next_eligible_at": next_eligible_at,
        }
        for series, status, first_seen_at, trades_observed, strike_count, last_evaluated_at, next_eligible_at in rows
    ]


def reset(series: str, now: float | None = None) -> bool:
    """The manual "Re-evaluate" action - a deliberate fresh start, not a
    continuation of prior escalation, so strike_count resets to 0 too
    (unlike the automatic reactivation in record_trade_observed, which
    carries it forward). Returns False if this series has no row to reset
    (nothing to do - it was never observed in the first place)."""
    now = now if now is not None else time.time()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE series_status SET status = ?, first_seen_at = ?, trades_observed = 0, "
            "strike_count = 0, last_evaluated_at = ?, next_eligible_at = NULL WHERE series = ?",
            (_STATUS_OBSERVING, now, now, series),
        )
        return cur.rowcount > 0


def clear_all():
    """Wipes all series-evaluator state - Danger Zone reset, matching every
    other domain there."""
    with _connect() as conn:
        conn.execute("DELETE FROM series_status")
