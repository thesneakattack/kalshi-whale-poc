"""
Real Kalshi market data, logged over time - independent of whale signals
(simulated, see services/whale_simulator.py) and independent of whether the
whale-follow strategy ever traded on a given market. Direct request
(2026-08-08): "market data is real, the whale data is fake... there's no
reason why I shouldn't start storing and analyzing market data now."

Two jobs:
1. A rolling per-market snapshot log (price/spread/volume/time-to-close),
   populated once per trading-loop tick from the same real market fetch
   main.py already does for the whale-follow strategy - zero extra API
   cost. This module's own momentum() reads it (used until 2026-08-22 by
   the now-removed Market-Native strategy to decide entries), and it's the
   raw substrate for #2 below.
2. Settlement outcomes (real, from Kalshi's market.result field, same
   source main.py's check_exits/evaluate already use) plus
   compute_hypothetical_trades(), a retrospective "what would a simple
   entry have looked like" analysis - explicitly hypothetical/backtested,
   never a claim about a real position (same honesty framing as
   trade_analytics.py's left_on_table).

SQLite file: data/market_history.db - gitignored, same one-file-per-concern
pattern as every other services/*.py persistence module.
"""
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from services import fault_log
from services.signal_log import series_of

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market_history.db"

# momentum() requires the earliest snapshot in its window to actually be
# close to the start of the requested lookback, not just "the oldest one
# available" - otherwise a ticker with only 90 seconds of history would
# report "30-minute momentum" computed from two snapshots 90 seconds apart,
# which is noise dressed up as a real reading.
_MIN_WINDOW_COVERAGE = 0.5

# How often one ticker's websocket-pushed price is worth its own snapshot
# row (2026-08-17, see record_snapshot_from_ticker) - the channel fires on
# every field change, far more often than momentum()/volatility() need to
# see. Same cadence question series_watcher.record_book already asks for
# its own (unrelated) book-snapshot table; reused here rather than invented
# fresh - see series_watcher._DEFAULT_BOOK_INTERVAL_SEC.
_TICKER_SNAPSHOT_MIN_INTERVAL_SEC = 5.0

# ticker -> last snapshot timestamp written via the ticker-stream path,
# kept separate from the REST tick's own cadence (that path has no
# throttle - it already runs at most once per poll_interval_sec).
_last_ticker_snapshot: dict[str, float] = {}


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
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            yes_price REAL NOT NULL,
            spread REAL,
            volume_24h REAL,
            time_to_close_sec REAL,
            timestamp REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_ts ON snapshots (ticker, timestamp)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outcomes (
            ticker TEXT PRIMARY KEY,
            series TEXT NOT NULL,
            result TEXT NOT NULL,
            resolved_at REAL NOT NULL
        )
        """
    )
    return conn


def seconds_to_close(close_time: str | None, now: float) -> float | None:
    """Same close_time parsing idiom as whale_simulator._score_confidence -
    no close_time (or an unparseable one) returns None rather than
    guessing. Shared here (not duplicated) since main.py's snapshot
    logging needs it too."""
    if not close_time:
        return None
    try:
        close_ts = datetime.fromisoformat(close_time.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None
    return close_ts - now


def record_snapshots(rows: list[dict], timestamp: float | None = None):
    """rows: [{"ticker", "yes_price", "spread", "volume_24h", "time_to_close_sec"}, ...] -
    one call per trading-loop tick with every currently-fetched real
    market. series is derived here (signal_log.series_of), not passed in,
    so this never drifts from the one series definition the rest of the
    app already shares."""
    ts = timestamp if timestamp is not None else time.time()
    with _connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT INTO snapshots (ticker, series, yes_price, spread, volume_24h, time_to_close_sec, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (r["ticker"], series_of(r["ticker"]), r["yes_price"], r.get("spread"),
                 r.get("volume_24h"), r.get("time_to_close_sec"), ts)
                for r in rows
            ],
        )


def record_snapshot_from_ticker(
    ticker: str, yes_price: float, spread: float | None = None,
    volume_24h: float | None = None, close_time: str | None = None,
    now: float | None = None,
) -> bool:
    """Same snapshots table as record_snapshots, fed from the ticker
    websocket stream instead of waiting for the next REST tick - raises
    this table's real time resolution for actively-trading markets using
    data already flowing into main.py's state["latest_prices"]
    (_process_stream_ticker), no new subscription or API cost. Part of the
    2026-08-17 REST-vs-websocket architecture finding (see docs/next-
    session-pickup-2026-08-17.md): momentum()/volatility() were measured at
    exactly 0.0 for 78% of markets because 6-second REST-only sampling was
    too sparse.

    Throttled per ticker (_TICKER_SNAPSHOT_MIN_INTERVAL_SEC), since the
    channel fires on every field change, not just price - momentum()/
    volatility() need real elapsed time between samples to measure a trend,
    not a row per tick of book noise. Returns False (no-op, not an error)
    when throttled, so callers don't need their own gating logic.

    Never raises - this runs on the websocket message path, where main.py's
    own kalshi_trade_ws consumer already swallows exceptions per-message
    (see its docstring), but silently is exactly the failure mode
    services/fault_log.py exists to prevent, so any real fault is recorded
    there rather than just disappearing."""
    try:
        now = now if now is not None else time.time()
        last = _last_ticker_snapshot.get(ticker)
        if last is not None and (now - last) < _TICKER_SNAPSHOT_MIN_INTERVAL_SEC:
            return False
        _last_ticker_snapshot[ticker] = now
        record_snapshots(
            [{
                "ticker": ticker,
                "yes_price": yes_price,
                "spread": spread,
                "volume_24h": volume_24h,
                "time_to_close_sec": seconds_to_close(close_time, now),
            }],
            timestamp=now,
        )
        return True
    except Exception as exc:
        fault_log.record("market_history", "record_snapshot_from_ticker", exc)
        return False


def record_outcome(ticker: str, result: str, resolved_at: float | None = None):
    """INSERT OR IGNORE - a ticker resolves once and stays resolved, first
    write wins. Called from the same market_results scan main.py's
    check_exits/evaluate already do, so this costs nothing extra either."""
    with _connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO outcomes (ticker, series, result, resolved_at) VALUES (?, ?, ?, ?)",
            (ticker, series_of(ticker), result, resolved_at if resolved_at is not None else time.time()),
        )


def momentum(ticker: str, lookback_sec: float, as_of: float | None = None) -> dict | None:
    """Real price movement over the trailing lookback_sec window, from
    logged snapshots - None if there isn't yet enough history to trust a
    reading (fewer than 2 snapshots, or the oldest one in range doesn't
    cover at least _MIN_WINDOW_COVERAGE of the requested window). Missing
    data is treated as "no signal," never as zero momentum."""
    as_of = as_of if as_of is not None else time.time()
    window_start = as_of - lookback_sec
    with _connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT yes_price, timestamp FROM snapshots WHERE ticker = ? AND timestamp <= ? "
            "ORDER BY timestamp ASC",
            (ticker, as_of),
        ).fetchall()
    in_window = [r for r in rows if r[1] >= window_start]
    if len(in_window) < 2:
        return None
    earliest, latest = in_window[0], in_window[-1]
    span_sec = latest[1] - earliest[1]
    if span_sec < lookback_sec * _MIN_WINDOW_COVERAGE:
        return None
    return {
        "delta": latest[0] - earliest[0],
        "from_price": earliest[0],
        "to_price": latest[0],
        "span_sec": span_sec,
    }


def volatility(ticker: str, lookback_sec: float, as_of: float | None = None) -> float | None:
    """Realized-volatility proxy: population stdev of consecutive-snapshot
    price deltas within the trailing lookback_sec window - "how much does
    this ticker's price normally wiggle," deliberately orthogonal to
    momentum() (net direction/magnitude of the move, not its noisiness).
    2026-08-14 direct request (auto-exit deep-dive): the pnl factor in
    services/strategy_engine.py's _exit_confidence compared the same raw
    percentage move the same way for every ticker regardless of how much
    that ticker normally moves - a real move on a slow-moving political
    market and routine noise on a fast in-play sports market read as
    equally "decisive," a real contributor to the whipsaw pattern found in
    that session's trade-history review (auto-exit firing on what was
    normal volatility for that specific ticker, not a genuine signal).
    None when there isn't enough history to trust a reading (fewer than 3
    snapshots in window - need at least 2 deltas to measure spread at
    all), same "missing data is 'no signal,' never a false zero" idiom as
    momentum()."""
    as_of = as_of if as_of is not None else time.time()
    window_start = as_of - lookback_sec
    with _connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT yes_price FROM snapshots WHERE ticker = ? AND timestamp <= ? AND timestamp >= ? "
            "ORDER BY timestamp ASC",
            (ticker, as_of, window_start),
        ).fetchall()
    if len(rows) < 3:
        return None
    prices = [r[0] for r in rows]
    deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    mean_delta = sum(deltas) / len(deltas)
    variance = sum((d - mean_delta) ** 2 for d in deltas) / len(deltas)
    return variance ** 0.5


def recent_price(ticker: str, max_age_sec: float, as_of: float | None = None) -> float | None:
    """Most recent snapshot's yes_price, or None if there isn't one within
    max_age_sec.

    Added 2026-08-17 as the corroboration source for
    strategy_engine.check_exits' stop-loss/take-profit decision - direct,
    confirmed-live incident: a real WTA position (Cirstea/Kalinskaya) was
    liquidated via stop-loss at exit_price 0.0 one tick after this exact
    table's own independently REST-polled snapshots had sat pinned at 0.99
    for 13+ minutes straight. `latest_prices.get(ticker, pos.entry_price)`
    (state["latest_prices"], written by the websocket ticker channel) was
    trusted with zero corroboration against this already-known-good REST
    price sitting in the same process. Three tennis positions showed the
    identical shape the same night; zero crypto positions did, consistent
    with a thin/illiquid in-play sports order book producing one garbage
    quote rather than a universal parsing bug.

    "No recent snapshot" (illiquid ticker, cold start, or a genuinely new
    market with no REST history yet) returns None - the caller's contract
    is to fail OPEN in that case (trust current_price as before, same as
    today), not to block every exit decision just because corroboration
    isn't available yet."""
    as_of = as_of if as_of is not None else time.time()
    with _connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT yes_price, timestamp FROM snapshots WHERE ticker = ? AND timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (ticker, as_of),
        ).fetchone()
    if row is None or (as_of - row[1]) > max_age_sec:
        return None
    return row[0]


def snapshot_count(ticker: str | None = None) -> int:
    with _connect(DB_PATH) as conn:
        if ticker:
            return conn.execute("SELECT COUNT(*) FROM snapshots WHERE ticker = ?", (ticker,)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]


def tracked_ticker_count() -> int:
    with _connect(DB_PATH) as conn:
        return conn.execute("SELECT COUNT(DISTINCT ticker) FROM snapshots").fetchone()[0]


def outcome_count() -> int:
    with _connect(DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]


def clear_all():
    """Wipes snapshots and outcomes - self-healing on its own via ordinary
    per-tick recording, this exists for the Config tab's Danger Zone reset,
    matching market_catalog.clear_all()'s identical shape (data-robustness
    audit finding, 2026-08-10: this file had no wired reset path at all,
    despite being one of the two largest data/*.db files on disk)."""
    with _connect(DB_PATH) as conn:
        conn.execute("DELETE FROM snapshots")
        conn.execute("DELETE FROM outcomes")


def compute_hypothetical_trades(lookback_windows_sec: tuple = (3600, 21600, 86400)) -> list[dict]:
    """Retrospective, explicitly hypothetical: for every settled market,
    and for each configured lookback window, finds the snapshot closest to
    (settlement - window) and computes what a "buy the side the price
    already favored at that point, hold to settlement" entry would have
    returned per contract. This is a real, whale-independent labeled
    dataset (market conditions at a point in time -> realized outcome) -
    never a claim that a real position existed; nothing here touches
    PaperBroker or spends any bankroll. Same per-contract cost/payout
    convention as PaperBroker.open_position's unit_cost (a "no" entry's
    real cost is 1-price, not price)."""
    with _connect(DB_PATH) as conn:
        outcomes = conn.execute("SELECT ticker, series, result, resolved_at FROM outcomes").fetchall()

    results = []
    with _connect(DB_PATH) as conn:
        for ticker, series, result, resolved_at in outcomes:
            snaps = conn.execute(
                "SELECT yes_price, timestamp FROM snapshots WHERE ticker = ? AND timestamp <= ? ORDER BY timestamp ASC",
                (ticker, resolved_at),
            ).fetchall()
            if not snaps:
                continue
            for window in lookback_windows_sec:
                target_time = resolved_at - window
                # Closest snapshot to the target time, preferring one at or
                # before it (a real entry can't be placed using a price from
                # after the hypothetical entry point).
                candidates = [s for s in snaps if s[1] <= target_time]
                if not candidates:
                    continue
                entry_price, entry_ts = candidates[-1]
                side = "yes" if entry_price > 0.5 else "no"
                won = result == side
                unit_cost = entry_price if side == "yes" else (1 - entry_price)
                payout = 1.0 if won else 0.0
                results.append({
                    "ticker": ticker,
                    "series": series,
                    "lookback_sec": window,
                    "entry_price": entry_price,
                    "entry_timestamp": entry_ts,
                    "side": side,
                    "result": result,
                    "won": won,
                    "pnl_per_contract": round(payout - unit_cost, 4),
                    "resolved_at": resolved_at,
                })
    return results
