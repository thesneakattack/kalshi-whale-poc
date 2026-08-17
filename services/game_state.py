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

docs/kalshi/get-live-data.md's verified football_game example carries
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
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "game_state.db"

# Buffered for the same reason every other capture path in this app is: the
# live-status pass can touch many events in one tick, and a per-event
# connect+insert on the event loop is the shape that froze the app on
# 2026-08-11.
_buffer: list[tuple] = []
_FLUSH_BATCH = 100
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_event ON game_states (event_ticker, observed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gs_time ON game_states (observed_at)")
    return conn


# Per-sport field names for the same underlying concept. Football is the one
# shape verified against a real payload (docs/kalshi/get-live-data.md, real
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


def record(event_ticker: str, details: dict, sport: str | None = None,
           now: float | None = None) -> bool:
    """Persist one game-state observation. Returns whether a row was
    buffered - False for an unchanged state (deduplicated), a missing event
    ticker, or any error.

    Never raises: this is called from the trading loop's live-status pass,
    where an exception would cost the tick."""
    try:
        if not event_ticker or not details:
            return False
        fields = extract(details, sport)
        fp = _fingerprint(fields)
        if _last_fingerprint.get(event_ticker) == fp:
            return False
        _last_fingerprint[event_ticker] = fp
        _buffer.append((
            event_ticker, sport, now if now is not None else time.time(),
            fields["source_updated_ts"], fields["status"], fields["widget_status"],
            fields["home_score"], fields["away_score"], fields["period"],
            fields["period_label"], fields["clock"], fields["winner"],
            fields["last_play"], fields["last_play_ts"],
            json.dumps(details, default=str),
        ))
        if len(_buffer) >= _FLUSH_BATCH:
            flush()
        return True
    except Exception:
        return False


def flush() -> dict:
    global _buffer
    rows, _buffer = _buffer, []
    if not rows:
        return {"rows": 0}
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT INTO game_states (event_ticker, sport, observed_at, source_updated_ts, "
                "status, widget_status, home_score, away_score, period, period_label, clock, "
                "winner, last_play, last_play_ts, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
    except Exception:
        return {"rows": 0, "dropped": len(rows)}
    return {"rows": len(rows)}


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
        "buffered": len(_buffer),
    }
