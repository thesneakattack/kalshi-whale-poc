import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candlestick_volatility.db"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = DB_PATH
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series_ticker TEXT NOT NULL,
            period_interval_min INTEGER NOT NULL,
            end_period_ts REAL NOT NULL,
            yes_bid_open REAL NOT NULL,
            yes_bid_high REAL NOT NULL,
            yes_bid_low REAL NOT NULL,
            yes_bid_close REAL NOT NULL,
            volume_fp REAL,
            open_interest_fp REAL,
            fetched_at REAL NOT NULL,
            UNIQUE(ticker, period_interval_min, end_period_ts)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_candles_ticker_ts ON candles (ticker, end_period_ts)"
    )
    return conn


def record_candles(
    ticker: str, series_ticker: str, period_interval_min: int, bars: list[dict],
    fetched_at: float | None = None,
) -> int:
    """bars: the raw get_candlesticks()["candlesticks"] list. Stores the
    full yes_bid OHLC (open/high/low/close_dollars - all four schema-
    required per docs/kalshi/get-market-candlesticks.md's BidAskDistribution,
    never null) rather than just close, and rather than price.close_dollars
    (nullable when a period had no real trade) - matches market_history.py's
    own yes_bid-sourced yes_price convention for the close column (so this
    module's volatility() stays a fair apples-to-apples comparison against
    market_history.volatility()), while keeping open/high/low too per this
    repo's Fidelity hard rule (store what the exchange sent, don't discard
    fields already in the same response) and to leave room for a future
    high-low-range volatility estimator without a full re-fetch. A bar
    missing end_period_ts or yes_bid.close_dollars is skipped, never
    guessed (open/high/low missing while close is present would be a
    genuinely malformed response per the doc's own required-fields list -
    not expected in practice, but if it happens, still skip the bar rather
    than write a partial/inconsistent OHLC row). UPSERT via the
    UNIQUE(ticker, period_interval_min, end_period_ts) constraint -
    re-fetching an overlapping window every cycle is idempotent. Returns
    rows actually stored."""
    fetched_at = fetched_at if fetched_at is not None else time.time()
    stored = 0
    with _connect() as conn:
        for bar in bars:
            end_ts = bar.get("end_period_ts")
            yes_bid = bar.get("yes_bid") or {}
            yes_bid_open = yes_bid.get("open_dollars")
            yes_bid_high = yes_bid.get("high_dollars")
            yes_bid_low = yes_bid.get("low_dollars")
            yes_bid_close = yes_bid.get("close_dollars")
            if end_ts is None or None in (yes_bid_open, yes_bid_high, yes_bid_low, yes_bid_close):
                continue
            conn.execute(
                """INSERT INTO candles
                   (ticker, series_ticker, period_interval_min, end_period_ts,
                    yes_bid_open, yes_bid_high, yes_bid_low, yes_bid_close,
                    volume_fp, open_interest_fp, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticker, period_interval_min, end_period_ts)
                   DO UPDATE SET yes_bid_open = excluded.yes_bid_open,
                                 yes_bid_high = excluded.yes_bid_high,
                                 yes_bid_low = excluded.yes_bid_low,
                                 yes_bid_close = excluded.yes_bid_close,
                                 volume_fp = excluded.volume_fp,
                                 open_interest_fp = excluded.open_interest_fp,
                                 fetched_at = excluded.fetched_at""",
                (ticker, series_ticker, period_interval_min, end_ts,
                 yes_bid_open, yes_bid_high, yes_bid_low, yes_bid_close,
                 bar.get("volume_fp"), bar.get("open_interest_fp"), fetched_at),
            )
            stored += 1
    return stored


def volatility(
    ticker: str, lookback_sec: float, as_of: float | None = None,
    period_interval_min: int = 60,
) -> float | None:
    """Candlestick-derived counterpart to market_history.volatility() -
    population stdev of consecutive-bar yes_bid close-to-close deltas
    within the trailing lookback_sec window, for one fixed
    period_interval_min (mixing bar granularities in one stdev would be
    invalid). None when fewer than 3 bars fall in the window (need at
    least 2 deltas to measure spread) - missing/insufficient candlestick
    history is "no signal," never a false zero, matching every other
    volatility/momentum reader in this app."""
    as_of = as_of if as_of is not None else time.time()
    window_start = as_of - lookback_sec
    with _connect() as conn:
        rows = conn.execute(
            "SELECT yes_bid_close FROM candles WHERE ticker = ? AND period_interval_min = ? "
            "AND end_period_ts <= ? AND end_period_ts >= ? ORDER BY end_period_ts ASC",
            (ticker, period_interval_min, as_of, window_start),
        ).fetchall()
    if len(rows) < 3:
        return None
    closes = [r[0] for r in rows]
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    mean_delta = sum(deltas) / len(deltas)
    variance = sum((d - mean_delta) ** 2 for d in deltas) / len(deltas)
    return variance ** 0.5


def last_fetched_at(ticker: str) -> float | None:
    """Most recent fetched_at across this ticker's bars, or None - used by
    the background scan (Task 4) as a watermark so a ticker already in the
    table only needs bars since its own last fetch, not a blind refetch of
    the full lookback_window_sec every cycle."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) FROM candles WHERE ticker = ?", (ticker,)
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def bar_count(ticker: str | None = None) -> int:
    with _connect() as conn:
        if ticker is None:
            return conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM candles WHERE ticker = ?", (ticker,)).fetchone()[0]


def clear_all() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM candles")
