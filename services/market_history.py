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
   cost. This is what services/market_strategy.py's momentum() reads to
   decide entries, and the raw substrate for #2 below.
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

from services.signal_log import series_of

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "market_history.db"

# momentum() requires the earliest snapshot in its window to actually be
# close to the start of the requested lookback, not just "the oldest one
# available" - otherwise a ticker with only 90 seconds of history would
# report "30-minute momentum" computed from two snapshots 90 seconds apart,
# which is noise dressed up as a real reading.
_MIN_WINDOW_COVERAGE = 0.5


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
    guessing. Shared here (not duplicated) since both main.py's snapshot
    logging and services/market_strategy.py's entry gate need it."""
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
