"""
Live CF Benchmarks / Pyth index tick capture - the streamed-in half of what
was one flat services/index_feed.py, split 2026-08-23 (per-module audit)
into services/index_feed/ (this package): ingestion (this file) vs.
settlement_algebra.py (the settlement-projection math built on top of it).
See services/index_feed/__init__.py for the split's full rationale and a
real monkeypatch gotcha it's worth reading before touching this file's
tests.

Direct goal (2026-08-17): "get ready to make this a microsecond-reactive
trading platform." This module is the reason that phrase can mean something
concrete here, because for the 15-minute crypto series it is not a signal -
it is partial knowledge of the answer, delivered before the market closes.

DISCIPLINE

- Read-only with respect to trading: this module records and computes,
  nothing here places or closes anything.
- Buffered writes, same reasoning as services/series_watcher.py - ticks
  arrive ~1/second per index, and a per-tick sqlite3.connect() on the event
  loop is the pattern that froze the app on 2026-08-11.
"""
import json
import sqlite3
import time
from pathlib import Path

from services import fault_log

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "index_feed.db"

# CF Benchmarks index IDs this app cares about by default. BRTI is the one
# KXBTC15M/KXBTCD settle against (see settlement_algebra.py); ETHUSD_RTI is
# its ETH counterpart. "all" is supported by the channel and is what the
# config uses when nothing narrower is set.
DEFAULT_INDEX_IDS = ["BRTI", "ETHUSD_RTI"]

# services/index_feed/backfill.py (issue #260) stores historical CF
# Benchmarks points fetched via the REST passthrough (docs/kalshi/
# rest-passthrough.md) after a WS reconnect gap under this source value -
# same table, same shape as a live "cfbenchmarks" row, told apart only by
# this column, per the fidelity rule (store what Kalshi sent, no lossy
# normalization either way).
BACKFILL_SOURCE = "cfbenchmarks_backfill"

_latest: dict[str, dict] = {}
_tick_buffer: list[tuple] = []
_FLUSH_BATCH = 200
_dropped_rows = 0


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS index_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            index_id TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at REAL NOT NULL,
            received_at_ms INTEGER,
            source_ts_ms INTEGER,
            value REAL,
            avg_60s_value REAL,
            avg_60s_window_size INTEGER,
            q15_value REAL,
            q15_window_size INTEGER,
            raw_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_index_ticks ON index_ticks (index_id, observed_at)")
    # Only rows inside a settlement window carry q15_window_size, and those
    # are the ones every later reconstruction of "what did we know, when"
    # will query - worth their own partial index rather than scanning the
    # full ~86k rows/day/index that the 1Hz stream produces.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_index_ticks_q15 ON index_ticks (index_id, observed_at) "
        "WHERE q15_window_size IS NOT NULL"
    )
    return conn


def _float(value) -> float | None:
    """CF Benchmarks and Pyth both emit values as STRINGS formatted to 8
    decimal places (cfbenchmarks-value.md / pyth-value.md), never as JSON
    numbers - so every one is converted explicitly rather than assumed
    numeric because one sample happened to parse."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_cf_data(raw: str | None) -> float | None:
    """The `data` field is "the raw CF Benchmarks JSON frame, as a string" -
    a nested JSON document, not an object, so it needs a second parse. Its
    own `value` is the untouched upstream index level, which is the thing
    to compare a projection against; Kalshi's own averages sit beside it."""
    if not raw:
        return None
    try:
        return _float((json.loads(raw) or {}).get("value"))
    except (ValueError, TypeError, AttributeError):
        return None


def record_cfbenchmarks(msg: dict, now: float | None = None) -> bool:
    """Persist one `cfbenchmarks_value` message and update the in-memory
    latest. Never raises - this runs on the websocket handler."""
    try:
        index_id = msg.get("index_id")
        if not index_id:
            return False
        now = now if now is not None else time.time()
        avg60 = msg.get("avg_60s_data") or {}
        q15 = msg.get("last_60s_windowed_average_15min") or {}
        spot = _parse_cf_data(msg.get("data"))
        entry = {
            "index_id": index_id,
            "source": "cfbenchmarks",
            "observed_at": now,
            "received_at_ms": msg.get("received_at"),
            "value": spot,
            "avg_60s_value": _float(avg60.get("value")),
            "avg_60s_window_size": avg60.get("window_size"),
            # Present ONLY during the final minute before a quarter-hour
            # close. Its absence is meaningful (we are not in a settlement
            # window), so it is stored as NULL rather than defaulted to
            # anything.
            "q15_value": _float(q15.get("value")) if q15 else None,
            "q15_window_size": q15.get("window_size") if q15 else None,
            "q15_window_end_ts_ms": q15.get("window_end_ts_exclusive") if q15 else None,
        }
        _latest[index_id] = entry
        _tick_buffer.append((
            index_id, "cfbenchmarks", now, msg.get("received_at"), None, spot,
            entry["avg_60s_value"], entry["avg_60s_window_size"],
            entry["q15_value"], entry["q15_window_size"],
            json.dumps(msg, default=str),
        ))
        if len(_tick_buffer) >= _FLUSH_BATCH:
            flush()
        return True
    except Exception as exc:
        fault_log.record("index_feed", "record", exc)
        return False


def record_pyth(msg: dict, now: float | None = None) -> bool:
    """Persist one `pyth_value` message. Pyth carries no windowed averages -
    it is a straight price for an underlying ticker - so the settlement-
    projection fields stay NULL and only `value` is populated."""
    try:
        ticker = msg.get("underlying_ticker")
        if not ticker:
            return False
        now = now if now is not None else time.time()
        value = _float(msg.get("value_usd"))
        _latest[ticker] = {
            "index_id": ticker, "source": "pyth", "observed_at": now,
            "received_at_ms": msg.get("received_at"), "source_ts_ms": msg.get("source_ts_ms"),
            "value": value, "avg_60s_value": None, "avg_60s_window_size": None,
            "q15_value": None, "q15_window_size": None,
        }
        _tick_buffer.append((
            ticker, "pyth", now, msg.get("received_at"), msg.get("source_ts_ms"),
            value, None, None, None, None, json.dumps(msg, default=str),
        ))
        if len(_tick_buffer) >= _FLUSH_BATCH:
            flush()
        return True
    except Exception:
        return False


def last_tick_before(index_id: str, before_ts: float) -> float | None:
    """Most recent `observed_at` for index_id strictly before before_ts -
    the true start of a reconnect gap (services/index_feed/backfill.py,
    issue #260), not just the running MAX(observed_at), which could
    already include ticks recorded AFTER the reconnect by the time a
    periodic gap check actually runs (the WS starts streaming again well
    before a 10s-interval check task next executes). Flushes the buffer
    first so a tick still sitting in _tick_buffer isn't missed."""
    flush()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT MAX(observed_at) FROM index_ticks WHERE index_id = ? AND observed_at < ?",
                (index_id, before_ts),
            ).fetchone()
    except sqlite3.Error as exc:
        fault_log.record("index_feed", "last_tick_before", exc)
        return None
    return row[0] if row and row[0] is not None else None


def record_cfbenchmarks_backfill(index_id: str, points: list[dict], now: float | None = None) -> int:
    """Persist historical CF Benchmarks points fetched via the REST
    passthrough (services/index_feed/backfill.py, docs/kalshi/
    rest-passthrough.md) to backfill a WS reconnect gap. Each entry in
    `points` is one raw historical value object exactly as Kalshi/CF
    Benchmarks returned it - stored as `raw_json` verbatim (fidelity rule:
    no lossy normalization on the way in), with `value`/`source_ts_ms` read
    out defensively the same way record_cfbenchmarks/_parse_cf_data already
    do for the live stream.

    Deliberately never touches `_latest`: unlike record_cfbenchmarks, this
    runs well after the gap it's filling - by the time a backfill
    completes, a fresher live tick has already arrived (that arrival is
    *why* the gap is now known to be over), so backfilled history must
    never regress the in-memory "current value" backward in time. Never
    raises - same contract as record_cfbenchmarks/record_pyth.

    `observed_at` is derived from each point's OWN historical timestamp
    (source_ts_ms/1000), never from `now` (the wall-clock time the backfill
    itself ran) - CLAUDE.md's accuracy rule ("a value means exactly what
    its label says") applies here: a point observed_at time would otherwise
    read as "just happened" when it actually happened during the outage,
    corrupting every later MIN/MAX(observed_at) query and volatility/
    settlement-window read keyed on that column
    (last_tick_before/tick_stats/recent_volatility). `now` is only a
    fallback for the (should-not-happen - callers filter this case out
    before calling here) case of a point with no derivable timestamp at
    all, and that fallback is logged rather than silent."""
    now = now if now is not None else time.time()
    stored = 0
    for point in points or []:
        if not isinstance(point, dict):
            continue
        try:
            source_ts_ms = point.get("time")
            if source_ts_ms is None:
                source_ts_ms = point.get("timestamp", point.get("ts"))
            value = _float(point.get("value") if "value" in point else point.get("price"))
            if isinstance(source_ts_ms, (int, float)) and not isinstance(source_ts_ms, bool):
                observed_at = source_ts_ms / 1000.0
            else:
                observed_at = now
                fault_log.record_fault(
                    "index_feed", "record_cfbenchmarks_backfill",
                    f"{index_id} backfill point had no usable time field - stored under the backfill "
                    "wall-clock time instead of its own historical timestamp",
                    severity="warn",
                )
            _tick_buffer.append((
                index_id, BACKFILL_SOURCE, observed_at, None, source_ts_ms, value,
                None, None, None, None, json.dumps(point, default=str),
            ))
            stored += 1
        except Exception as exc:
            fault_log.record("index_feed", "record_cfbenchmarks_backfill", exc)
    if stored:
        # Backfill is rare (only on reconnect) and low-volume per event -
        # persist immediately rather than waiting for the live stream's
        # _FLUSH_BATCH threshold, so a backfilled gap is durable right away
        # instead of sitting in memory behind whatever the live stream
        # happens to be buffering.
        flush()
    return stored


def flush() -> dict:
    global _tick_buffer, _dropped_rows
    rows, _tick_buffer = _tick_buffer, []
    if not rows:
        return {"ticks": 0}
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT INTO index_ticks (index_id, source, observed_at, received_at_ms, "
                "source_ts_ms, value, avg_60s_value, avg_60s_window_size, q15_value, "
                "q15_window_size, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
    except Exception as exc:
        _dropped_rows += len(rows)
        fault_log.record("index_feed", "flush", exc, context=f"{len(rows)} tick(s) dropped")
        return {"ticks": 0, "dropped": len(rows)}
    return {"ticks": len(rows)}


def prune(retention_hours: float = 168.0, now: float | None = None) -> dict:
    """Drop index ticks older than the retention window. At ~2 ticks/second
    across two indices this is ~172k rows/day, so it needs a bound to sit
    unattended for a week."""
    now = now if now is not None else time.time()
    try:
        with _connect() as conn:
            cur = conn.execute("DELETE FROM index_ticks WHERE observed_at < ? "
                               "AND q15_window_size IS NULL",
                               (now - retention_hours * 3600,))
            return {"deleted": cur.rowcount}
    except Exception as exc:
        fault_log.record("index_feed", "prune", exc)
        return {"deleted": 0, "error": str(exc)}


def latest(index_id: str) -> dict | None:
    return _latest.get(index_id)


def snapshot() -> dict:
    """Everything currently known, for /api/state and the dashboard."""
    return {
        "indices": dict(_latest),
        "buffered_ticks": len(_tick_buffer),
        "dropped_rows": _dropped_rows,
    }


def tick_stats(index_id: str | None = None) -> dict:
    try:
        with _connect() as conn:
            if index_id:
                n, first, last = conn.execute(
                    "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM index_ticks "
                    "WHERE index_id = ?", (index_id,),
                ).fetchone()
            else:
                n, first, last = conn.execute(
                    "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM index_ticks"
                ).fetchone()
            in_window = conn.execute(
                "SELECT COUNT(*) FROM index_ticks WHERE q15_window_size IS NOT NULL"
                + (" AND index_id = ?" if index_id else ""),
                (index_id,) if index_id else (),
            ).fetchone()[0]
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    return {
        "index_id": index_id, "ticks": n, "first_at": first, "last_at": last,
        "settlement_window_ticks": in_window,
        "buffered": len(_tick_buffer), "dropped_rows": _dropped_rows,
    }
