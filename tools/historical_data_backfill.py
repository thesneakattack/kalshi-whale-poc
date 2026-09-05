"""One-off, parameterized backfill for a SPECIFIC, already-quantified
capture gap - issue #265, scoped to the losses issues #209 and #211 found
and measured.

WHAT THIS IS NOT: a standing/scheduled reconciliation job. It is a manual,
read-mostly script an operator runs once per known gap, exactly like
tools/kalshi_rate_limit_probe.py or tools/kalshi_census.py - never imported,
scheduled, or configured by the app (CLAUDE.md's tooling/application
separation). It never touches services/ or main.py.

WHAT IT ACTUALLY RECOVERS (verified 2026-08-30, not assumed):

- #211 (services/capture_writer.py's raw_trades drops under lock
  contention, fixed in commit 13680e5): recoverable. The live app's own
  `/api/health/faults` shows the drop-causing fault ("capture_writer",
  "flush", context "raw_trades") ran from 2026-08-27T20:22:15Z to
  2026-08-30T16:08:55Z. A real call to `GET /historical/cutoff` the same
  day returned `trades_created_ts: 2026-07-01T00:00:00Z` - #211's whole
  loss window is ~8 weeks INSIDE the live tier, nowhere near the
  historical boundary. So despite issue #265's title, the correct source
  for this specific recovery is Kalshi's LIVE trades endpoint
  (`GET /markets/trades`, docs/kalshi/get-trades.md), not
  `/historical/trades` (docs/kalshi/get-historical-trades.md) - the
  historical archive only serves trades OLDER than the cutoff
  (docs/kalshi/historical_data.md: "the target window for live data is 3
  months"). Both endpoints share one Trade schema and query-parameter set
  (ticker/min_ts/max_ts/limit/cursor/is_block_trade - confirmed by
  introspecting the installed kalshi_python_async 3.27.0 SDK's Trade /
  GetTradesResponse models, not assumed from the docs' prose alone), so
  this module picks whichever endpoint actually covers a given window
  (classify_window()) and can span both for a window that straddles the
  cutoff, per historical_data.md's own migration guide step 3 ("combine
  results if needed"). A genuinely old future gap would use the historical
  endpoint the same way - nothing here is #211-specific except the CLI
  arguments an operator supplies.

- #209 (services/kalshi/websocket.py's discarded_on_reconnect_by_class,
  fixed in commit a3b2c86): NOT recoverable by this tool, and this module
  deliberately implements no path for it. The fix's own "Not established"
  section says the 74 discarded items were never split between "sitting in
  a queue" (any message class - trade/ticker/fill/position/lifecycle/
  index/control) and "the ticker coalescing map" (always class "ticker") -
  nothing recorded which, so there is no trade_id, ticker, or even a
  per-reconnect timestamp to parameterize a targeted fetch with. Separately,
  to the extent any of the 74 were "ticker"-class (bid/ask/volume snapshot
  messages, not trade executions), Kalshi's trades endpoints - live or
  historical - only return completed trade executions; there is no Kalshi
  endpoint that replays "the ticker snapshot that existed at market M at
  time T" byte-for-byte. A data-type mismatch, not a coverage-window one -
  building a fetch for it here would be exactly the "fabricate a partial
  or fake recovery" issue #265 explicitly warns against.

RAW_TRADES SCHEMA / FIDELITY (per services/capture_writer.py's
_STORE_DDL["raw_trades"], read directly, not assumed):

- This module writes through services.capture_writer.submit()/flush_now()
  - the exact function, DDL, and INSERT OR IGNORE dedup the live app uses -
  rather than opening its own sqlite3 connection, so "same store, same
  schema, same fidelity" is enforced by construction, not by copying the
  DDL a second time somewhere it could drift.
- A column CANNOT be added to raw_trades to mark backfilled rows: verified
  empirically (see the worktree's investigation) that
  capture_writer._flush_store's INSERT is
  `INSERT OR IGNORE INTO raw_trades VALUES (?, ?, ..., ?)` with NO column
  list, so SQLite requires the value count to equal the table's total
  column count exactly. Adding a 17th column would raise
  "table raw_trades has 17 columns but 16 values were supplied" on every
  live flush thereafter - a self-inflicted completeness regression far
  worse than the gap this tool is recovering. Provenance instead lives in
  a SEPARATE, tool-owned ledger (data/historical_backfill_log.db,
  write_ledger_entries() below) keyed by the real Kalshi trade_id - ask
  "was trade_id X backfilled, when, from which endpoint, for which issue"
  there, the same way this repo already answers "why did this line
  change" from git log/blame rather than an inline comment.
- raw_json is byte-for-byte json.dumps() of the Kalshi response with no
  injected keys, matching services/kalshi/websocket.py's own explicit
  reasoning for why MESSAGE_ENQUEUED_AT is a contextvar rather than a key
  stamped into the message dict: "the gateway stamping a private key into
  the vendor payload ... would leak into archival raw_json."
- The three raw direction-alias columns (taker_outcome_side,
  taker_book_side, taker_side_legacy) are left NULL for backfilled rows.
  Not an oversight: tools/quality_audit/kalshi_boundary.py's CI guard
  (check 4) hard-fails any `.get("taker_outcome_side"/"taker_book_side"/
  "taker_side")` (or subscript) read outside services/kalshi/ and its one
  reviewed archival exemption, services/series_watcher.py - this module
  gets no second exemption, and gaming the guard's letter (e.g. via
  operator.itemgetter) while defeating its intent would be its own defect.
  Nothing is actually lost: raw_json still carries the real values
  byte-for-byte and is queryable via json_extract() if ever needed, and
  resolved_side - the one column anything downstream keys a decision off,
  per services/series_watcher.py's own comment ("canonical columns derive
  from the boundary's own direction/notional semantics") - is populated
  through the boundary's real resolve_taker_outcome_side(), not guessed.

CLI:

    python -m tools.historical_data_backfill \\
        --start 2026-08-27T20:22:15Z --end 2026-08-30T16:08:55Z \\
        --series KXBTC15M --series KXETH15M --series KXBTCD --series KXETHD \\
        --issue 211 --dry-run

--start/--end accept Unix seconds or ISO-8601. At least one of --ticker or
--series is required (repeatable) - this tool never guesses which
market(s) a gap belongs to; the operator supplies it from their own
fault-log/diagnostic investigation, the same way #211's real loss window
above was read from GET /api/health/faults, not assumed. --dry-run never
opens db_path or ledger_db_path at all (no read, no write) - it reports
only what was fetched from Kalshi and what rows WOULD be built, so it is
safe to run against the real Kalshi API while proving the mechanism works
before ever pointing --db-path at a real data/*.db file.

MEASURED COST, --series without --ticker: neither /markets/trades nor
/historical/trades takes a series/prefix filter (only an exact `ticker` -
confirmed against the installed SDK/docs, not assumed), so --series alone
fetches EVERY market's trades exchange-wide for the whole window and
filters to the requested series client-side. Real measurement (2026-08-30,
this window): --ticker scoped to one KXBTC15M market returned 27,932
trades in well under two minutes; the same window with --series across
all four watched series (no --ticker) was still paging past two minutes
when stopped. For a multi-day window, supply the specific --ticker(s) the
diagnostic investigation already named where possible - --series is
correctness-correct but can be slow, and that cost is not paid down here:
doing so would mean adding a get_markets series-enumeration pass this
issue never asked for and this session never verified against
docs/kalshi/get-markets.md, i.e. exactly the "verify, don't guess" the
project's own hard rule requires before shipping it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import signal_log
from services.kalshi.contracts.trade import (
    parse_fixed_point_dollars,
    resolve_taker_outcome_side,
    taker_notional_usd,
    trade_exchange_ts,
)

_RAW_TRADES_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "series_watcher.db"
_LEDGER_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "historical_backfill_log.db"

# Which bound SDK method serves each tier, both introspected on the
# installed kalshi_python_async 3.27.0 KalshiClient (2026-08-30): identical
# ticker/min_ts/max_ts/limit/cursor/is_block_trade signature, identical
# GetTradesResponse{trades, cursor} return shape - only the tier differs.
_TRADE_METHOD_BY_ENDPOINT: dict[str, str] = {"live": "get_trades", "historical": "get_trades_historical"}

_LEDGER_DDL = """
    CREATE TABLE IF NOT EXISTS backfilled_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        store TEXT NOT NULL,
        trade_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        source_endpoint TEXT NOT NULL,
        window_start_ts REAL NOT NULL,
        window_end_ts REAL NOT NULL,
        fetched_at REAL NOT NULL,
        issue_ref TEXT,
        already_present INTEGER NOT NULL DEFAULT 0,
        UNIQUE(store, trade_id)
    )
"""


def parse_ts(value: Any) -> float:
    """Unix seconds (int/float/numeric string) or ISO-8601 (any offset,
    'Z' included) -> epoch seconds. Raises ValueError on anything else -
    never silently defaults a malformed --start/--end to "now"."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ValueError(f"not a Unix timestamp or ISO-8601 datetime: {value!r}") from exc


def classify_window(start_ts: float, end_ts: float, cutoff_ts: float) -> list[tuple[str, float, float]]:
    """Split [start_ts, end_ts) into (endpoint, seg_start, seg_end) segments
    per docs/kalshi/historical_data.md: trades that occurred BEFORE
    trades_created_ts are historical-only; the cutoff instant itself and
    everything after is live. historical_data.md's own migration guide
    step 3 ("combine results if needed") is exactly this split for a
    window straddling the boundary."""
    if end_ts <= start_ts:
        raise ValueError(f"end_ts ({end_ts}) must be after start_ts ({start_ts})")
    if end_ts <= cutoff_ts:
        return [("historical", start_ts, end_ts)]
    if start_ts >= cutoff_ts:
        return [("live", start_ts, end_ts)]
    return [("historical", start_ts, cutoff_ts), ("live", cutoff_ts, end_ts)]


def build_raw_trade_row(trade: dict, observed_at: float) -> tuple:
    """The exact 16-column tuple services/capture_writer.py's raw_trades
    DDL expects, in column order - mirrors services/series_watcher.py's
    record_trade() row-construction (same boundary functions, same
    precedence), minus record_trade()'s own capture_enabled/watched_series/
    quarantine gating, which is a live-pipeline runtime concern this
    one-off tool has no equivalent of (the operator's --ticker/--series
    CLI arguments ARE the scope decision here).

    `trade` may be either REST shape (ticker/created_time, both live
    `/markets/trades` and historical `/historical/trades`) - ticker lookup
    and trade_exchange_ts()'s ts_ms->ts->created_time precedence both
    already handle this without a WS-only assumption."""
    ticker = trade.get("ticker") or trade.get("market_ticker")
    trade_id = trade.get("trade_id")
    series = signal_log.series_of(ticker) if ticker else None
    side = resolve_taker_outcome_side(trade)  # boundary-owned - see module docstring
    notional = taker_notional_usd(trade, side) if side else None
    return (
        str(trade_id) if trade_id is not None else None,
        ticker,
        series,
        observed_at,
        trade_exchange_ts(trade),
        None,  # taker_outcome_side - deliberately not read here, see module docstring
        None,  # taker_book_side - deliberately not read here, see module docstring
        None,  # taker_side_legacy - deliberately not read here, see module docstring
        side,
        parse_fixed_point_dollars(trade.get("count_fp")),
        parse_fixed_point_dollars(trade.get("yes_price_dollars")),
        parse_fixed_point_dollars(trade.get("no_price_dollars")),
        notional,
        1 if trade.get("is_block_trade") else 0,
        0,  # excluded - never repurposed as a provenance flag; see module docstring
        json.dumps(trade, default=str),
    )


def filter_by_series(trades: list[dict], series_allowlist: list[str] | None) -> list[dict]:
    """Keep only trades whose ticker's series (signal_log.series_of - the
    one series definition strategy_engine.py's own gate already uses) is
    in series_allowlist. Empty/None allowlist keeps everything - callers
    that already scoped by --ticker have nothing left to narrow."""
    if not series_allowlist:
        return trades
    allowed = set(series_allowlist)
    kept = []
    for t in trades:
        ticker = t.get("ticker") or t.get("market_ticker") or ""
        if signal_log.series_of(ticker) in allowed:
            kept.append(t)
    return kept


async def get_trades_created_cutoff_ts(client) -> float:
    """GET /historical/cutoff's trades_created_ts, as epoch seconds -
    docs/kalshi/get-historical-cutoff-timestamps.md / historical_data.md.
    Calling this for every run (never a cached/assumed value) is the
    point: the cutoff "will be regularly updated, advancing forward over
    time" per the doc, so a stale assumption would silently misroute a
    future window to the wrong endpoint."""
    from services.kalshi import transport

    resp = await transport.call_with_backoff(client.get_historical_cutoff)
    data = resp.model_dump(mode="json")
    iso = data["trades_created_ts"]
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()


async def fetch_trades_segment(
    client, endpoint: str, ticker: str | None, start_ts: float, end_ts: float, page_limit: int = 1000,
) -> list[dict]:
    """Pages one [start_ts, end_ts) window through either
    `/markets/trades` (endpoint="live") or `/historical/trades`
    (endpoint="historical") until the response's cursor is empty
    (docs/kalshi/get-trades.md / get-historical-trades.md: "an empty
    cursor indicates no more pages are available"), same loop shape
    main.py's own _fetch_trade_tape uses for the live endpoint."""
    from services.kalshi import transport

    method = getattr(client, _TRADE_METHOD_BY_ENDPOINT[endpoint])
    trades: list[dict] = []
    cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "min_ts": int(start_ts), "max_ts": int(end_ts), "limit": page_limit, "cursor": cursor,
        }
        if ticker:
            kwargs["ticker"] = ticker
        resp = await transport.call_with_backoff(method, **kwargs)
        data = resp.model_dump(mode="json")
        page = data.get("trades") or []
        trades.extend(page)
        cursor = data.get("cursor") or ""
        if not cursor:
            break
    return trades


def _existing_trade_ids(db_path: Path | None, trade_ids: list[str]) -> set[str]:
    """Which of trade_ids already exist in db_path's raw_trades table -
    read-only (URI mode=ro), so this never creates or modifies db_path.
    Returns an empty set (never raises) when the file or table doesn't
    exist yet - "nothing can already be present" is the correct answer
    then, not an error."""
    if not trade_ids or db_path is None:
        return set()
    db_path = Path(db_path)
    if not db_path.exists():
        return set()
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return set()
    try:
        placeholders = ",".join("?" for _ in trade_ids)
        rows = conn.execute(
            f"SELECT trade_id FROM raw_trades WHERE trade_id IN ({placeholders})", trade_ids,
        ).fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()


def write_ledger_entries(ledger_db_path: Path, entries: list[dict]) -> int:
    """Appends provenance rows to the tool-owned backfill ledger (see
    module docstring for why this exists instead of a raw_trades column).
    INSERT OR IGNORE on UNIQUE(store, trade_id) - re-running for an
    overlapping window records each trade_id's provenance exactly once.
    Returns the count of NEWLY recorded entries (0 on a pure re-run)."""
    if not entries:
        return 0
    ledger_db_path = Path(ledger_db_path)
    ledger_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ledger_db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_LEDGER_DDL)
        written = 0
        for e in entries:
            cur = conn.execute(
                "INSERT OR IGNORE INTO backfilled_rows "
                "(store, trade_id, ticker, source_endpoint, window_start_ts, window_end_ts, "
                "fetched_at, issue_ref, already_present) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    e["store"], e["trade_id"], e["ticker"], e["source_endpoint"],
                    e["window_start_ts"], e["window_end_ts"], e["fetched_at"],
                    e.get("issue_ref"), 1 if e.get("already_present") else 0,
                ),
            )
            written += cur.rowcount
        conn.commit()
        return written
    finally:
        conn.close()


def _row_preview(row: tuple) -> dict:
    (trade_id, ticker, series, _observed_at, exchange_ts, _tos, _tbs, _tsl,
     resolved_side, count_fp, yes_price, no_price, notional, is_block_trade, _excluded, _raw_json) = row
    return {
        "trade_id": trade_id, "ticker": ticker, "series": series, "exchange_ts": exchange_ts,
        "resolved_side": resolved_side, "count_fp": count_fp, "yes_price_dollars": yes_price,
        "no_price_dollars": no_price, "notional_usd": notional, "is_block_trade": bool(is_block_trade),
    }


async def run_backfill(
    *, start_ts: float, end_ts: float, tickers: list[str] | None, series: list[str] | None,
    db_path: Path | None, ledger_db_path: Path | None, dry_run: bool, issue_ref: str | None,
    page_limit: int = 1000, base_url: str | None = None, client=None,
) -> dict:
    """Orchestrates one backfill run. `client` is injectable (tests pass a
    stub with get_historical_cutoff/get_trades/get_trades_historical) - a
    real caller (main() below) leaves it None and this function builds and
    closes a real services.kalshi.transport public client itself.

    base_url=None (the default) resolves from the app's own
    config/settings.yaml `kalshi.base_url` at call time, the same pattern
    tools/kalshi_rate_limit_probe.py already uses - not a literal host
    string here: tools/quality_audit/kalshi_boundary.py's CI guard (check
    2) hard-fails a raw Kalshi host constant outside services/kalshi/ and
    its small reviewed allowlist, and that check's own remediation text
    names exactly this alternative ("config-provided base URLs"). This
    reads the app's config, not writes/schedules/imports it, so it is not
    the tooling/application overlap CLAUDE.md's separation rule forbids.

    dry_run=True never touches db_path or ledger_db_path at all (no
    sqlite3.connect call of any kind against either path) - only the
    Kalshi fetch and the in-memory row build happen, so it is safe to run
    against the real Kalshi API before ever pointing at a real data/*.db."""
    if not tickers and not series:
        raise ValueError(
            "at least one of tickers or series is required - never guess which market/series "
            "a gap belongs to; supply it from the same fault-log/diagnostic investigation that "
            "named the loss window"
        )

    owns_client = client is None
    if client is None:
        from services.kalshi import transport

        if base_url is None:
            from services.config.config_store import config_store

            base_url = config_store.get()["kalshi"]["base_url"]
        client = transport.build_public_client(base_url)
    try:
        cutoff_ts = await get_trades_created_cutoff_ts(client)
        segments = classify_window(start_ts, end_ts, cutoff_ts)

        fetch_targets = tickers if tickers else [None]
        tagged_trades: dict[str, tuple[str, dict]] = {}  # trade_id -> (endpoint, trade)
        segment_reports = []
        for endpoint, seg_start, seg_end in segments:
            fetched_this_segment = 0
            for ticker in fetch_targets:
                page_trades = await fetch_trades_segment(client, endpoint, ticker, seg_start, seg_end, page_limit)
                fetched_this_segment += len(page_trades)
                for t in page_trades:
                    tid = t.get("trade_id")
                    if tid and tid not in tagged_trades:
                        tagged_trades[tid] = (endpoint, t)
            segment_reports.append({
                "endpoint": endpoint, "start_ts": seg_start, "end_ts": seg_end, "fetched": fetched_this_segment,
            })

        candidates = list(tagged_trades.values())
        if series:
            allowed = set(series)
            candidates = [
                (ep, t) for ep, t in candidates
                if signal_log.series_of(t.get("ticker") or t.get("market_ticker") or "") in allowed
            ]

        now = time.time()
        built = [(ep, build_raw_trade_row(t, now)) for ep, t in candidates]

        report: dict[str, Any] = {
            "window": {"start_ts": start_ts, "end_ts": end_ts},
            "trades_created_ts_cutoff": cutoff_ts,
            "segments": segment_reports,
            "candidate_trade_count": len(built),
            "tickers_seen": sorted({row[1] for _ep, row in built}),
            "dry_run": dry_run,
            "sample_rows": [_row_preview(row) for _ep, row in built[:5]],
        }
        if dry_run:
            return report

        from services import capture_writer

        existing_ids = _existing_trade_ids(db_path, [row[0] for _ep, row in built])
        capture_writer._STORE_PATHS["raw_trades"] = Path(db_path)

        already_present = 0
        ledger_entries = []
        for endpoint, row in built:
            trade_id = row[0]
            is_existing = trade_id in existing_ids
            already_present += 1 if is_existing else 0
            capture_writer.submit("raw_trades", row)
            ledger_entries.append({
                "store": "raw_trades", "trade_id": trade_id, "ticker": row[1],
                "source_endpoint": endpoint, "window_start_ts": start_ts, "window_end_ts": end_ts,
                "fetched_at": now, "issue_ref": issue_ref, "already_present": is_existing,
            })

        capture_writer.flush_now("raw_trades")
        dropped = capture_writer.dropped_count().get("raw_trades", 0)
        overflow = capture_writer.overflow_dropped_count().get("raw_trades", 0)
        ledger_written = write_ledger_entries(ledger_db_path, ledger_entries) if ledger_db_path else 0

        report.update({
            "written": len(built) - already_present,
            "already_present": already_present,
            "dropped": dropped,
            "overflow_dropped": overflow,
            "ledger_entries_recorded": ledger_written,
        })
        return report
    finally:
        if owns_client:
            await client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m tools.historical_data_backfill",
        description=(
            "One-off backfill of a specific, already-quantified raw_trades gap (issue #265, "
            "scoped to #209/#211) from Kalshi's live/historical trades REST surface. "
            "Always run --dry-run first; it never touches --db-path or --ledger-db-path."
        ),
    )
    parser.add_argument("--start", required=True, help="window start: Unix seconds or ISO-8601 (e.g. 2026-08-27T20:22:15Z)")
    parser.add_argument("--end", required=True, help="window end: Unix seconds or ISO-8601")
    parser.add_argument("--ticker", dest="tickers", action="append", default=None,
                        help="repeatable; narrow to specific ticker(s). At least one of --ticker/--series is required.")
    parser.add_argument("--series", action="append", default=None,
                        help="repeatable; narrow to specific series prefix(es), e.g. KXBTC15M. "
                             "Fetches exchange-wide and filters client-side (no server-side series "
                             "filter exists) - slow on a multi-day window; prefer --ticker when known.")
    parser.add_argument("--base-url", default=None,
                        help="override the exchange REST base URL; defaults to the app's own "
                             "configured Kalshi API host (config/settings.yaml)")
    parser.add_argument("--db-path", default=str(_RAW_TRADES_DB_PATH),
                        help="raw_trades store to write into (default: the real data/series_watcher.db - "
                             "override for any non-dry-run test/manual verification)")
    parser.add_argument("--ledger-db-path", default=str(_LEDGER_DB_PATH))
    parser.add_argument("--issue", dest="issue_ref", default=None, help="free-text provenance label, e.g. 211")
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    ns = parser.parse_args(argv)
    if not ns.tickers and not ns.series:
        parser.error("at least one of --ticker or --series is required - never guess which market/series a gap belongs to")
    return ns


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv)
    start_ts = parse_ts(ns.start)
    end_ts = parse_ts(ns.end)
    report = asyncio.run(run_backfill(
        start_ts=start_ts, end_ts=end_ts, tickers=ns.tickers, series=ns.series,
        db_path=Path(ns.db_path), ledger_db_path=Path(ns.ledger_db_path),
        dry_run=ns.dry_run, issue_ref=ns.issue_ref, page_limit=ns.page_limit, base_url=ns.base_url,
    ))
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
