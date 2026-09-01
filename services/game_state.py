"""
Durable time series of real in-game state — score, period, clock, last play,
down/distance — captured alongside the market data this app already stores.

Direct request (2026-08-17): make sure that between sessions "the whole
flow is working at peak low latency and effectiveness, so even if we scrap
things data gathered is still useful," and "don't forget to include any
other data for us to formulate on down the line. example a stub for now to
fill data gaps for a baseball game like score and innings."

THE GAP THIS CLOSES

main.py already fetches this payload on every live-status pass
(`_fetch_live_status` -> `client.get_live_datas`), and since 2026-08-16 it
already parses it into `state["live_game_state"]`. But that is an in-memory
dict: it dies with the process, so nothing survives a restart and no
question about game state can ever be asked about the past. The data has
been arriving, correctly parsed, and evaporating.

Persisting it costs ZERO additional API calls - the fetch already happens
for `widget_status` - and it is the difference between being able and
unable to ask the questions that actually matter later:

  - Did this whale print land immediately after a scoring play?
  - How does the market's implied probability track the real score
    differential, and where does it lag?
  - Are whale signals during a live game better or worse than pre-game
    ones, controlling for how far the game has progressed?

None of those are answerable retroactively without a stored history, and
every one of them is worth having even if the current whale strategy is
scrapped entirely. That is the whole point: this is the raw record, not a
derived signal, so it stays useful across changes of approach.

SPORT-SHAPED, NOT FOOTBALL-SHAPED

docs/kalshi/get-live-data-with-type.md's verified football_game example carries
`away_points`/`home_points`/`clock`/`quarter`/`situation`. Other sports use
different names for the same ideas - a baseball game has innings rather
than quarters, and half-innings rather than a clock. So the columns here
are deliberately generic (`period`, `period_label`, `home_score`,
`away_score`) with a per-sport extraction map, AND the complete untouched
payload in `raw_json`. A sport whose shape isn't mapped yet still gets its
full record stored from day one and can be backfilled into columns later -
which is exactly the mistake this app already made with the ticker channel
and had to fix retroactively.
"""
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from services import fault_log

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "game_state.db"

# Buffered for the same reason every other capture path in this app is: the
# live-status pass can touch many events in one tick, and a per-event
# connect+insert on the event loop is the shape that froze the app on
# 2026-08-11.
_buffer: list[tuple] = []
_FLUSH_BATCH = 100
# A tick_executor worker thread (main.py's _flush_secondary_capture_stores,
# 2026-09-01) and the main asyncio event-loop thread (record()'s own call,
# awaited on the live-status pass) both touch _buffer now - see
# services/series_watcher.py's own _buffer_lock comment for the exact race
# this guards against (an unsynchronized flush() swap-and-clear racing a
# concurrent .append() can silently lose the appended row). threading.Lock,
# not asyncio.Lock - the two real callers are on different OS threads.
_buffer_lock = threading.Lock()
# Last stored fingerprint per event, so an unchanged game state doesn't
# write a duplicate row every poll. A finished game polled for hours would
# otherwise dominate the table with identical rows.
_last_fingerprint: dict[str, str] = {}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS game_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_ticker TEXT NOT NULL,
            sport TEXT,
            event_type TEXT,
            observed_at REAL NOT NULL,
            source_updated_ts INTEGER,
            status TEXT,
            widget_status TEXT,
            home_score INTEGER,
            away_score INTEGER,
            period INTEGER,
            period_label TEXT,
            clock TEXT,
            winner TEXT,
            last_play TEXT,
            last_play_ts INTEGER,
            raw_json TEXT NOT NULL
        )
        """
    )
    # CREATE TABLE IF NOT EXISTS does NOT add a column to a table that
    # already exists, so a column added after the first row was ever written
    # needs an explicit guarded ALTER - the idiom CLAUDE.md mandates for
    # exactly this reason.
    #
    # Learned the hard way here, within hours: `event_type` was added to the
    # CREATE above without this, so on any process that had already created
    # the table the INSERT carried 16 values against 15 columns and raised.
    # flush()'s broad `except` then swallowed it and returned a drop count
    # nobody was reading, so game_states sat at 0 rows looking like a quiet
    # market rather than a broken write.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(game_states)")}
    if "event_type" not in cols:
        conn.execute("ALTER TABLE game_states ADD COLUMN event_type TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_event ON game_states (event_ticker, observed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_time ON game_states (observed_at)")
    return conn


# Per-sport field names for the same underlying concept. Football is the one
# shape verified against a real payload (docs/kalshi/get-live-data-with-type.md, real
# NFL milestones, 2026-08-15); the others are declared here as the mapping
# to apply IF those field names appear, and are explicitly unverified until
# a real payload confirms them - see period_label_for().
#
# Anything not matched still lands in raw_json in full, so a wrong guess
# here costs a NULL column, never a lost record.
_PERIOD_KEYS = ("quarter", "inning", "period", "half", "set", "frame")
_HOME_KEYS = ("home_points", "home_score", "home_runs")
_AWAY_KEYS = ("away_points", "away_score", "away_runs")

# What a "period" is called, per sport, for display and for later grouping.
# Keyed by the sport/series hint the caller passes; unknown sports get the
# generic label rather than a guessed one.
_PERIOD_LABELS = {
    "baseball": "inning",
    "football": "quarter",
    "basketball": "quarter",
    "hockey": "period",
    "soccer": "half",
    "tennis": "set",
}


def period_label_for(sport: str | None) -> str:
    """"inning" for baseball, "quarter" for football, and so on. Returns the
    generic "period" for anything unmapped rather than inventing a term -
    a wrong label on a stored row is worse than a neutral one, because it
    reads as verified."""
    return _PERIOD_LABELS.get((sport or "").strip().lower(), "period")


def _first(details: dict, keys: tuple) -> object | None:
    for k in keys:
        if details.get(k) is not None:
            return details[k]
    return None


def _as_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract(details: dict, sport: str | None = None) -> dict:
    """Pull the generic fields out of one sport-specific `details` payload.

    Pure and side-effect free so it can be tested against real captured
    payloads without touching the DB. Every field is optional: a payload
    that carries none of them still produces a valid row whose value is
    entirely in raw_json."""
    details = details or {}
    last_play = details.get("last_play") or {}
    if not isinstance(last_play, dict):
        last_play = {}
    return {
        "status": details.get("status"),
        "widget_status": details.get("widget_status"),
        "home_score": _as_int(_first(details, _HOME_KEYS)),
        "away_score": _as_int(_first(details, _AWAY_KEYS)),
        "period": _as_int(_first(details, _PERIOD_KEYS)),
        "period_label": period_label_for(sport),
        "clock": details.get("clock"),
        # "" is Kalshi's own not-yet-decided value for winner, not a team
        # named empty string - normalised to None so a query for "has a
        # winner" doesn't match every in-progress game.
        "winner": (details.get("winner") or None),
        "last_play": last_play.get("description"),
        "last_play_ts": _as_int(last_play.get("occurence_ts")),
        "source_updated_ts": _as_int(details.get("last_updated_ts")),
    }


def _fingerprint(fields: dict) -> str:
    """What counts as "the game state changed". Deliberately excludes
    observed_at and source_updated_ts - a clock that ticks while nothing
    happens is not a state change worth a row, but a score, period, status
    or play change is."""
    return "|".join(str(fields.get(k)) for k in (
        "status", "widget_status", "home_score", "away_score",
        "period", "clock", "winner", "last_play",
    ))


# Minimum gap between stored rows for one event, for payloads whose content
# changes continuously. Crypto live-data carries OHLC candlesticks and a
# price timeseries that differ on essentially every poll, so fingerprint
# deduplication alone would not bound the volume - a sport's score changes a
# handful of times an hour, a candlestick array changes every tick.
_MIN_INTERVAL_SEC = 60.0
_last_write_at: dict[str, float] = {}
_last_flush_error: str | None = None


def record(event_ticker: str, details: dict, sport: str | None = None,
           event_type: str | None = None, now: float | None = None) -> bool:
    """Persist one live-data observation. Returns whether a row was buffered
    - False for an unchanged payload (deduplicated), one inside the
    per-event minimum interval, a missing event ticker, or any error.

    Handles ANY live-data shape, not just games: Kalshi's `type` field
    distinguishes `football_game` from `crypto`, and the crypto payload
    carries OHLC candlesticks plus an underlying price timeseries rather
    than a score. Sport-specific columns stay NULL for those, and the whole
    payload lands in raw_json either way - a shape this module has never
    seen still gets its complete record stored from the day it appears,
    which is the entire discipline here.

    Never raises: called from the trading loop, where an exception would
    cost the tick."""
    try:
        if not event_ticker or not details:
            return False
        # Real live bloat found 2026-08-23: crypto's OHLC-candlesticks-plus-
        # price-timeseries payload (see this function's own docstring) gets
        # re-stored in FULL on every write, and the array only grows over a
        # market's life - _MIN_INTERVAL_SEC above bounds write FREQUENCY,
        # not per-write PAYLOAD size, so successive writes for the same
        # event overwhelmingly duplicate what the previous write already
        # stored. Measured live: 14,204 crypto rows averaging 403KB each -
        # 5.72GB, 99.4% of this table's total size - against zero callers
        # of this module's own timeline() anywhere in the codebase (grepped
        # to confirm, not assumed), i.e. nothing has ever read a single one
        # of these rows back. The underlying price data this would capture
        # is already recorded with far better fidelity by the WS-based
        # services/index_feed/ (~1 msg/sec vs this REST path's 60s-rate-
        # limited snapshots) - this table's own schema (home_score/period/
        # clock/winner/...) is sports-shaped anyway, so a crypto payload
        # never populated any of it. Not a data-retention question (nothing
        # here was ever a load-bearing historical asset to begin with, per
        # the zero-consumer finding) - a wrong-module, wrong-shape write
        # path. Sports/commodity event types are unaffected (commodity's
        # own footprint measured negligible - 576 rows, 30MB total).
        if (event_type or details.get("type")) == "crypto":
            return False
        now = now if now is not None else time.time()
        last = _last_write_at.get(event_ticker)
        if last is not None and (now - last) < _MIN_INTERVAL_SEC:
            return False
        fields = extract(details, sport)
        fp = _fingerprint(fields)
        if fp == "None|None|None|None|None|None|None|None":
            # No sport-shaped field matched at all (e.g. a crypto payload),
            # so the generic fingerprint carries no information - fall back
            # to the payload itself so genuine changes still register and
            # identical repeats still don't.
            fp = str(hash(json.dumps(details, sort_keys=True, default=str)))
        if _last_fingerprint.get(event_ticker) == fp:
            return False
        _last_fingerprint[event_ticker] = fp
        _last_write_at[event_ticker] = now
        row = (
            event_ticker, sport, event_type or details.get("type"), now,
            fields["source_updated_ts"], fields["status"], fields["widget_status"],
            fields["home_score"], fields["away_score"], fields["period"],
            fields["period_label"], fields["clock"], fields["winner"],
            fields["last_play"], fields["last_play_ts"],
            json.dumps(details, default=str),
        )
        with _buffer_lock:
            _buffer.append(row)
            should_flush = len(_buffer) >= _FLUSH_BATCH
        if should_flush:
            flush()
        return True
    except Exception as exc:
        fault_log.record("game_state", "record", exc)
        return False


def flush() -> dict:
    global _buffer
    # _buffer_lock: the swap-and-clear must be atomic with record()'s own
    # append and with a concurrent second flush() call - see
    # services/series_watcher.py's own _buffer_lock comment for why. The DB
    # write itself stays outside the lock.
    with _buffer_lock:
        rows, _buffer = _buffer, []
    if not rows:
        return {"rows": 0}
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT INTO game_states (event_ticker, sport, event_type, observed_at, "
                "source_updated_ts, status, widget_status, home_score, away_score, period, "
                "period_label, clock, winner, last_play, last_play_ts, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
    except Exception as exc:
        # Surface the reason, don't just count the loss. A silent drop here
        # already cost this module every row it should have written: the
        # store read as "no games happening" when it was actually raising on
        # every insert. A capture layer that fails quietly is worse than one
        # that fails loudly, because the whole point is being trusted while
        # nobody is watching.
        #
        # fault_log.record (2026-09-01 fix): logger.exception()/
        # _last_flush_error alone reach the app log and this module's own
        # stats(), but not /api/health/faults, tools/soak_analyzer.py, or
        # capture_writer.py's loss-accounting siblings - series_watcher.py/
        # settlement_edge.py/index_feed/ingestion.py's own flush() all
        # already call fault_log.record on failure; this one didn't, the
        # one inconsistency found while investigating why a real, live
        # "database is locked" flush collision on this store (introduced by
        # main.py's _flush_secondary_capture_stores now calling flush() from
        # a second thread) went unmeasured by this app's own health
        # endpoints - exactly the "cost game_state every row it should have
        # written on 2026-08-17" gap this function's own comment already
        # named.
        global _last_flush_error
        _last_flush_error = str(exc)
        logger.exception("flush failed, %d row(s) dropped", len(rows))
        fault_log.record("game_state", "flush", exc, context=f"{len(rows)} row(s) dropped")
        return {"rows": 0, "dropped": len(rows), "error": str(exc)}
    return {"rows": len(rows)}


def prune_crypto_backlog(vacuum: bool = True) -> dict:
    """One-time cleanup companion to the 2026-08-23 fix above (see record's
    own docstring) - deletes every already-accumulated crypto row
    regardless of age, then reclaims the freed disk space via VACUUM
    (SQLite does not shrink a file on DELETE alone; the freed pages just
    become reusable free-list space inside the same file). The write path
    is already fixed, so no more crypto rows will ever be added going
    forward - this only ever needs to run once, against the backlog that
    predates that fix. Not wired into any recurring loop or route on
    purpose - a manual, deliberate one-time operation, not a mechanism.

    VACUUM cannot run inside an explicit transaction, hence the separate
    autocommit-mode connection rather than reusing _connect()'s default
    (implicit-transaction) behavior."""
    conn = sqlite3.connect(DB_PATH)
    conn.isolation_level = None  # autocommit - required for VACUUM below
    try:
        rows_before = conn.execute("SELECT COUNT(*) FROM game_states").fetchone()[0]
        cur = conn.execute("DELETE FROM game_states WHERE event_type = 'crypto'")
        deleted = cur.rowcount
        if vacuum:
            conn.execute("VACUUM")
        rows_after = conn.execute("SELECT COUNT(*) FROM game_states").fetchone()[0]
    finally:
        conn.close()
    return {"rows_before": rows_before, "rows_deleted": deleted, "rows_after": rows_after}


def prune(retention_hours: float = 168.0, now: float | None = None) -> dict:
    """Drop observations older than the retention window.

    Needed more here than anywhere else in the app: a crypto live-data
    payload carries a full candlestick array plus a price timeseries, so a
    single row is orders of magnitude larger than a trade print. Measured
    2026-08-17, this store reached 32MB within minutes of starting to write.
    Unbounded, it would be the largest file on disk inside a week."""
    now = now if now is not None else time.time()
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM game_states WHERE observed_at < ?",
                               (now - retention_hours * 3600,))
            return {"deleted": cur.rowcount}
    except Exception as exc:
        fault_log.record("game_state", "prune", exc)
        return {"deleted": 0, "error": str(exc)}


def timeline(event_ticker: str, limit: int = 500) -> list[dict]:
    """Every recorded state for one event, oldest first - the shape a later
    analysis wants when aligning game progress against market prices."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT * FROM game_states WHERE event_ticker = ? ORDER BY observed_at LIMIT ?",
                (event_ticker, limit),
            )]
    except sqlite3.Error:
        return []


def stats() -> dict:
    try:
        with _connect() as conn:
            n, events, first, last = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT event_ticker), MIN(observed_at), MAX(observed_at) "
                "FROM game_states").fetchone()
            scored = conn.execute(
                "SELECT COUNT(*) FROM game_states WHERE home_score IS NOT NULL").fetchone()[0]
            by_sport = dict(conn.execute(
                "SELECT COALESCE(sport,'unknown'), COUNT(*) FROM game_states GROUP BY 1").fetchall())
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    return {
        "observations": n, "distinct_events": events, "with_score": scored,
        "first_at": first, "last_at": last, "by_sport": by_sport,
        "buffered": len(_buffer), "last_flush_error": _last_flush_error,
    }
