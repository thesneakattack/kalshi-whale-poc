import asyncio
import sqlite3
import time
from pathlib import Path

from services import http_client, market_history, task_supervisor
from services.app_state import state
from services.kalshi.public import KalshiPublicGateway
from services.market_catalog import market_catalog

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


def comparison_report(
    tickers: list[str], lookback_sec: float = 3600, as_of: float | None = None,
) -> list[dict]:
    """Per-ticker side-by-side: this module's own volatility() vs.
    market_history.volatility() at the same (lookback_sec, as_of) - a
    diagnostic, never a blend/verdict (additive-only scope: nothing here
    picks a winner or feeds a decision). delta is only computed when both
    sides have enough history - never a fabricated comparison against a
    missing reading."""
    as_of = as_of if as_of is not None else time.time()
    rows = []
    for ticker in tickers:
        cv_val = volatility(ticker, lookback_sec, as_of=as_of)
        snap_val = market_history.volatility(ticker, lookback_sec, as_of=as_of)
        delta = (cv_val - snap_val) if (cv_val is not None and snap_val is not None) else None
        rows.append({
            "ticker": ticker,
            "candlestick_volatility": cv_val,
            "candlestick_bar_count": bar_count(ticker),
            "snapshot_volatility": snap_val,
            "delta": delta,
        })
    return rows


def clear_all() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM candles")


# ---- background scan (Task 4) ---------------------------------------------
#
# Periodically fetches candlestick history for the current watchlist
# (state["markets"]) and stores it via record_candles above. Mirrors
# services/market_watch/mve_scan.py's _maybe_scan_mve_batch/
# _scan_mve_batch_background pair almost exactly - same due()/overlap-guard
# scheduling shape, same task_supervisor wiring, same
# construct-client/scan/close-in-finally lifecycle, same
# asyncio.gather(..., return_exceptions=True) per-item failure isolation.

_CANDLESTICK_SCAN_MIN_INTERVAL_SEC = 1800  # How often a new background batch
# may be KICKED OFF - the shared token-bucket rate limiter (services/
# http_client.py) is what actually keeps the real aggregate call rate safe,
# same relationship catalog_scan._CATALOG_SCAN_MIN_INTERVAL_SEC/mve_scan.
# _MVE_SCAN_MIN_INTERVAL_SEC's own comments document. Overridable via
# config/settings.yaml's candlestick_volatility.scan_interval_sec.

_CANDLESTICK_PACE_LIMIT = 4  # Same bounded-concurrency reasoning as
# mve_scan._MVE_SCAN_PACE_LIMIT/catalog_scan.PACE_LIMIT - caps how many of
# this batch's own get_candlesticks() calls run concurrently against the
# shared token bucket, independent of watchlist size.


async def _fetch_one_ticker_candles(
    client: KalshiPublicGateway, pace_sem: asyncio.Semaphore,
    ticker: str, series_ticker: str, start_ts: int, end_ts: int, period_interval_min: int,
) -> tuple[str, str, dict]:
    async with pace_sem:
        resp = await client.get_candlesticks(series_ticker, ticker, start_ts, end_ts, period_interval_min)
    return ticker, series_ticker, resp


async def _scan_candlestick_volatility_batch(client: KalshiPublicGateway, cfg: dict) -> None:
    """One cycle: current watchlist (state["markets"]) -> resolve each
    ticker's series_ticker via market_catalog.series_ticker_for() (skip,
    don't guess, if not yet catalogued - same posture mve_scan/catalog_scan
    already take toward an unresolved series) -> one paced
    get_candlesticks() per resolved ticker -> record_candles() per
    response. A per-ticker fetch failure is isolated
    (asyncio.gather(..., return_exceptions=True), same shape as
    mve_scan._scan_mve_batch) and logged, never aborts the rest of the
    cycle."""
    cv_cfg = cfg.get("candlestick_volatility") or {}
    period_interval_min = cv_cfg.get("period_interval_min", 60)
    lookback_window_sec = cv_cfg.get("lookback_window_sec", 86400)
    now = int(time.time())
    full_window_start_ts = now - lookback_window_sec

    tickers = [m["ticker"] for m in state["markets"] if m.get("ticker")]
    pace_sem = asyncio.Semaphore(_CANDLESTICK_PACE_LIMIT)
    fetch_tickers = []
    fetches = []
    for ticker in tickers:
        series_ticker = market_catalog.series_ticker_for(ticker)
        if not series_ticker:
            continue
        # Watermark, not a blind full-window refetch: a ticker this scan
        # has already fetched only needs bars since its own last_fetched_at
        # (same "min_ts filters items after this Unix timestamp" idiom
        # services/kalshi/public.py's get_trades already established) -
        # re-requesting the full lookback_window_sec every 30-minute cycle
        # would re-fetch/re-upsert ~24 bars for ~1 new one every time
        # (found in adversarial review, 2026-08-30). First-ever fetch for a
        # ticker still uses the full window.
        prior = last_fetched_at(ticker)
        start_ts = max(full_window_start_ts, int(prior)) if prior is not None else full_window_start_ts
        fetch_tickers.append(ticker)
        fetches.append(_fetch_one_ticker_candles(client, pace_sem, ticker, series_ticker, start_ts, now, period_interval_min))

    results = await asyncio.gather(*fetches, return_exceptions=True)
    for ticker, result in zip(fetch_tickers, results):
        if isinstance(result, Exception):
            # Same "don't mark scanned, don't lose the ticker silently"
            # posture as mve_scan._scan_mve_batch's own failure handling -
            # no logging framework exists yet in this app, so stdout via
            # `ddev logs -s fastapi` is the visibility path.
            print(f"[candlestick_volatility] scan failed for {ticker!r}, will retry next cycle: {result!r}")
            continue
        fetched_ticker, series_ticker, resp = result
        bars = resp.get("candlesticks") or []
        record_candles(fetched_ticker, series_ticker, period_interval_min, bars, fetched_at=time.time())


def _maybe_scan_candlestick_volatility(cfg: dict) -> None:
    """Triggers _scan_candlestick_volatility_batch as an independent
    background task on its own steady interval - mirrors
    mve_scan._maybe_scan_mve_batch exactly (same overlap guard shape, same
    task_supervisor wiring), gated first on candlestick_volatility.enabled."""
    cv_cfg = cfg.get("candlestick_volatility") or {}
    if not cv_cfg.get("enabled", True):
        return
    scan_state = state["candlestick_volatility_scan"]
    interval = cv_cfg.get("scan_interval_sec", _CANDLESTICK_SCAN_MIN_INTERVAL_SEC)
    now_ts = time.time()
    due = now_ts - scan_state["last_started_at"] > interval
    if due and not scan_state["scanning"]:
        scan_state["scanning"] = True
        scan_state["last_started_at"] = now_ts
        scan_state["task"] = task_supervisor.supervise(
            lambda: _scan_candlestick_volatility_batch_background(cfg),
            component="candlestick_volatility_scan", operation="scan_batch",
        )


@http_client.classify("background_candlestick_volatility")
async def _scan_candlestick_volatility_batch_background(cfg: dict) -> None:
    """Owns its own KalshiPublicGateway - same construct/use/close shape as
    mve_scan._scan_mve_batch_background (the calling tick's own client
    closes at the end of that same tick, not this one)."""
    scan_state = state["candlestick_volatility_scan"]
    client = KalshiPublicGateway(cfg["kalshi"]["base_url"], cfg["kalshi"]["request_timeout_sec"])
    try:
        await _scan_candlestick_volatility_batch(client, cfg)
    finally:
        scan_state["scanning"] = False
        await client.close()
