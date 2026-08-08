"""
A broad, incrementally-scanned catalog of Kalshi's real markets - built to
close a real gap found directly, not assumed: discovery's live-only
filtering (kalshi.live_markets_only) only ever sampled the top 40 series by
24h volume, and checking that against real live data found almost none of
what's actually live right now (confirmed: the user independently observed
72-86 real live markets on Kalshi while this app's live-only watchlist found
effectively zero). A high-volume series overall and "has a game live right
now" are different things - volume-ranking the candidate pool systematically
misses live markets in lower-volume series.

Direct request, the actual shape of the fix: "fetch a complete list of
markets... paginate api fetches and populate/store data in a usable manner
until all markets are scanned and then just run updates on the ones that
match our config." services/kalshi_client.py's get_series_list() already
returns Kalshi's full ~9,400-series catalog in one call (no pagination
problem there - see its own docstring on why a flat *market* browse doesn't
work but a series-level one does); what was missing is fetching every
series' actual markets, not just the top 40. Doing that in one tick would be
thousands of API calls at once, so this scans incrementally instead - a
bounded batch of the least-recently-scanned series each trading-loop tick,
looping back around once everything's been covered (least-recently-scanned
first is self-healing: a series that hasn't been touched in a while
naturally rises to the front of the next batch, no separate cursor to keep
in sync with a series list that itself changes size over time).

SQLite file: data/market_catalog.db - gitignored, same one-file-per-concern
pattern as every other services/*.py persistence module. occurrence_datetime/
close_time are stored as parsed unix timestamps (not the original ISO
strings) specifically so window queries are cheap SQL range comparisons,
not per-row Python parsing on every call.
"""
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market_catalog.db"

# Direct scope correction: "maybe it doesn't have to be a FULL catalog but
# enough of sensible ones with enough volume and schedule close to being
# something relatively soon, within days or weeks, not 6 months or a year
# from now." A market whose occurrence is that far out isn't useful for
# live-status purposes yet anyway (it'll still be there to catch on a later
# scan, once it's actually near-term) - skipping it now keeps the catalog
# itself small and the scan cheap, rather than accumulating months of
# far-future markets nobody's asking about yet.
_MAX_PAST_HORIZON_SEC = 7 * 24 * 3600  # 1 week
_MAX_FUTURE_HORIZON_SEC = 21 * 24 * 3600  # 3 weeks


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS markets (
            ticker TEXT PRIMARY KEY,
            event_ticker TEXT,
            series_ticker TEXT,
            category TEXT,
            volume_24h_fp REAL,
            occurrence_ts REAL,
            close_ts REAL,
            status TEXT,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_occurrence ON markets (occurrence_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_markets_series ON markets (series_ticker)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS series_scan_state (
            series_ticker TEXT PRIMARY KEY,
            last_scanned_at REAL NOT NULL
        )
        """
    )
    return conn


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def next_series_to_scan(all_series: list[dict], batch_size: int) -> list[dict]:
    """The batch_size series least recently scanned (or never scanned at
    all) out of the given full series list - never a fixed offset/cursor,
    so it self-heals if the underlying series list grows/shrinks/reorders
    between calls, and a series that's been sitting stale the longest
    always surfaces first."""
    if not all_series:
        return []
    with _connect(DB_PATH) as conn:
        scanned_at = dict(conn.execute("SELECT series_ticker, last_scanned_at FROM series_scan_state").fetchall())
    ranked = sorted(all_series, key=lambda s: scanned_at.get(s["ticker"], 0.0))
    return ranked[:batch_size]


def mark_scanned(series_tickers: list[str], scanned_at: float | None = None):
    scanned_at = scanned_at if scanned_at is not None else time.time()
    with _connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT INTO series_scan_state (series_ticker, last_scanned_at) VALUES (?, ?) "
            "ON CONFLICT(series_ticker) DO UPDATE SET last_scanned_at = excluded.last_scanned_at",
            [(t, scanned_at) for t in series_tickers],
        )


def upsert_markets(series_ticker: str, category: str | None, markets: list[dict], updated_at: float | None = None):
    """One series' worth of real get_markets(series_ticker=...) results,
    written into the catalog. Called once per series per scan batch - see
    next_series_to_scan for how a batch is chosen."""
    updated_at = updated_at if updated_at is not None else time.time()
    rows = []
    for m in markets:
        ticker = m.get("ticker")
        if not ticker:
            continue
        occurrence_ts = _parse_ts(m.get("occurrence_datetime"))
        # Not a "full" catalog by design - skip anything with no schedule
        # info at all, or scheduled well outside the near-term horizon
        # (see _MAX_PAST_HORIZON_SEC/_MAX_FUTURE_HORIZON_SEC above). A
        # far-future market gets picked up on a later scan once it's
        # actually near-term; no need to store it today.
        if occurrence_ts is None:
            continue
        if not (updated_at - _MAX_PAST_HORIZON_SEC <= occurrence_ts <= updated_at + _MAX_FUTURE_HORIZON_SEC):
            continue
        rows.append((
            ticker, m.get("event_ticker"), series_ticker, category,
            float(m.get("volume_24h_fp") or 0), occurrence_ts,
            _parse_ts(m.get("close_time")), m.get("status"), updated_at,
        ))
    if not rows:
        return
    with _connect(DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO markets
                (ticker, event_ticker, series_ticker, category, volume_24h_fp, occurrence_ts, close_ts, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                event_ticker=excluded.event_ticker, category=excluded.category,
                volume_24h_fp=excluded.volume_24h_fp, occurrence_ts=excluded.occurrence_ts,
                close_ts=excluded.close_ts, status=excluded.status, updated_at=excluded.updated_at
            """,
            rows,
        )


def candidates_in_window(now: float, lookahead_sec: float, lookback_sec: float, min_volume: float = 0) -> list[dict]:
    """Every catalog market whose occurrence_ts falls within [now -
    lookback_sec, now + lookahead_sec] - the same window shape main.py's
    _fetch_live_status already checks against, just drawn from the full
    scanned catalog instead of one tick's narrow top-N-series fetch. Status
    filtered to "open" (or unset, for markets scanned before that field
    existed) - a closed/settled market has nothing live left to check.

    Returned as real-market-shaped dicts (occurrence_datetime/close_time as
    ISO strings, not the raw unix timestamps stored internally) so callers
    (main.py's _fetch_live_status, KalshiClient.round_robin_select) can
    treat a catalog row exactly like a freshly-fetched Kalshi market object -
    no second parsing convention to keep in sync with the real one."""
    lo, hi = now - lookback_sec, now + lookahead_sec
    with _connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT ticker, event_ticker, series_ticker, category, volume_24h_fp, occurrence_ts, close_ts, status
            FROM markets
            WHERE occurrence_ts IS NOT NULL AND occurrence_ts BETWEEN ? AND ?
              AND volume_24h_fp >= ?
              AND (status IS NULL OR status = 'open' OR status = 'active')
            ORDER BY volume_24h_fp DESC
            """,
            (lo, hi, min_volume),
        ).fetchall()
    cols = ("ticker", "event_ticker", "series_ticker", "category", "volume_24h_fp", "occurrence_ts", "close_ts", "status")
    results = []
    for r in rows:
        d = dict(zip(cols, r))
        d["occurrence_datetime"] = _to_iso(d.pop("occurrence_ts"))
        close_ts = d.pop("close_ts")
        if close_ts is not None:
            d["close_time"] = _to_iso(close_ts)
        results.append(d)
    return results


def _to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def scan_progress() -> dict:
    """Honest progress reporting, same idiom as every other data-threshold-
    gated feature in this app - lets a debug endpoint say "8,420/9,400
    series scanned at least once" instead of the catalog silently being
    empty or partial with no visibility into why."""
    with _connect(DB_PATH) as conn:
        scanned_series = conn.execute("SELECT COUNT(*) FROM series_scan_state").fetchone()[0]
        total_markets = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
        oldest_scan = conn.execute("SELECT MIN(last_scanned_at) FROM series_scan_state").fetchone()[0]
    return {"scanned_series": scanned_series, "total_markets": total_markets, "oldest_scan_at": oldest_scan}


def clear_all():
    """Wipes the catalog and scan progress - self-healing on its own via
    ordinary re-scanning, this exists mainly for tests and the Config tab's
    Danger Zone reset, matching every other domain there."""
    with _connect(DB_PATH) as conn:
        conn.execute("DELETE FROM markets")
        conn.execute("DELETE FROM series_scan_state")
