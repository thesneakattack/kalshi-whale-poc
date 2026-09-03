"""
End-to-end watcher for a single Kalshi series — captures the raw exchange
data this app otherwise throws away, then reconciles "how often were the
whales right" against "how often did I actually win."

Direct request (2026-08-17): "build a watcher file that can plug into
diagnostics for how the KXBTC15M series performs end to end compared to win
rates. if whale are winning at 70% I shouldnt be winning at 40. and keep in
mind all the api data you keep shaving off that ends up making your tasks
harder."

Two halves, and the first exists because of the second.

CAPTURE (record_trade / record_book) — the app currently discards most of
what Kalshi hands it. `main.py::_process_stream_ticker` receives a full
`ticker` websocket message and keeps exactly two fields
(`yes_bid_dollars`, `yes_ask_dollars`), dropping `yes_bid_size_fp`,
`yes_ask_size_fp`, `open_interest_fp`, `volume_fp`, `dollar_volume`,
`dollar_open_interest`, `last_trade_size_fp` and `ts_ms` on the floor
(field list per docs/kalshi/market-ticker.md). Those are precisely the
fields needed to answer why a signal that was directionally *right* still
lost money — was there depth at the whale's price, how wide was the
spread when I crossed it, how big was this print relative to the market's
own open interest. Every such question has, so far, been unanswerable
after the fact, because nothing kept the data. Same for trades: the
provider reads a print, derives a side and a notional, and the original
message is gone. So this module writes the FULL payload
(`raw_json`) alongside the columns it knows it wants — a field Kalshi adds
next month is captured from the day it appears, without a migration.

ANALYSIS (funnel / reconcile) — read-only, and specifically built around
the fact that "whale accuracy" and "my win rate" are not the same
measurement and are not supposed to be equal:

  - Whale accuracy asks a DIRECTIONAL question, settled at expiry: did the
    side the taker took end up paying $1?
  - My win rate asks a P&L question, settled whenever I exited: did this
    position close above what I paid for it?

Three things sit between them, all measurable, and `reconcile()` measures
each separately rather than blaming the signal:

  1. SELECTION — I don't trade every signal. entry_threshold, the price
     band, cooldowns and runway gates pick a subset, and that subset can be
     better or worse than the population it came from.
  2. EXIT — a stop-loss converts an eventually-correct signal into a
     realised loss. A signal can be 100% "correct" at settlement and still
     be a 100% loss rate for me if I stop out of every one before expiry.
  3. PRICE - the one that makes 70% accuracy insufficient rather than
     merely disappointing. A contract bought at unit cost c pays $1 or $0
     and the fill pays a taker fee, so expected value per contract is
     exactly (p - c - fee): breakeven accuracy is the entry price PLUS the
     fee (kalshi_fees.breakeven_unit_cost). At c = 0.80 that bar is 81.12%,
     so a 70%-accurate signal loses 11.12c per contract, forever, no matter
     how well it's managed.

Everything degrades honestly: a stage that cannot be computed reports None
with a stated reason, never a filled-in guess. This module exists to be
trusted when the dashboard's numbers are already in doubt.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

from services import capture_writer
from services import fault_log
from services import kalshi_fees
from services import signal_log
from services.history import trade_analytics
from services import paper_broker as pb_module
from services.diagnostics.diagnostics import Check
from services.diagnostics import _aio_db
from services.kalshi.contracts.trade import resolve_taker_outcome_side, taker_notional_usd, trade_exchange_ts

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "series_watcher.db"

# The series this was built for, and the default when nothing is configured
# — the user's standing end-to-end test case (a 15-minute BTC market, so a
# full open/trade/close/settle lifecycle completes every quarter hour,
# which is what makes it usable as a feedback loop at all).
DEFAULT_SERIES = "KXBTC15M"

# A `ticker` channel message fires on ANY field change, which on a liquid
# market is many times a second. Book snapshots are a sampled time series,
# not an audit log, so one row per ticker per interval is plenty to
# reconstruct spread/depth around an entry — and keeps a week of history in
# the low megabytes instead of the low gigabytes.
_DEFAULT_BOOK_INTERVAL_SEC = 5.0

# How far from an entry a book snapshot may be and still be treated as
# describing the book that entry crossed. Deliberately tight: a stale
# snapshot answering "what was the spread when I bought" is worse than no
# answer, since it looks like data.
_BOOK_MATCH_WINDOW_SEC = 30.0

_last_book_write: dict[str, float] = {}

# Buffered writes (2026-08-17, exchange-wide subscription). record_book is
# called once per inbound websocket ticker message, and with the trade
# channel subscribed exchange-wide that was originally thousands per
# minute for trades too. A per-message sqlite3.connect() + INSERT on the
# event loop is precisely the pattern that froze the whole app for minutes
# on 2026-08-11 (see kalshi_trade_tape.fetch_signals' docstring) - so rows
# accumulate in memory and land as one executemany per batch instead.
#
# Trade capture moved off this module's own buffer (2026-08-27, realtime
# data-plane remediation P3 Task 15): record_trade now calls
# capture_writer.submit("raw_trades", row) directly - a shared daemon
# thread (services/capture_writer.py) owns raw_trades' batching/flush
# cadence and its own cross-thread locking entirely independently of this
# module. _book_buffer/_buffer_lock below are book-snapshots-only now; the
# trade-side version of every race this lock originally guarded against is
# tested in tests/test_capture_writer.py, not here.
#
# The trade-off is explicit: up to _FLUSH_BATCH book rows can be lost if
# the process dies uncleanly (capture_writer has its own, separate loss
# accounting for raw_trades - see its dropped_count()). That is acceptable
# here and nowhere else in this app - this store is an observability
# record, not trading state, and paper_broker/risk_manager still write
# through immediately.
#
# _buffer_lock (code-review fix, finding #2 - /code-review high pass
# against PR #23, originally guarding both buffers): a tick_executor
# worker thread (main.py's _flush_trade_capture_async, still calling this
# module's flush() for the book half) and the main asyncio event-loop
# thread (services/whale_stream/whale_stream_handlers.py's
# _process_stream_trade/_process_stream_ticker) both touch _book_buffer,
# and flush()'s swap-and-clear was never synchronized against a concurrent
# .append() or a concurrent second flush(). book_snapshots has no unique
# constraint (only an AUTOINCREMENT surrogate key), so an unsynchronized
# double-flush can insert the same buffered rows twice; the same race can
# also orphan an appended row into a buffer nothing ever flushes again - a
# silently lost row, no error, no drop counter increment. A plain
# threading.Lock (not asyncio.Lock - the two real callers are on different
# OS threads, not just different coroutines on one event loop) now guards
# every touch of _book_buffer: both record_book()'s .append() and flush()'s
# own swap-and-clear.
_buffer_lock = threading.Lock()
_book_buffer: list[tuple] = []
_FLUSH_BATCH = 500
# Hard ceiling if flush() somehow never runs - drop oldest rather than grow
# without bound. Reaching this means something is wrong upstream, so it's
# counted, not silent.
_MAX_BUFFER = 20000
_dropped_rows = 0
_quarantine_cache: tuple[float, bool] | None = None


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # Same WAL rationale as signal_log/paper_broker: this table is written
    # from the websocket hot path, which is exactly the bursty-writer
    # pattern that took the app down under rollback-journal mode on
    # 2026-08-11.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(capture_writer.RAW_TRADES_DDL_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_series ON raw_trades (series, observed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_ticker ON raw_trades (ticker, observed_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            observed_at REAL NOT NULL,
            exchange_ts REAL,
            price_dollars REAL,
            yes_bid_dollars REAL,
            yes_ask_dollars REAL,
            yes_bid_size_fp REAL,
            yes_ask_size_fp REAL,
            volume_fp REAL,
            open_interest_fp REAL,
            dollar_volume REAL,
            dollar_open_interest REAL,
            last_trade_size_fp REAL,
            raw_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_ticker ON book_snapshots (ticker, observed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_book_series ON book_snapshots (series, observed_at)")
    return conn


async def _ensure_schema_aio(conn) -> None:
    """Same DDL as _connect() above, run once per (loop, db_path) key via
    _aio_db.connection_for()'s schema_init hook - _connect() itself stays
    untouched (still used by every write-path function this plan doesn't
    convert). Shares capture_writer.RAW_TRADES_DDL_SQL with _connect()
    (2026-09-03, Task 3c of docs/superpowers/plans/2026-09-03-tier1-
    backend-hygiene.md) rather than a hand-kept-in-sync second copy - the
    plain SQL string works identically for both conn.execute(sql) (sync)
    and await conn.execute(sql) (aiosqlite), since only the caller's
    execute differs, not the string itself; both paths are exercised by
    tests/test_series_watcher.py."""
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(capture_writer.RAW_TRADES_DDL_SQL)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_series ON raw_trades (series, observed_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_trades_ticker ON raw_trades (ticker, observed_at)")
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS book_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            observed_at REAL NOT NULL,
            exchange_ts REAL,
            price_dollars REAL,
            yes_bid_dollars REAL,
            yes_ask_dollars REAL,
            yes_bid_size_fp REAL,
            yes_ask_size_fp REAL,
            volume_fp REAL,
            open_interest_fp REAL,
            dollar_volume REAL,
            dollar_open_interest REAL,
            last_trade_size_fp REAL,
            raw_json TEXT NOT NULL
        )
        """
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_book_ticker ON book_snapshots (ticker, observed_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_book_series ON book_snapshots (series, observed_at)")


def _cfg_section(cfg: dict | None) -> dict:
    return ((cfg or {}).get("series_watcher") or {})


def watched_series(cfg: dict | None = None) -> list[str]:
    """Which series this watcher captures. Deliberately a short allowlist
    rather than "everything": the whole point of keeping the raw payload is
    that it's verbose, and that only pays for itself on a series being
    actively investigated."""
    configured = _cfg_section(cfg).get("series")
    if isinstance(configured, str):
        configured = [configured]
    return [s for s in (configured or [DEFAULT_SERIES]) if s]


def capture_enabled(cfg: dict | None = None) -> bool:
    return bool(_cfg_section(cfg).get("enabled", True))


def _float(value) -> float | None:
    """Kalshi emits fixed-point quantities as STRINGS (docs/kalshi/
    get-trades.md's FixedPointDollars/FixedPointCount: "responses always
    emit 2 decimals" / "responses emit up to 6"), so every numeric field
    here arrives as text and has to be converted explicitly — not assumed
    to already be a number because one sample happened to parse."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# A13: exchange-timestamp precedence (ts_ms -> ts -> created_time) is
# trade-contract semantics, boundary-owned; same-object alias as with the
# direction/notional helpers above.
_exchange_ts = trade_exchange_ts


def record_trade(trade: dict, cfg: dict | None = None, now: float | None = None) -> bool:
    """Submits one trade-tape print in full to capture_writer for
    persistence (realtime data-plane remediation P3 Task 15 - previously
    this module batched trades in its own _trade_buffer; now it's a pure
    row-builder). Returns whether the row was ACCEPTED for capture, not
    whether it was written or is new - False for an unwatched series or
    capture being disabled; True for a duplicate trade_id too (dedup
    happens at flush time via raw_trades' trade_id PRIMARY KEY + INSERT OR
    IGNORE, not here - deliberately, since checking here would mean a
    second read against data this module no longer owns synchronously).

    Never raises: this is called from the websocket handler, where an
    exception would kill the stream. A watcher that silently misses a row
    is a degraded watcher; a watcher that stops the trading loop is a
    bug."""
    try:
        ticker = trade.get("ticker") or trade.get("market_ticker")
        trade_id = trade.get("trade_id")
        if not ticker or not trade_id or not capture_enabled(cfg):
            return False
        series = signal_log.series_of(ticker)
        if series not in watched_series(cfg):
            return False

        # Resolved through the provider's own _taker_side rather than a
        # second local copy of the outcome/book/legacy precedence — one
        # definition, so the watcher can never disagree with the signal
        # path about which side a print took (the exact failure mode the
        # 2026-08-17 taker_side audit found).
        # Canonical columns derive from the boundary's own direction/
        # notional semantics (A13) - the raw taker_* columns below stay as
        # pure archival copies of what the wire carried.
        side = resolve_taker_outcome_side(trade)
        notional = taker_notional_usd(trade, side) if side else None

        row = (
            str(trade_id), ticker, series, now if now is not None else time.time(),
            _exchange_ts(trade), trade.get("taker_outcome_side"), trade.get("taker_book_side"),
            trade.get("taker_side"), side, _float(trade.get("count_fp")),
            _float(trade.get("yes_price_dollars")), _float(trade.get("no_price_dollars")),
            notional, 1 if trade.get("is_block_trade") else 0,
            1 if _quarantine_active() else 0,
            json.dumps(trade, default=str),
        )
        # capture_writer owns raw_trades' batching/flush cadence and its
        # own cross-thread locking entirely (P3 Task 15) - this module's
        # _buffer_lock no longer guards trade capture at all.
        capture_writer.submit("raw_trades", row)
        return True
    except Exception as exc:
        fault_log.record("series_watcher", "record_trade", exc)
        return False


def record_book(ticker_msg: dict, cfg: dict | None = None, now: float | None = None) -> tuple[bool, bool]:
    """Persist one `ticker`-channel book snapshot in full — every field
    docs/kalshi/market-ticker.md documents, not just the two prices
    _process_stream_ticker keeps.

    Throttled per ticker (series_watcher.book_snapshot_interval_sec) since
    the channel fires on every field change. Same never-raises contract as
    record_trade.

    Returns (accepted, should_flush) - accepted is the original "was this
    row buffered" signal; should_flush tells the caller a buffer-full flush
    is now due, to be scheduled off the event loop rather than called
    inline here - event-loop-blocking elimination Fix 1, 2026-09-01. This
    function fires on every ticker-channel field change, making it plausibly
    the highest-frequency of the four functions this fix touches."""
    try:
        ticker = ticker_msg.get("market_ticker") or ticker_msg.get("ticker")
        if not ticker or not capture_enabled(cfg):
            return False, False
        series = signal_log.series_of(ticker)
        if series not in watched_series(cfg):
            return False, False

        now = now if now is not None else time.time()
        interval = float(_cfg_section(cfg).get("book_snapshot_interval_sec", _DEFAULT_BOOK_INTERVAL_SEC))
        last = _last_book_write.get(ticker)
        if last is not None and (now - last) < interval:
            return False, False
        _last_book_write[ticker] = now

        row = (
            ticker, series, now, _exchange_ts(ticker_msg),
            _float(ticker_msg.get("price_dollars")), _float(ticker_msg.get("yes_bid_dollars")),
            _float(ticker_msg.get("yes_ask_dollars")), _float(ticker_msg.get("yes_bid_size_fp")),
            _float(ticker_msg.get("yes_ask_size_fp")), _float(ticker_msg.get("volume_fp")),
            _float(ticker_msg.get("open_interest_fp")), _float(ticker_msg.get("dollar_volume")),
            _float(ticker_msg.get("dollar_open_interest")), _float(ticker_msg.get("last_trade_size_fp")),
            json.dumps(ticker_msg, default=str),
        )
        # _buffer_lock (finding #2) - see record_trade's own comment above.
        with _buffer_lock:
            _book_buffer.append(row)
            should_flush = len(_book_buffer) >= _FLUSH_BATCH
        return True, should_flush
    except Exception as exc:
        fault_log.record("series_watcher", "record_book", exc)
        return False, False


def _quarantine_active() -> bool:
    """data_quarantine.is_active() opens a SQLite connection, and this is
    now the exchange-wide hot path - so the answer is cached for a second
    rather than asked once per print. A quarantine window is minutes long
    at minimum; a one-second lag at its boundary cannot mislabel anything
    that matters."""
    global _quarantine_cache
    now = time.time()
    if _quarantine_cache is None or (now - _quarantine_cache[0]) > 1.0:
        from services import data_quarantine

        try:
            _quarantine_cache = (now, data_quarantine.is_active())
        except Exception:
            return False
    return _quarantine_cache[1]


def flush() -> dict:
    """Write buffered book snapshots as one executemany batch. Called from
    the trading loop each tick and automatically once the buffer reaches
    _FLUSH_BATCH - see the buffer declaration above for why this is
    batched rather than written per message. Trade capture no longer flows
    through here (P3 Task 15 routed it through capture_writer instead,
    which has its own independent flush cadence/loss-accounting) - this
    function is book_snapshots-only now.

    Never raises, for the same reason record_book doesn't: a failed
    observability write must not take down the trading loop that called
    it. On failure the rows are dropped rather than retried forever, and
    counted in _dropped_rows so the loss is visible in capture_stats
    instead of silent."""
    global _book_buffer, _dropped_rows
    # _buffer_lock (finding #2): the swap-and-clear itself must be atomic
    # with record_book's own append (above) and with a concurrent second
    # flush() call - without this, two flush() calls can both capture the
    # same not-yet-reset buffer (book_snapshots has no unique constraint,
    # so that means literal duplicate rows), or an append can land in a
    # buffer neither flush() call will ever read again (a silently lost
    # row). The DB write itself stays outside the lock - only the buffer
    # swap needs it, and holding a lock across blocking disk I/O would
    # needlessly serialize captures against a flush that's still writing.
    with _buffer_lock:
        books, _book_buffer = _book_buffer, []
    if not books:
        return {"books": 0}
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT INTO book_snapshots "
                "(ticker, series, observed_at, exchange_ts, price_dollars, yes_bid_dollars, "
                " yes_ask_dollars, yes_bid_size_fp, yes_ask_size_fp, volume_fp, open_interest_fp, "
                " dollar_volume, dollar_open_interest, last_trade_size_fp, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                books,
            )
    except Exception as exc:
        _dropped_rows += len(books)
        fault_log.record("series_watcher", "flush", exc,
                         context=f"{len(books)} book row(s) dropped")
        return {"books": 0, "dropped": len(books)}
    return {"books": len(books)}


def prune(retention_hours: float = 168.0, now: float | None = None) -> dict:
    """Drop book snapshots older than the retention window. Trades are NOT
    pruned — CLAUDE.md treats accumulated history as a first-class asset,
    and raw_trades is one row per real exchange print (small, and the
    dataset every later comparison rests on). Book snapshots are a sampled
    time series at ~1 row/5s/ticker, which is the only part of this store
    that grows without bound."""
    now = now if now is not None else time.time()
    cutoff = now - retention_hours * 3600
    with _connect() as conn:
        cur = conn.execute("DELETE FROM book_snapshots WHERE observed_at < ?", (cutoff,))
        return {"book_snapshots_deleted": cur.rowcount, "cutoff": cutoff}


async def capture_stats(series: str | None = None) -> dict:
    """What the capture half has actually collected — so a caller can tell
    "no whale prints happened" apart from "the watcher wasn't running,"
    which are the two explanations this whole module exists to
    distinguish."""
    series = series or DEFAULT_SERIES
    try:
        conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
        trades_cursor = await conn.execute(
            "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM raw_trades WHERE series = ?",
            (series,),
        )
        trades, first_t, last_t = await trades_cursor.fetchone()
        books_cursor = await conn.execute(
            "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM book_snapshots WHERE series = ?",
            (series,),
        )
        books, first_b, last_b = await books_cursor.fetchone()
    except sqlite3.Error as exc:
        return {"series": series, "error": str(exc)}
    return {
        "series": series,
        "raw_trades": trades, "raw_trades_first_at": first_t, "raw_trades_last_at": last_t,
        "book_snapshots": books, "book_first_at": first_b, "book_last_at": last_b,
        # Rows captured but not yet written. Reported so a count read
        # right after a burst isn't mistaken for data loss. buffered_trades
        # now reads capture_writer's own depth (P3 Task 15 - this module no
        # longer buffers trades itself); buffered_books is still this
        # module's own _book_buffer.
        "buffered_trades": capture_writer.depth().get("raw_trades", 0),
        "buffered_books": len(_book_buffer),
        # dropped_rows sums this module's own book-flush failures with
        # capture_writer's raw_trades losses on BOTH of its drop paths (a
        # non-retryable flush failure, and the retained-buffer cap after
        # lock collisions - issue #211) - all are real loss against the
        # SAME series_watcher-owned dataset, so a caller reading this field
        # shouldn't have to know the causes now live in different modules
        # to get an honest total. capture_writer.loss_snapshot() has the
        # breakdown.
        "dropped_rows": (_dropped_rows
                         + capture_writer.dropped_count().get("raw_trades", 0)
                         + capture_writer.overflow_dropped_count().get("raw_trades", 0)),
    }


# ------------------------------------------------------------------ reading

async def _signals_for_series(series: str, since_ts: float, before_ts: float) -> list[dict]:
    """Non-excluded signals only — an experiment window is real data about
    the exchange but is not evidence about the strategy (see
    signal_log.mark_excluded_range)."""
    try:
        conn = await _aio_db.connection_for(signal_log.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT ticker, side, size, confidence, seen_at, price, resolved, correct, "
            "raw_notional_usd FROM signals "
            "WHERE series = ? AND seen_at > ? AND seen_at <= ? AND excluded = 0",
            (series, since_ts, before_ts),
        )
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


async def _trades_for_series(series: str, since_ts: float, before_ts: float) -> list[dict]:
    """Every paper trade on this series in the window, chronological — both
    entries and closes, since trade_analytics.build_trade_history pairs them
    itself and needs the entry that precedes each close.

    Widened to `since_ts - 7 days` on the read side so a position opened
    before the window but closed inside it still finds its own entry;
    filtering happens on the close timestamp afterwards."""
    try:
        conn = await _aio_db.connection_for(pb_module.DB_PATH)
        rows = await conn.execute_fetchall(
            "SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, "
            "fee, signal_seen_at FROM trades "
            "WHERE ticker LIKE ? AND timestamp > ? AND timestamp <= ? AND excluded = 0 ORDER BY timestamp",
            (f"{series}-%", since_ts - 7 * 86400, before_ts),
        )
    except sqlite3.Error:
        return []
    # LIKE 'SERIES-%' is a prefix filter, not the series definition —
    # re-check against signal_log.series_of so a series whose name is a
    # prefix of another can't leak in.
    return [dict(r) for r in rows if signal_log.series_of(r["ticker"]) == series]


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 1) if denominator else None


# ------------------------------------------------------------------ funnel

async def funnel(series: str | None = None, hours: float = 24.0, cfg: dict | None = None,
                  now: float | None = None) -> dict:
    """Every stage from "a print happened on the exchange" to "a position
    closed", with the count at each — so a shortfall can be located at the
    stage it actually happened rather than blamed on whichever stage is
    most visible.

    Stage 1 (prints observed) reads this module's own raw_trades, so it is
    only populated for windows the watcher was actually running for. That
    is reported explicitly rather than papered over: an empty stage 1
    alongside a populated stage 2 means the capture half wasn't on, not
    that no whales traded."""
    series = series or DEFAULT_SERIES
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600

    min_contracts = float(
        ((cfg or {}).get("whale_watcher_kalshi") or {}).get("min_contracts_by_series", {}).get(series)
        or ((cfg or {}).get("whale_watcher_kalshi") or {}).get("min_contracts")
        or 0
    )

    try:
        conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
        cursor = await conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN count_fp >= ? THEN 1 ELSE 0 END), MIN(observed_at) "
            "FROM raw_trades WHERE series = ? AND observed_at > ? AND excluded = 0",
            (min_contracts, series, since_ts),
        )
        observed, whale_sized, capture_start = await cursor.fetchone()
        unreadable_cursor = await conn.execute(
            "SELECT COUNT(*) FROM raw_trades WHERE series = ? AND observed_at > ? "
            "AND resolved_side IS NULL",
            (series, since_ts),
        )
        unreadable_row = await unreadable_cursor.fetchone()
        unreadable_side = unreadable_row[0]
    except sqlite3.Error as exc:
        observed, whale_sized, unreadable_side, capture_start = None, None, None, None
        capture_error = str(exc)
    else:
        capture_error = None

    # The capture half only sees what it was running for, while every stage
    # below it reads a store that predates this module. Reporting both under
    # one "last 24h" heading would make "2,716 prints -> 2 whale-sized -> 397
    # signals" look like a catastrophic filter bug rather than two different
    # observation periods side by side - exactly the "a displayed value must
    # match its label" failure CLAUDE.md documents. So the covered fraction
    # is computed explicitly and named in the stage notes themselves.
    capture_hours = (now - capture_start) / 3600 if capture_start else 0.0
    capture_pct = round(min(100.0, 100.0 * capture_hours / hours), 1) if hours else None
    capture_note = (
        f"covers the last {capture_hours:.1f}h of this {hours:.0f}h window ({capture_pct}%) — "
        "the watcher only sees what it was running for"
        if capture_start else "the watcher captured nothing in this window"
    )

    signals = await _signals_for_series(series, since_ts, now)
    resolved = [s for s in signals if s["resolved"]]
    correct = [s for s in resolved if s["correct"]]

    raw = await _trades_for_series(series, since_ts, now)
    entries = [t for t in raw if not t["reason"].startswith("closed:") and t["timestamp"] > since_ts]
    history = [r for r in trade_analytics.build_trade_history(raw) if r["exit_timestamp"] > since_ts]
    wins = [r for r in history if r["won"]]

    stages = [
        {
            "stage": "prints_observed",
            "count": observed,
            "covers_window": capture_pct,
            "note": (
                f"raw exchange prints captured on {series} — {capture_note}" if capture_error is None
                else f"unreadable: {capture_error}"
            ),
        },
        {
            "stage": "whale_sized_prints",
            "count": whale_sized,
            "covers_window": capture_pct,
            "note": f"cleared the {min_contracts:,.0f}-contract min_contracts gate — {capture_note}",
        },
        {
            "stage": "signals_logged",
            "count": len(signals),
            "note": "reached signal_log (excludes quarantined experiment windows)",
        },
        {
            "stage": "signals_resolved",
            "count": len(resolved),
            "note": "settled, so directional accuracy is knowable",
        },
        {
            "stage": "signals_correct",
            "count": len(correct),
            "note": "the whale's side was the side that paid $1",
        },
        {
            "stage": "entries_taken",
            "count": len(entries),
            "note": "passed entry_threshold, the price band, cooldowns and runway gates",
        },
        {
            "stage": "positions_closed",
            "count": len(history),
            "note": "reached an exit (stop-loss, take-profit, settlement or auto-exit)",
        },
        {
            "stage": "positions_won",
            "count": len(wins),
            "note": "closed with realised P&L above zero",
        },
    ]

    return {
        "series": series,
        "window_hours": hours,
        "since_ts": since_ts,
        "generated_at": now,
        "min_contracts": min_contracts,
        "stages": stages,
        "capture_active": bool(observed),
        # What fraction of the analysis window the first two stages actually
        # cover. Below 100 the funnel is two observation periods stacked,
        # and the print counts must NOT be read as the population the
        # signal counts were drawn from.
        "capture_covers_window_pct": capture_pct,
        "capture_window_hours": round(capture_hours, 2),
        "prints_with_unreadable_side": unreadable_side,
        "signal_accuracy_pct": _pct(len(correct), len(resolved)),
        "signal_resolution_pct": _pct(len(resolved), len(signals)),
        "realised_win_rate_pct": _pct(len(wins), len(history)),
        "conversion_pct": _pct(len(entries), len(signals)),
    }


# ------------------------------------------------------------------ reconcile

async def reconcile(series: str | None = None, hours: float = 24.0, cfg: dict | None = None,
                     now: float | None = None) -> dict:
    """The actual question: whales are right ~70% of the time, so why is my
    win rate ~40%?

    Splits the gap into two additive count-based components plus a separate
    money-based one that is NOT part of the win-rate arithmetic but is the
    reason a healthy win rate can still lose money:

        realised_win_rate - signal_accuracy
            = selection_delta   (accuracy of the signals I traded, minus
                                 accuracy of all signals — did my gates pick
                                 better or worse than average?)
            + exit_delta        (my win rate on those same trades, minus the
                                 accuracy of the signals behind them — how
                                 many eventually-right calls did I exit at a
                                 loss before they settled?)

        edge_pts = signal_accuracy - breakeven_accuracy
            A contract at unit cost c pays $1 or $0 and its fill pays a
            taker fee, so EV per contract is exactly (p - c - fee) and
            BREAKEVEN ACCURACY IS THE ENTRY PRICE PLUS THE FEE. This is the
            component that makes "70% right" and "profitable" two unrelated
            statements.

    Signals are joined to trades on (ticker, signal_seen_at) — an exact
    key, not a heuristic: PaperBroker.Trade.signal_seen_at is written from
    the same WhaleSignal.timestamp that signal_log stores as seen_at (see
    main.py::_handle_signal and strategy_engine's open_position calls), and
    both round-trip through SQLite REAL unchanged."""
    series = series or DEFAULT_SERIES
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600

    signals = await _signals_for_series(series, since_ts, now)
    raw = await _trades_for_series(series, since_ts, now)
    history = [r for r in trade_analytics.build_trade_history(raw) if r["exit_timestamp"] > since_ts]

    resolved = [s for s in signals if s["resolved"]]
    correct = sum(1 for s in resolved if s["correct"])
    signal_accuracy = _pct(correct, len(resolved))

    # (ticker, seen_at) -> signal, for the entry->signal join below.
    by_key = {(s["ticker"], round(s["seen_at"], 6)): s for s in signals}

    entries = [t for t in raw if not t["reason"].startswith("closed:") and t["timestamp"] > since_ts]
    traded_signals, unjoined = [], 0
    for t in entries:
        seen = t.get("signal_seen_at")
        match = by_key.get((t["ticker"], round(seen, 6))) if seen is not None else None
        if match is None:
            unjoined += 1
            continue
        traded_signals.append(match)
    traded_resolved = [s for s in traded_signals if s["resolved"]]
    traded_correct = sum(1 for s in traded_resolved if s["correct"])
    traded_accuracy = _pct(traded_correct, len(traded_resolved))

    wins = sum(1 for r in history if r["won"])
    realised_win_rate = _pct(wins, len(history))

    selection_delta = (
        round(traded_accuracy - signal_accuracy, 1)
        if traded_accuracy is not None and signal_accuracy is not None else None
    )
    exit_delta = (
        round(realised_win_rate - traded_accuracy, 1)
        if realised_win_rate is not None and traded_accuracy is not None else None
    )

    # ------- the money view: what accuracy would these entry prices need?
    # Breakeven is the entry price PLUS the taker fee that fill really pays
    # (issue #205). Fee-free breakeven was displayed here until 2026-08-30
    # and understated the bar by 100*0.07*multiplier*c*(1-c) points - 1.75
    # at c = 0.50 down to 0.63 at c = 0.90, the band config/settings.yaml
    # enforces - which fed straight into edge_pts.
    #
    # Averaged per entry, not derived from mean_unit_cost: EV = 0 over an
    # entry set needs mean(c_i + fee_i), and the fee is concave in price
    # with a per-ticker multiplier, so mean(fee) is not fee(mean) and a
    # multiplier-0 series (KXBTCY, ...) genuinely has breakeven == entry
    # price. The gap is 0.07*multiplier*Var(c) — under 0.3pts within the
    # configured band, the whole fee on a multiplier-0 series — so the
    # headline below states the two numbers side by side rather than
    # claiming one is derived from the other.
    priced_entries = [
        (t, uc) for t, uc in ((t, kalshi_fees.unit_cost(t["side"], t["price"])) for t in entries)
        if uc is not None
    ]
    unit_costs = [uc for _, uc in priced_entries]
    mean_unit_cost = round(sum(unit_costs) / len(unit_costs), 4) if unit_costs else None
    breakevens = [
        kalshi_fees.breakeven_unit_cost(uc, ticker=t["ticker"]) for t, uc in priced_entries
    ]
    breakeven_accuracy_pct = (
        round(100.0 * sum(breakevens) / len(breakevens), 2) if breakevens else None
    )
    edge_pts = (
        round(signal_accuracy - breakeven_accuracy_pct, 1)
        if signal_accuracy is not None and breakeven_accuracy_pct is not None else None
    )

    # ------- named leaks, each measured independently
    # A stop-loss on a signal that later settled the whale's way is the
    # single most literal form of "the whale was right and I still lost":
    # the call was correct, the exit rule realised the drawdown before
    # settlement could pay it back.
    resolved_by_ticker: dict[str, list[dict]] = {}
    for s in resolved:
        resolved_by_ticker.setdefault(s["ticker"], []).append(s)
    stopped_but_correct, stopped_total = 0, 0
    for r in history:
        if r["close_type"] != "stop_loss":
            continue
        stopped_total += 1
        matches = [
            s for s in resolved_by_ticker.get(r["ticker"], [])
            if r["entry_timestamp"] is not None and s["seen_at"] <= r["entry_timestamp"] and s["side"] == r["side"]
        ]
        if matches and matches[-1]["correct"]:
            stopped_but_correct += 1

    # Slippage: what the whale's print cost per contract vs what my fill
    # cost. signal_log.price is always the yes-side implied probability at
    # print time, same convention as Trade.price, so both go through the
    # same side-aware inversion before being compared.
    #
    # Expect ~0 in paper mode and don't read that as "no slippage": the
    # paper broker fills at the price handed to open_position, which for a
    # whale-follow entry IS the signal's own price. This number only starts
    # measuring anything real once fills come from
    # kalshi_account_client.create_order. Kept anyway, because a NON-zero
    # value in paper mode means the entry path is pricing off something
    # other than the signal it claims to be following.
    slippage = []
    for t in entries:
        seen = t.get("signal_seen_at")
        match = by_key.get((t["ticker"], round(seen, 6))) if seen is not None else None
        if match is None or match.get("price") is None:
            continue
        signal_cost = kalshi_fees.unit_cost(t["side"], match["price"])
        fill_cost = kalshi_fees.unit_cost(t["side"], t["price"])
        if signal_cost is not None and fill_cost is not None:
            slippage.append(fill_cost - signal_cost)
    mean_slippage_pts = round(sum(slippage) / len(slippage) * 100, 2) if slippage else None

    lags = [r["time_to_open_sec"] for r in history if r.get("time_to_open_sec") is not None]
    fees = sum(r.get("fees_paid") or 0.0 for r in history)
    cost_basis = sum(r["cost_basis"] for r in history if r["cost_basis"])
    pnl = sum(r["realized_pnl"] for r in history if r["realized_pnl"] is not None)

    close_types: dict[str, dict] = {}
    for r in history:
        bucket = close_types.setdefault(r["close_type"] or "unknown", {"count": 0, "wins": 0, "pnl": 0.0})
        bucket["count"] += 1
        bucket["wins"] += 1 if r["won"] else 0
        bucket["pnl"] = round(bucket["pnl"] + (r["realized_pnl"] or 0.0), 2)

    return {
        "series": series,
        "window_hours": hours,
        "generated_at": now,
        "signals": len(signals),
        "signals_resolved": len(resolved),
        "signal_accuracy_pct": signal_accuracy,
        "entries": len(entries),
        "entries_unjoined_to_signal": unjoined,
        "traded_signals_resolved": len(traded_resolved),
        "traded_signal_accuracy_pct": traded_accuracy,
        "positions_closed": len(history),
        "realised_win_rate_pct": realised_win_rate,
        "selection_delta_pts": selection_delta,
        "exit_delta_pts": exit_delta,
        "mean_entry_unit_cost": mean_unit_cost,
        "breakeven_accuracy_pct": breakeven_accuracy_pct,
        "edge_pts": edge_pts,
        "stop_losses": stopped_total,
        "stop_losses_on_correct_signals": stopped_but_correct,
        "mean_entry_slippage_pts": mean_slippage_pts,
        "mean_signal_to_entry_sec": round(sum(lags) / len(lags), 2) if lags else None,
        "fees_paid": round(fees, 2),
        "fee_drag_pct_of_cost": round(100.0 * fees / cost_basis, 2) if cost_basis else None,
        "realised_pnl": round(pnl, 2),
        "close_type_breakdown": close_types,
    }


async def book_context_at_entry(series: str | None = None, hours: float = 24.0,
                                 now: float | None = None) -> dict:
    """Spread and resting depth at the moment each entry was opened, from
    the captured book snapshots — the question that was unanswerable before
    this module existed, because _process_stream_ticker kept only two of
    the ticker channel's fifteen documented fields.

    Returns "unknown" honestly when no snapshot sits within
    _BOOK_MATCH_WINDOW_SEC of an entry: a stale book reading is worse than
    no reading, because it looks like evidence."""
    series = series or DEFAULT_SERIES
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600

    raw = await _trades_for_series(series, since_ts, now)
    entries = [t for t in raw if not t["reason"].startswith("closed:") and t["timestamp"] > since_ts]
    if not entries:
        return {"series": series, "status": "unknown", "reason": "no entries in this window"}

    matched, spreads, depth_ratios = 0, [], []
    try:
        conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
        for t in entries:
            cursor = await conn.execute(
                "SELECT yes_bid_dollars, yes_ask_dollars, yes_bid_size_fp, yes_ask_size_fp, "
                "open_interest_fp FROM book_snapshots "
                "WHERE ticker = ? AND observed_at BETWEEN ? AND ? "
                "ORDER BY ABS(observed_at - ?) LIMIT 1",
                (t["ticker"], t["timestamp"] - _BOOK_MATCH_WINDOW_SEC,
                 t["timestamp"] + _BOOK_MATCH_WINDOW_SEC, t["timestamp"]),
            )
            snap = await cursor.fetchone()
            if snap is None:
                continue
            matched += 1
            bid, ask = snap["yes_bid_dollars"], snap["yes_ask_dollars"]
            if bid is not None and ask is not None:
                spreads.append(ask - bid)
            # Depth on the side I had to cross, against my own size —
            # a ratio below 1 means the resting book could not fill me
            # at the quoted price and the rest was paid up for.
            resting = snap["yes_ask_size_fp"] if t["side"] == "yes" else snap["yes_bid_size_fp"]
            if resting and t["size"]:
                depth_ratios.append(resting / t["size"])
    except sqlite3.Error as exc:
        return {"series": series, "status": "unknown", "reason": f"watcher store unreadable: {exc}"}

    if matched == 0:
        return {
            "series": series, "status": "unknown", "entries": len(entries),
            "reason": (
                "no book snapshot within "
                f"{_BOOK_MATCH_WINDOW_SEC:.0f}s of any entry — the capture half of this watcher "
                "was not running during this window, so spread/depth at entry is not reconstructable"
            ),
        }
    return {
        "series": series,
        "status": "ok",
        "entries": len(entries),
        "entries_with_book": matched,
        "mean_spread_pts": round(sum(spreads) / len(spreads) * 100, 2) if spreads else None,
        "mean_depth_ratio": round(sum(depth_ratios) / len(depth_ratios), 2) if depth_ratios else None,
        "entries_with_insufficient_depth": sum(1 for d in depth_ratios if d < 1.0),
    }


# ------------------------------------------------------------------ the Check

async def check_series_funnel(cfg: dict, series: str | None = None, hours: float = 24.0,
                               now: float | None = None) -> Check:
    """diagnostics-compatible wrapper — this is the plug. Returns the same
    Check shape every other check in services/diagnostics/diagnostics.py returns, so
    run_offline can include it and /api/diagnostics renders it with no
    special-casing."""
    series = series or (watched_series(cfg) or [DEFAULT_SERIES])[0]
    r = await reconcile(series, hours=hours, cfg=cfg, now=now)

    acc, win = r["signal_accuracy_pct"], r["realised_win_rate_pct"]
    if acc is None and win is None:
        return Check(
            f"series_funnel:{series}", "unknown",
            f"no resolved signals and no closed positions for {series} in the last {hours:.0f}h",
            detail=r,
        )
    if acc is None or win is None:
        missing = "resolved signals" if acc is None else "closed positions"
        return Check(
            f"series_funnel:{series}", "unknown",
            f"{series} has no {missing} in the last {hours:.0f}h — the accuracy-vs-win-rate "
            "comparison needs both sides",
            detail=r,
        )

    gap = round(win - acc, 1)
    parts = []
    if r["selection_delta_pts"] is not None:
        parts.append(f"{r['selection_delta_pts']:+.1f}pts from which signals were selected")
    if r["exit_delta_pts"] is not None:
        parts.append(f"{r['exit_delta_pts']:+.1f}pts from how positions were exited")

    # A negative edge is the finding that outranks the gap itself: it means
    # the entry price demands more accuracy than the signal has, so no
    # amount of exit tuning makes the series profitable.
    edge = r["edge_pts"]
    if edge is not None and edge < 0:
        status = "fail"
        headline = (
            f"whales {acc}% accurate, realised win rate {win}% ({gap:+.1f}pts) — and entries "
            f"averaged {r['mean_entry_unit_cost']:.3f}/contract and, at their own prices and "
            f"per-series fee rates, need {r['breakeven_accuracy_pct']:.2f}% accuracy after taker "
            f"fees just to break even, so this series is {abs(edge):.1f}pts underwater at the "
            f"price level regardless of exits"
        )
    elif gap <= -15:
        status = "fail"
        headline = f"whales {acc}% accurate but realised win rate is {win}% ({gap:+.1f}pts)"
    elif gap <= -5:
        status = "warn"
        headline = f"whales {acc}% accurate, realised win rate {win}% ({gap:+.1f}pts)"
    else:
        status = "ok"
        headline = f"whales {acc}% accurate, realised win rate {win}% ({gap:+.1f}pts)"

    if parts:
        headline += "; " + " and ".join(parts)
    if r["stop_losses_on_correct_signals"]:
        headline += (
            f"; {r['stop_losses_on_correct_signals']} of {r['stop_losses']} stop-losses fired on "
            "signals that went on to settle the whale's way"
        )

    return Check(
        f"series_funnel:{series}", status, headline,
        detail=r,
        evidence=(await funnel(series, hours=hours, cfg=cfg, now=now))["stages"],
    )
