"""
Does knowing part of the settlement average beat the market's own price?

services/index_feed/ established that for the crypto series the final
sixty one-second index observations ARE the settlement, and that Kalshi
streams them as they accumulate. That is a fact about the data. Whether it
is an *edge* is a separate, empirical question, and this module exists to
answer it with measurement rather than assertion.

The question, stated precisely: at T seconds before close, with k of 60
observations known, is the projection implied by the partial average a
better predictor of the actual outcome than the market's own yes price at
that same instant?

Both are probability forecasts of the same binary event, so they are scored
the same way - Brier score, mean squared error against the realised 0/1
outcome. Lower is better. If the projection does not beat the market, this
module says so and nothing should be wired to it; the honest negative
result is worth as much as the positive one and costs a great deal less
than discovering it with capital.

WHY A PURPOSE-BUILT DATASET RATHER THAN A JOIN

Reconstructing this after the fact from index_feed, series_watcher and
market_catalog would mean joining three stores on timestamps that were
never meant to align, at one-second resolution, across a boundary where the
market's own ticker rotates. Instead, each observation is recorded once, at
the moment it is true, with both forecasts already side by side. The
outcome is filled in later against the same row.

This module never trades. It records and scores.
"""
import sqlite3
import threading
import time
from pathlib import Path

from services import fault_log
from services import index_feed

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "settlement_edge.db"

# A market's implied probability and the projection are only comparable if
# they refer to the same event, so observations are keyed by (ticker,
# window_end) - the close the settlement average is accumulating toward.
_MIN_SAMPLES_FOR_VERDICT = 200


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS window_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            index_id TEXT NOT NULL,
            window_end_ts REAL NOT NULL,
            observed_at REAL NOT NULL,
            seconds_to_close REAL,
            observations_known INTEGER NOT NULL,
            partial_average REAL NOT NULL,
            strike REAL NOT NULL,
            comparison TEXT NOT NULL,
            spot REAL,
            required_remaining REAL,
            gap_from_spot REAL,
            market_yes_price REAL,
            settled_yes INTEGER,
            final_average REAL,
            resolved_at REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_se_window ON window_observations (ticker, window_end_ts)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_se_unresolved ON window_observations (window_end_ts) "
        "WHERE settled_yes IS NULL"
    )
    return conn


_buffer: list[tuple] = []
_FLUSH_BATCH = 120
_record_errors = 0
# A tick_executor worker thread (main.py's _flush_secondary_capture_stores,
# 2026-09-01) and the main asyncio event-loop thread (services/whale_stream/
# index_stream_handlers.py's record_observation call, awaited directly on
# the loop) both touch _buffer now - before that fix, flush() also only ran
# on the event loop, so append/flush could only interleave cooperatively.
# Same race series_watcher.py's own _buffer_lock was added to fix (code-
# review finding #2): an unsynchronized flush() swap-and-clear racing a
# concurrent .append() can orphan an appended row into a buffer nothing
# ever flushes again - a silently lost row, no error, no drop counter
# increment. threading.Lock, not asyncio.Lock - the two real callers are on
# different OS threads, not just different coroutines on one event loop.
_buffer_lock = threading.Lock()


def record_observation(ticker: str, spec: dict, projection: dict,
                       market_yes_price: float | None, now: float | None = None) -> bool:
    """One paired forecast, taken while the settlement window is open.

    Records BOTH predictors at the same instant - the market's yes price and
    everything needed to derive the projection - so neither can be
    reconstructed later with hindsight leaking in. Only fires while
    projection["status"] == "accumulating"; outside the window there is
    nothing to compare."""
    try:
        if projection.get("status") != "accumulating":
            return False
        now = now if now is not None else time.time()
        window_end = close_ts(spec)
        row = (
            ticker, spec["index_id"], window_end or 0.0, now,
            (window_end - now) if window_end else None,
            projection["observations_known"], projection["partial_average"],
            spec["strike"], spec["comparison"], projection.get("spot"),
            projection.get("required_remaining"), projection.get("gap_from_spot"),
            market_yes_price,
        )
        with _buffer_lock:
            _buffer.append(row)
            should_flush = len(_buffer) >= _FLUSH_BATCH
        if should_flush:
            flush()
        return True
    except Exception as exc:
        # Counted, not just swallowed. This except exists because the
        # recorder runs on the websocket path and must never take the
        # stream down - but a bare `return False` also hid a real bug for a
        # while (a renamed helper left a NameError here, and the symptom was
        # simply that nothing was ever recorded). A non-zero value in
        # stats() means this is failing systematically, not that the market
        # is quiet.
        #
        # Real live bug, caught 2026-08-23 re-reading this function while
        # building services/settlement_edge_entry.py: this `except`
        # clause didn't bind `exc` (`except Exception:`, not `except
        # Exception as exc:`), so any real failure raised a fresh
        # NameError right here instead of being logged - the exact
        # "renamed helper left a NameError here" incident this same
        # comment already describes, reintroduced by omission. Silent
        # because nothing has actually failed inside the try block
        # recently (_record_errors sat at 0), so the NameError itself was
        # never exercised.
        global _record_errors
        _record_errors += 1
        fault_log.record("settlement_edge", "record_observation", exc)
        return False


def close_ts(spec: dict) -> float | None:
    from datetime import datetime

    raw = spec.get("close_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def flush() -> dict:
    global _buffer
    # _buffer_lock: the swap-and-clear must be atomic with record_observation's
    # own append (above) and with a concurrent second flush() call - without
    # this, two flush() calls can both capture the same not-yet-reset buffer
    # (window_observations has no unique constraint, so that means literal
    # duplicate rows), or an append can land in a buffer neither flush() call
    # will ever read again (a silently lost row). The DB write itself stays
    # outside the lock - only the buffer swap needs it.
    with _buffer_lock:
        rows, _buffer = _buffer, []
    if not rows:
        return {"observations": 0}
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT INTO window_observations (ticker, index_id, window_end_ts, observed_at, "
                "seconds_to_close, observations_known, partial_average, strike, comparison, spot, "
                "required_remaining, gap_from_spot, market_yes_price) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
    except Exception as exc:
        fault_log.record("settlement_edge", "flush", exc, context=f"{len(rows)} observation(s) dropped")
        return {"observations": 0, "dropped": len(rows)}
    return {"observations": len(rows)}


def resolve_window(ticker: str, settled_yes: bool, final_average: float | None = None,
                   now: float | None = None) -> int:
    """Fill in the realised outcome for every observation of one market's
    window. Called once the market settles - the outcome is written to the
    rows rather than joined at read time, so a scoring run can never
    accidentally pair an observation with a different window's result."""
    now = now if now is not None else time.time()
    try:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE window_observations SET settled_yes = ?, final_average = ?, resolved_at = ? "
                "WHERE ticker = ? AND settled_yes IS NULL",
                (1 if settled_yes else 0, final_average, now, ticker),
            )
            return cur.rowcount
    except sqlite3.Error:
        return 0


def projected_probability(row: dict, index_volatility: float | None) -> float | None:
    """The projection expressed as a probability, so it can be scored
    against the market's price on the same scale.

    The known part of the settlement average is certain; only the remaining
    m = 60 - k observations are uncertain, and required_remaining() already
    says exactly what they must average. So the question reduces to: will
    the mean of the next m one-second index values clear that number?

    Treating the index as a random walk with per-second scale sigma, the
    value j seconds ahead has standard deviation sigma*sqrt(j), and the
    mean of the next m values has

        Var = sigma^2 * (m + 1)(2m + 1) / (6m)  ~=  sigma^2 * m / 3

    so its standard error is sigma * sqrt(m/3). It is centred on the
    current spot, because a random walk's expected future value is where it
    is now. That gives a plain normal-tail probability.

    Deliberately kept crude and explicit rather than fitted: this is a
    scoring baseline for a hypothesis test, not a pricing model, and an
    over-tuned version would beat the market on the sample it was tuned on
    and tell us nothing. If even this crude form beats the market's price,
    the effect is real and worth building properly."""
    import math

    required = row.get("required_remaining")
    spot = row.get("spot")
    k = row.get("observations_known")
    if required is None or spot is None or not k or not index_volatility:
        return None
    m = index_feed.SETTLEMENT_WINDOW_TICKS - k
    if m <= 0:
        return None
    se = max(index_volatility * math.sqrt((m + 1) * (2 * m + 1) / (6 * m)), 1e-9)
    z = (spot - required) / se
    # Normal CDF without scipy - the standard erf identity.
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def edge_report(min_samples: int = _MIN_SAMPLES_FOR_VERDICT) -> dict:
    """Brier scores for both forecasts, overall and bucketed by how much of
    the window was known.

    Reports "insufficient" rather than a verdict below min_samples. A Brier
    comparison on a handful of observations from one or two windows is
    noise, and this module's whole purpose is to be the thing that does not
    overclaim."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM window_observations WHERE settled_yes IS NOT NULL "
                "AND market_yes_price IS NOT NULL")]
    except sqlite3.Error as exc:
        return {"status": "unknown", "reason": f"store unreadable: {exc}"}

    if not rows:
        return {"status": "insufficient", "resolved_observations": 0,
                "reason": "no settled observations yet — this fills in as settlement windows "
                          "are observed and their markets resolve"}

    vol_cache: dict[str, float | None] = {}
    buckets: dict[str, dict] = {}
    market_se, proj_se, scored = 0.0, 0.0, 0

    for r in rows:
        outcome = 1.0 if r["settled_yes"] else 0.0
        idx = r["index_id"]
        if idx not in vol_cache:
            vol_cache[idx] = index_feed.recent_volatility(idx, lookback_sec=3600)
        p_proj = projected_probability(r, vol_cache[idx])
        p_mkt = r["market_yes_price"]
        if p_proj is None:
            continue
        scored += 1
        market_se += (p_mkt - outcome) ** 2
        proj_se += (p_proj - outcome) ** 2
        known = r["observations_known"]
        key = "0-15" if known <= 15 else ("16-30" if known <= 30 else
                                          ("31-45" if known <= 45 else "46-59"))
        b = buckets.setdefault(key, {"n": 0, "market_se": 0.0, "proj_se": 0.0})
        b["n"] += 1
        b["market_se"] += (p_mkt - outcome) ** 2
        b["proj_se"] += (p_proj - outcome) ** 2

    if scored == 0:
        return {"status": "insufficient", "resolved_observations": len(rows),
                "reason": "no observation had enough index volatility history to form a "
                          "projection probability"}

    for b in buckets.values():
        b["market_brier"] = round(b["market_se"] / b["n"], 4)
        b["projection_brier"] = round(b["proj_se"] / b["n"], 4)
        b["projection_better_by"] = round(b["market_brier"] - b["projection_brier"], 4)
        del b["market_se"], b["proj_se"]

    market_brier = market_se / scored
    proj_brier = proj_se / scored
    verdict = (
        "insufficient" if scored < min_samples else
        ("projection_beats_market" if proj_brier < market_brier else "market_beats_projection")
    )
    return {
        "status": verdict,
        "scored_observations": scored,
        "distinct_windows": len({(r["ticker"], r["window_end_ts"]) for r in rows}),
        "market_brier": round(market_brier, 4),
        "projection_brier": round(proj_brier, 4),
        "projection_better_by": round(market_brier - proj_brier, 4),
        "by_observations_known": dict(sorted(buckets.items())),
        "min_samples_for_verdict": min_samples,
        "note": (
            "Brier score, lower is better. Both columns forecast the same binary event at the "
            "same instant. A positive projection_better_by means the streaming partial average "
            "carried information the market price did not — and only then is it worth wiring "
            "into an entry rule."
        ),
    }


def unresolved_tickers(older_than_sec: float = 120.0, limit: int = 40,
                       now: float | None = None) -> list[str]:
    """Markets with observations still awaiting an outcome, whose window
    closed at least `older_than_sec` ago.

    This exists because resolving from the trading loop's own market list
    does not work, and that was confirmed rather than assumed: a
    KXBTC15M market was found `finalized` with `result='yes'` six minutes
    after close while its 59 observations sat unresolved. The 15-minute
    series rotates its ticker every quarter hour, so by the time Kalshi
    populates `result` the market has already dropped out of the discovery
    watchlist and the loop's outcome pass never sees it again.

    So resolution has to be driven from THIS store's own pending list -
    the one place that remembers a window happened - rather than from
    whatever the watchlist happens to contain."""
    now = now if now is not None else time.time()
    try:
        with _connect() as conn:
            return [r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM window_observations WHERE settled_yes IS NULL "
                "AND window_end_ts < ? ORDER BY window_end_ts LIMIT ?",
                (now - older_than_sec, limit),
            )]
    except sqlite3.Error:
        return []


def drop_mismatched_observations() -> int:
    """Remove observations recorded against a window that was never this
    market's own (see index_feed.window_matches_close).

    Deleting rather than flagging, and this is the one place in this
    codebase that deletes captured data, so the reasoning is explicit:
    CLAUDE.md treats accumulated history as a first-class asset because it
    is *evidence*. These rows are not evidence of anything - they pair a
    market with the partial average of a settlement it does not settle on,
    which is not a measurement that was taken badly but one that was never
    of this market at all. Keeping them would mean every future scoring run
    has to re-derive and re-apply this same exclusion.

    Identified by their own recorded fields, not by ticker prefix: an
    observation whose window_end_ts is more than one window-spacing away
    from when it was observed cannot have been of an accumulating window."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM window_observations "
                "WHERE settled_yes IS NULL AND ABS(window_end_ts - observed_at) > 900"
            )
            return cur.rowcount
    except sqlite3.Error:
        return 0


def pending_windows() -> int:
    try:
        with _connect() as conn:
            return conn.execute(
                "SELECT COUNT(DISTINCT ticker || window_end_ts) FROM window_observations "
                "WHERE settled_yes IS NULL").fetchone()[0]
    except sqlite3.Error:
        return 0


def stats() -> dict:
    try:
        with _connect() as conn:
            n, resolved = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN settled_yes IS NOT NULL THEN 1 ELSE 0 END) "
                "FROM window_observations").fetchone()
            windows = conn.execute(
                "SELECT COUNT(DISTINCT ticker || window_end_ts) FROM window_observations").fetchone()[0]
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    return {"observations": n, "resolved": resolved or 0, "windows": windows,
            "buffered": len(_buffer), "pending_windows": pending_windows(),
            "record_errors": _record_errors}
