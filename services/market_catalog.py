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

from services import title_cache

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market_catalog.db"

# Direct scope correction: "maybe it doesn't have to be a FULL catalog but
# enough of sensible ones with enough volume and schedule close to being
# something relatively soon, within days or weeks, not 6 months or a year
# from now." A market whose occurrence is that far out isn't useful for
# live-status purposes yet anyway (it'll still be there to catch on a later
# scan, once it's actually near-term) - skipping it now keeps the catalog
# itself small and the scan cheap, rather than accumulating months of
# far-future markets nobody's asking about yet.
_MAX_PAST_HORIZON_SEC = 1 * 24 * 3600  # 1 day
_MAX_FUTURE_HORIZON_SEC = 21 * 24 * 3600  # 3 weeks


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # data/market_catalog.db is a live file the running dev server reads/
    # writes (CLAUDE.md) - CREATE TABLE IF NOT EXISTS alone doesn't add a
    # column to an existing table with existing rows, so a new column needs
    # an explicit, idempotent ALTER TABLE guarded by a check - same pattern
    # services/paper_broker.py/signal_log.py already established.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
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
    # Real display text - direct report of a regression caught live: catalog-
    # sourced markets were missing these entirely, so main.py's title-
    # building (`m.get("title") or m.get("yes_sub_title") or m["ticker"]`)
    # fell all the way through to the raw ticker for anything discovered via
    # the catalog instead of a fresh per-tick fetch. Added after the table
    # above already had live rows, hence the guarded ALTER TABLE.
    _add_column_if_missing(conn, "markets", "title", "TEXT")
    _add_column_if_missing(conn, "markets", "yes_sub_title", "TEXT")
    _add_column_if_missing(conn, "markets", "no_sub_title", "TEXT")
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


def series_with_expired_data(now: float | None = None) -> set[str]:
    """Series whose most-recently-known market has already closed - the
    catalog's own knowledge of this series is dead, not just aging.
    Real, confirmed-live finding (2026-08-16, same incident as
    candidates_in_window/open_candidates' own close_ts fix): pure least-
    recently-scanned ordering in next_series_to_scan has no way to know a
    series like KXBTC15M (a brand new market every 15 minutes) needs
    revisiting far sooner than a series whose markets each run for weeks
    or months - both look identical to a purely time-based LRU. A full
    scan cycle across every configured-category series was confirmed
    taking 2-8h in practice; no batch-size/interval tuning of that cycle
    can keep a 15-minute rotation fresh, since the fix has to be about
    WHICH series gets scanned next, not how fast the whole rotation
    goes."""
    now = now if now is not None else time.time()
    with _connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT series_ticker, MAX(close_ts) FROM markets "
            "WHERE series_ticker IS NOT NULL GROUP BY series_ticker"
        ).fetchall()
    return {series for series, max_close in rows if max_close is not None and max_close < now}


def next_series_to_scan(all_series: list[dict], batch_size: int, now: float | None = None) -> list[dict]:
    """The batch_size series most in need of a rescan out of the given full
    series list - never a fixed offset/cursor, so it self-heals if the
    underlying series list grows/shrinks/reorders between calls.

    Two-tier priority (2026-08-16, see series_with_expired_data's own
    docstring for the real incident this closes): a series whose most
    recently known market has already closed comes first, REGARDLESS of
    how recently it was last scanned - a fast-rotating series (e.g.
    KXBTC15M) would otherwise sit at its normal least-recently-scanned
    position in the queue and never get revisited fast enough to catch its
    next live window. Within each tier, least-recently-scanned (or never
    scanned at all) still comes first, unchanged from before this fix -
    a series that's been sitting stale the longest within its tier always
    surfaces first."""
    if not all_series:
        return []
    now = now if now is not None else time.time()
    with _connect(DB_PATH) as conn:
        scanned_at = dict(conn.execute("SELECT series_ticker, last_scanned_at FROM series_scan_state").fetchall())
    expired = series_with_expired_data(now)
    ranked = sorted(
        all_series,
        key=lambda s: (s["ticker"] not in expired, scanned_at.get(s["ticker"], 0.0)),
    )
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
        title_fields = title_cache.market_title_fields(m)
        rows.append((
            ticker, m.get("event_ticker"), series_ticker, category,
            float(m.get("volume_24h_fp") or 0), occurrence_ts,
            _parse_ts(m.get("close_time")), m.get("status"), updated_at,
            title_fields["title"], title_fields["yes_sub_title"], title_fields["no_sub_title"],
        ))
    if not rows:
        return
    with _connect(DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO markets
                (ticker, event_ticker, series_ticker, category, volume_24h_fp, occurrence_ts, close_ts, status, updated_at,
                 title, yes_sub_title, no_sub_title)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                event_ticker=excluded.event_ticker, category=excluded.category,
                volume_24h_fp=excluded.volume_24h_fp, occurrence_ts=excluded.occurrence_ts,
                close_ts=excluded.close_ts, status=excluded.status, updated_at=excluded.updated_at,
                title=excluded.title, yes_sub_title=excluded.yes_sub_title, no_sub_title=excluded.no_sub_title
            """,
            rows,
        )


def candidates_in_window(now: float, lookahead_sec: float, lookback_sec: float, min_volume: float = 0) -> list[dict]:
    """Every catalog market whose occurrence_ts falls within [now -
    lookback_sec, now + lookahead_sec] - the same window shape main.py's
    _fetch_live_status already checks against, just drawn from the full
    scanned catalog instead of one tick's narrow top-N-series fetch. Status
    filtered to "active" (or unset, for markets scanned before that field
    existed) - a closed/settled market has nothing live left to check.
    docs/kalshi/market_lifecycle.md confirms the real REST response value is
    "active", never the literal string "open" ("open" only ever appears as a
    GET /markets?status= query FILTER value, mapped server-side to "active" -
    see that doc's own filter-value table); the old status = 'open' check
    here was dead code, matching nothing any real market object ever sent.

    close_ts > now is also required now (2026-08-16 direct report: "im not
    seeing any positions being opened or signals being read" for
    KXBTC15M - real, confirmed-live root cause). occurrence_ts does NOT
    mean "start of live window" for every market shape - for KXBTC15M,
    occurrence_datetime is actually ~5 minutes AFTER close_time (a
    settlement-checkpoint timestamp, not a kickoff one), so a market that
    closed hours ago can still have occurrence_ts fall inside this window,
    and its catalog `status` column stays whatever it was at last scan
    (never corrected to "closed" without a rescan) - confirmed live: 3
    KXBTC15M rows sitting 2-8h stale, all already closed, still reading
    status="active" and satisfying this query before this filter existed.
    close_time is the one signal every market shape agrees means "trading
    has stopped" (same shortcut _fetch_live_status/event_lifecycle.
    classify_phase already use) - filtering on it directly, rather than
    trying to fix what occurrence_ts means per market shape, closes this
    for every current and future fast-rotating series, not just this one.

    Returned as real-market-shaped dicts (occurrence_datetime/close_time as
    ISO strings, not the raw unix timestamps stored internally) so callers
    (main.py's _fetch_live_status, KalshiClient.round_robin_select) can
    treat a catalog row exactly like a freshly-fetched Kalshi market object -
    no second parsing convention to keep in sync with the real one."""
    lo, hi = now - lookback_sec, now + lookahead_sec
    with _connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT ticker, event_ticker, series_ticker, category, volume_24h_fp, occurrence_ts, close_ts, status,
                   title, yes_sub_title, no_sub_title
            FROM markets
            WHERE occurrence_ts IS NOT NULL AND occurrence_ts BETWEEN ? AND ?
              AND volume_24h_fp >= ?
              AND (status IS NULL OR status = 'active')
              AND (close_ts IS NULL OR close_ts > ?)
            ORDER BY volume_24h_fp DESC
            """,
            (lo, hi, min_volume, now),
        ).fetchall()
    cols = (
        "ticker", "event_ticker", "series_ticker", "category", "volume_24h_fp", "occurrence_ts", "close_ts", "status",
        "title", "yes_sub_title", "no_sub_title",
    )
    results = []
    for r in rows:
        d = dict(zip(cols, r))
        d["occurrence_datetime"] = _to_iso(d.pop("occurrence_ts"))
        close_ts = d.pop("close_ts")
        if close_ts is not None:
            d["close_time"] = _to_iso(close_ts)
        results.append(d)
    return results


def open_candidates(
    categories: list[str] | None = None, min_volume: float = 0, now: float | None = None,
    min_volume_by_series: dict[str, float] | None = None,
) -> list[dict]:
    """Every open/active catalog market in the given categories (all
    categories if None) above min_volume, sorted by volume descending - no
    occurrence-time window at all, unlike candidates_in_window (built for
    the narrower "what's live right now" question). Direct incident
    (2026-08-15): "you made the market watch list and whale watching grind
    to a halt" - the default (non-live-only) discovery path used to spend
    a real get_candidate_markets REST call (one per series, tens to over a
    hundred per refresh) every time its cache went stale, discovering
    the SAME thing this already-persistent, already-incrementally-scanned
    catalog exists to answer for free. upsert_markets() already rejects
    anything outside the catalog's own near-term horizon
    (_MAX_PAST_HORIZON_SEC/_MAX_FUTURE_HORIZON_SEC) at write time, so
    every row in here is already schedule-reasonable without this query
    needing its own window on top.

    Same real-market-shaped dict return convention as candidates_in_window
    (occurrence_datetime/close_time as ISO strings) so callers can treat a
    row exactly like a freshly-fetched Kalshi market object.

    close_ts > now required now (2026-08-16) - same real, confirmed-live
    fix as candidates_in_window's own close_ts filter, see its docstring
    for the full incident. This function is the one actually driving the
    default (non-live-only) discovery path _refresh_discovery_cache uses,
    so a stale/already-closed row here directly means a dead market
    reaching the real watchlist, not just a theoretical gap - this was
    confirmed live against KXBTC15M specifically (3 already-closed
    instances, hours stale, all still status="active", all still being
    selected as real watchlist candidates before this filter existed).
    now is optional (defaults to time.time()), same convention as
    candidates_in_window, so tests can pin it deterministically.

    This alone still can't catch a row whose close_ts/status are stale
    because Kalshi revised them (a close_date_updated event this app
    doesn't listen for - docs/kalshi/market_lifecycle.md) rather than
    because it just hasn't been rescanned yet (2026-08-16 close_time-
    mutability finding, ROADMAP.md's now-closed "Active investigation"
    entry) - the real-time confirmation pass on the final, already-narrowed
    selection (main._refresh_discovery_cache, via the batched
    KalshiClient.get_markets_by_tickers) is what actually closes that gap;
    this function's own close_ts/status filtering is still worth keeping as
    a first-pass cut, just not sufficient alone. "active" per docs/kalshi/
    market_lifecycle.md's real REST response vocabulary - "open" (as in the
    old status = 'open' check this replaced) is only ever a query filter
    value, never a value a real market object's own status field sends.

    min_volume_by_series (2026-08-16, direct live incident: KXBTC15M never
    appeared in the auto-discovered watchlist despite being explicitly
    configured with per-series overrides elsewhere - confirmed root cause
    against market_catalog.db directly, not guessed): volume_24h is a
    24-hour rolling figure, but a KXBTC15M market's entire tradeable
    lifetime is 15 minutes - its own catalog rows show volume_24h_fp=0.0
    for every still-open instance and only becomes nonzero (600k+, well
    above any reasonable min_volume) after close_ts, by which point
    close_ts > now above has already excluded it. The 24h window structurally
    can never be satisfied while the market is still open, for any series
    whose full lifecycle is shorter than 24h - a blanket min_volume floor
    just isn't the right test for those. Optional per-series override dict
    (e.g. {"KXBTC15M": 0}), keyed by series_ticker, checked via SQL CASE
    against the blanket min_volume default for every series not listed -
    zero behavior change for anyone not passing this."""
    now = now if now is not None else time.time()
    if min_volume_by_series:
        case_sql = "CASE series_ticker " + " ".join("WHEN ? THEN ?" for _ in min_volume_by_series) + " ELSE ? END"
        vol_params: list = [v for pair in min_volume_by_series.items() for v in pair] + [min_volume]
        vol_clause = f"volume_24h_fp >= ({case_sql})"
    else:
        vol_clause = "volume_24h_fp >= ?"
        vol_params = [min_volume]
    with _connect(DB_PATH) as conn:
        if categories:
            placeholders = ",".join("?" for _ in categories)
            rows = conn.execute(
                f"""
                SELECT ticker, event_ticker, series_ticker, category, volume_24h_fp, occurrence_ts, close_ts, status,
                       title, yes_sub_title, no_sub_title
                FROM markets
                WHERE {vol_clause} AND category IN ({placeholders})
                  AND (status IS NULL OR status = 'active')
                  AND (close_ts IS NULL OR close_ts > ?)
                ORDER BY volume_24h_fp DESC
                """,
                (*vol_params, *categories, now),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT ticker, event_ticker, series_ticker, category, volume_24h_fp, occurrence_ts, close_ts, status,
                       title, yes_sub_title, no_sub_title
                FROM markets
                WHERE {vol_clause}
                  AND (status IS NULL OR status = 'active')
                  AND (close_ts IS NULL OR close_ts > ?)
                ORDER BY volume_24h_fp DESC
                """,
                (*vol_params, now),
            ).fetchall()
    cols = (
        "ticker", "event_ticker", "series_ticker", "category", "volume_24h_fp", "occurrence_ts", "close_ts", "status",
        "title", "yes_sub_title", "no_sub_title",
    )
    results = []
    for r in rows:
        d = dict(zip(cols, r))
        occurrence_ts = d.pop("occurrence_ts")
        if occurrence_ts is not None:
            d["occurrence_datetime"] = _to_iso(occurrence_ts)
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
