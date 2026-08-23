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
  3. PRICE — the one that makes 70% accuracy insufficient rather than
     merely disappointing. A contract bought at unit cost c pays $1 or $0,
     so expected value per contract is exactly (p - c): breakeven accuracy
     IS the entry price. At c = 0.80, a 70%-accurate signal loses 10c per
     contract, forever, no matter how well it's managed.

Everything degrades honestly: a stage that cannot be computed reports None
with a stated reason, never a filled-in guess. This module exists to be
trusted when the dashboard's numbers are already in doubt.
"""
import json
import sqlite3
import time
from pathlib import Path

from services import fault_log
from services import signal_log, trade_analytics
from services import paper_broker as pb_module
from services.diagnostics.diagnostics import Check
from services.whalewatchers.kalshi_trade_tape import _notional_usd, _taker_side

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

# Buffered writes (2026-08-17, exchange-wide subscription). record_trade is
# now called once per inbound websocket trade message, and with the trade
# channel subscribed exchange-wide that is thousands per minute rather than
# the watchlist's handful. A per-message sqlite3.connect() + INSERT on the
# event loop is precisely the pattern that froze the whole app for minutes
# on 2026-08-11 (see kalshi_trade_tape.fetch_signals' docstring) - so rows
# accumulate in memory and land as one executemany per batch instead.
#
# The trade-off is explicit: up to _FLUSH_BATCH rows can be lost if the
# process dies uncleanly. That is acceptable here and nowhere else in this
# app - this store is an observability record, not trading state, and
# paper_broker/risk_manager still write through immediately.
_trade_buffer: list[tuple] = []
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_trades (
            trade_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            observed_at REAL NOT NULL,
            exchange_ts REAL,
            taker_outcome_side TEXT,
            taker_book_side TEXT,
            taker_side_legacy TEXT,
            resolved_side TEXT,
            count_fp REAL,
            yes_price_dollars REAL,
            no_price_dollars REAL,
            notional_usd REAL,
            is_block_trade INTEGER,
            excluded INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL
        )
        """
    )
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


def _exchange_ts(msg: dict) -> float | None:
    ms = msg.get("ts_ms")
    if ms is not None:
        try:
            return float(ms) / 1000.0
        except (TypeError, ValueError):
            pass
    secs = msg.get("ts")
    if secs is not None:
        try:
            return float(secs)
        except (TypeError, ValueError):
            pass
    from services.whalewatchers.kalshi_trade_tape import _parse_trade_time

    return _parse_trade_time(msg.get("created_time"))


def record_trade(trade: dict, cfg: dict | None = None, now: float | None = None) -> bool:
    """Persist one trade-tape print in full. Returns whether a row was
    written (False for an unwatched series, a duplicate trade_id, capture
    being disabled, or any storage error).

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
        side = _taker_side(trade)
        notional = _notional_usd(trade, side) if side else None

        _trade_buffer.append((
            str(trade_id), ticker, series, now if now is not None else time.time(),
            _exchange_ts(trade), trade.get("taker_outcome_side"), trade.get("taker_book_side"),
            trade.get("taker_side"), side, _float(trade.get("count_fp")),
            _float(trade.get("yes_price_dollars")), _float(trade.get("no_price_dollars")),
            notional, 1 if trade.get("is_block_trade") else 0,
            1 if _quarantine_active() else 0,
            json.dumps(trade, default=str),
        ))
        if len(_trade_buffer) >= _FLUSH_BATCH:
            flush()
        return True
    except Exception as exc:
        fault_log.record("series_watcher", "record_trade", exc)
        return False


def record_book(ticker_msg: dict, cfg: dict | None = None, now: float | None = None) -> bool:
    """Persist one `ticker`-channel book snapshot in full — every field
    docs/kalshi/market-ticker.md documents, not just the two prices
    _process_stream_ticker keeps.

    Throttled per ticker (series_watcher.book_snapshot_interval_sec) since
    the channel fires on every field change. Same never-raises contract as
    record_trade."""
    try:
        ticker = ticker_msg.get("market_ticker") or ticker_msg.get("ticker")
        if not ticker or not capture_enabled(cfg):
            return False
        series = signal_log.series_of(ticker)
        if series not in watched_series(cfg):
            return False

        now = now if now is not None else time.time()
        interval = float(_cfg_section(cfg).get("book_snapshot_interval_sec", _DEFAULT_BOOK_INTERVAL_SEC))
        last = _last_book_write.get(ticker)
        if last is not None and (now - last) < interval:
            return False
        _last_book_write[ticker] = now

        _book_buffer.append((
            ticker, series, now, _exchange_ts(ticker_msg),
            _float(ticker_msg.get("price_dollars")), _float(ticker_msg.get("yes_bid_dollars")),
            _float(ticker_msg.get("yes_ask_dollars")), _float(ticker_msg.get("yes_bid_size_fp")),
            _float(ticker_msg.get("yes_ask_size_fp")), _float(ticker_msg.get("volume_fp")),
            _float(ticker_msg.get("open_interest_fp")), _float(ticker_msg.get("dollar_volume")),
            _float(ticker_msg.get("dollar_open_interest")), _float(ticker_msg.get("last_trade_size_fp")),
            json.dumps(ticker_msg, default=str),
        ))
        if len(_book_buffer) >= _FLUSH_BATCH:
            flush()
        return True
    except Exception as exc:
        fault_log.record("series_watcher", "record_book", exc)
        return False


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
    """Write buffered captures as two executemany batches. Called from the
    trading loop each tick and automatically once a buffer reaches
    _FLUSH_BATCH - see the buffer declarations above for why this is
    batched rather than written per message.

    Never raises, for the same reason record_trade doesn't: a failed
    observability write must not take down the trading loop that called
    it. On failure the rows are dropped rather than retried forever, and
    counted in _dropped_rows so the loss is visible in capture_stats
    instead of silent."""
    global _trade_buffer, _book_buffer, _dropped_rows
    trades, books = _trade_buffer, _book_buffer
    _trade_buffer, _book_buffer = [], []
    if not trades and not books:
        return {"trades": 0, "books": 0}
    try:
        with _connect() as conn:
            if trades:
                conn.executemany(
                    "INSERT OR IGNORE INTO raw_trades "
                    "(trade_id, ticker, series, observed_at, exchange_ts, taker_outcome_side, "
                    " taker_book_side, taker_side_legacy, resolved_side, count_fp, yes_price_dollars, "
                    " no_price_dollars, notional_usd, is_block_trade, excluded, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    trades,
                )
            if books:
                conn.executemany(
                    "INSERT INTO book_snapshots "
                    "(ticker, series, observed_at, exchange_ts, price_dollars, yes_bid_dollars, "
                    " yes_ask_dollars, yes_bid_size_fp, yes_ask_size_fp, volume_fp, open_interest_fp, "
                    " dollar_volume, dollar_open_interest, last_trade_size_fp, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    books,
                )
    except Exception as exc:
        _dropped_rows += len(trades) + len(books)
        fault_log.record("series_watcher", "flush", exc,
                         context=f"{len(trades)} trade + {len(books)} book row(s) dropped")
        return {"trades": 0, "books": 0, "dropped": len(trades) + len(books)}
    return {"trades": len(trades), "books": len(books)}


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


def capture_stats(series: str | None = None) -> dict:
    """What the capture half has actually collected — so a caller can tell
    "no whale prints happened" apart from "the watcher wasn't running,"
    which are the two explanations this whole module exists to
    distinguish."""
    series = series or DEFAULT_SERIES
    try:
        with _connect() as conn:
            trades, first_t, last_t = conn.execute(
                "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM raw_trades WHERE series = ?",
                (series,),
            ).fetchone()
            books, first_b, last_b = conn.execute(
                "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM book_snapshots WHERE series = ?",
                (series,),
            ).fetchone()
    except sqlite3.Error as exc:
        return {"series": series, "error": str(exc)}
    return {
        "series": series,
        "raw_trades": trades, "raw_trades_first_at": first_t, "raw_trades_last_at": last_t,
        "book_snapshots": books, "book_first_at": first_b, "book_last_at": last_b,
        # Rows captured but not yet written (see flush()). Reported so a
        # count read right after a burst isn't mistaken for data loss.
        "buffered_trades": len(_trade_buffer), "buffered_books": len(_book_buffer),
        "dropped_rows": _dropped_rows,
    }


# ------------------------------------------------------------------ reading

def _signals_for_series(series: str, since_ts: float, before_ts: float) -> list[dict]:
    """Non-excluded signals only — an experiment window is real data about
    the exchange but is not evidence about the strategy (see
    signal_log.mark_excluded_range)."""
    try:
        with sqlite3.connect(signal_log.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ticker, side, size, confidence, seen_at, price, resolved, correct, "
                "raw_notional_usd FROM signals "
                "WHERE series = ? AND seen_at > ? AND seen_at <= ? AND excluded = 0",
                (series, since_ts, before_ts),
            ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def _trades_for_series(series: str, since_ts: float, before_ts: float) -> list[dict]:
    """Every paper trade on this series in the window, chronological — both
    entries and closes, since trade_analytics.build_trade_history pairs them
    itself and needs the entry that precedes each close.

    Widened to `since_ts - 7 days` on the read side so a position opened
    before the window but closed inside it still finds its own entry;
    filtering happens on the close timestamp afterwards."""
    try:
        with sqlite3.connect(pb_module.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, ticker, side, size, price, reason, timestamp, config_fingerprint, "
                "fee, signal_seen_at FROM trades "
                "WHERE ticker LIKE ? AND timestamp > ? AND timestamp <= ? AND excluded = 0 ORDER BY timestamp",
                (f"{series}-%", since_ts - 7 * 86400, before_ts),
            ).fetchall()
    except sqlite3.Error:
        return []
    # LIKE 'SERIES-%' is a prefix filter, not the series definition —
    # re-check against signal_log.series_of so a series whose name is a
    # prefix of another can't leak in.
    return [dict(r) for r in rows if signal_log.series_of(r["ticker"]) == series]


def _unit_cost(side: str, yes_price: float | None) -> float | None:
    """What the taker actually paid per contract. Side-aware, same
    inversion PaperBroker.cost_basis uses — re-deriving this as size*price
    without the (1 - price) no-side flip is the exact bug class CLAUDE.md's
    "no-side dollar math" section documents."""
    if yes_price is None:
        return None
    return yes_price if side == "yes" else 1.0 - yes_price


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 1) if denominator else None


# ------------------------------------------------------------------ funnel

def funnel(series: str | None = None, hours: float = 24.0, cfg: dict | None = None,
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
        with _connect() as conn:
            observed, whale_sized, capture_start = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN count_fp >= ? THEN 1 ELSE 0 END), MIN(observed_at) "
                "FROM raw_trades WHERE series = ? AND observed_at > ? AND excluded = 0",
                (min_contracts, series, since_ts),
            ).fetchone()
            unreadable_side = conn.execute(
                "SELECT COUNT(*) FROM raw_trades WHERE series = ? AND observed_at > ? "
                "AND resolved_side IS NULL",
                (series, since_ts),
            ).fetchone()[0]
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

    signals = _signals_for_series(series, since_ts, now)
    resolved = [s for s in signals if s["resolved"]]
    correct = [s for s in resolved if s["correct"]]

    raw = _trades_for_series(series, since_ts, now)
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

def reconcile(series: str | None = None, hours: float = 24.0, cfg: dict | None = None,
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

        edge_pts = signal_accuracy - mean_entry_unit_cost
            A contract at unit cost c pays $1 or $0, so EV per contract is
            exactly (p - c) and BREAKEVEN ACCURACY IS THE ENTRY PRICE. This
            is the component that makes "70% right" and "profitable" two
            unrelated statements.

    Signals are joined to trades on (ticker, signal_seen_at) — an exact
    key, not a heuristic: PaperBroker.Trade.signal_seen_at is written from
    the same WhaleSignal.timestamp that signal_log stores as seen_at (see
    main.py::_handle_signal and strategy_engine's open_position calls), and
    both round-trip through SQLite REAL unchanged."""
    series = series or DEFAULT_SERIES
    now = now if now is not None else time.time()
    since_ts = now - hours * 3600

    signals = _signals_for_series(series, since_ts, now)
    raw = _trades_for_series(series, since_ts, now)
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
    unit_costs = [
        uc for uc in (_unit_cost(t["side"], t["price"]) for t in entries) if uc is not None
    ]
    mean_unit_cost = round(sum(unit_costs) / len(unit_costs), 4) if unit_costs else None
    breakeven_accuracy_pct = round(mean_unit_cost * 100, 1) if mean_unit_cost is not None else None
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
        signal_cost = _unit_cost(t["side"], match["price"])
        fill_cost = _unit_cost(t["side"], t["price"])
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


def book_context_at_entry(series: str | None = None, hours: float = 24.0,
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

    raw = _trades_for_series(series, since_ts, now)
    entries = [t for t in raw if not t["reason"].startswith("closed:") and t["timestamp"] > since_ts]
    if not entries:
        return {"series": series, "status": "unknown", "reason": "no entries in this window"}

    matched, spreads, depth_ratios = 0, [], []
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            for t in entries:
                snap = conn.execute(
                    "SELECT yes_bid_dollars, yes_ask_dollars, yes_bid_size_fp, yes_ask_size_fp, "
                    "open_interest_fp FROM book_snapshots "
                    "WHERE ticker = ? AND observed_at BETWEEN ? AND ? "
                    "ORDER BY ABS(observed_at - ?) LIMIT 1",
                    (t["ticker"], t["timestamp"] - _BOOK_MATCH_WINDOW_SEC,
                     t["timestamp"] + _BOOK_MATCH_WINDOW_SEC, t["timestamp"]),
                ).fetchone()
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

def check_series_funnel(cfg: dict, series: str | None = None, hours: float = 24.0,
                        now: float | None = None) -> Check:
    """diagnostics-compatible wrapper — this is the plug. Returns the same
    Check shape every other check in services/diagnostics/diagnostics.py returns, so
    run_offline can include it and /api/diagnostics renders it with no
    special-casing."""
    series = series or (watched_series(cfg) or [DEFAULT_SERIES])[0]
    r = reconcile(series, hours=hours, cfg=cfg, now=now)

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
            f"averaged {r['mean_entry_unit_cost']:.3f}/contract, which needs "
            f"{r['breakeven_accuracy_pct']}% accuracy just to break even, so this series is "
            f"{abs(edge):.1f}pts underwater at the price level regardless of exits"
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
        evidence=funnel(series, hours=hours, cfg=cfg, now=now)["stages"],
    )
