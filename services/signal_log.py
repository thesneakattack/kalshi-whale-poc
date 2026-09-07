"""
Persistent log of every whale signal seen, plus — once a market settles —
whether the whale actually called it right. This is what makes "whale track
record over the last 30 days" a real, growing number instead of something
derived from the last 50 in-memory signals, which reset on every restart and
never knew what happened after the fact.

SQLite file lives at data/signal_log.db — gitignored, never commit it.

Honesty note: resolution checking relies on Kalshi's market `result` field
being "yes"/"no" once settled. That's a reasonable reading of the API but,
like the account-balance field names elsewhere in this app, wasn't
independently confirmed against every market type — see /status.

Resolution has two writers since P8 Task 30 (2026-08-28): the WS
market_lifecycle_v2 `settled` handler (resolve_from_market_results, per
ticker, the instant a market settles) and main.py's 30s/200-batch REST poll
(mark_resolved, now the slower safety net for tickers this app wasn't
watching at settlement time). Until then the REST poll was the ONLY caller
in the app while the same settled event already resolved four other stores
for the same ticker - see docs/superpowers/research/2026-08-25-realtime-
data-plane-known-findings.md H13. Both paths are idempotent (WHERE
resolved = 0), so a row graded by either is never reopened or re-graded.
"""
import asyncio
import contextlib
import json
import sqlite3
import time
from pathlib import Path

from services import history_push, title_cache
from services.diagnostics import _aio_db

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "signal_log.db"


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # data/signal_log.db is a live file the running dev server reads/writes
    # (CLAUDE.md) - CREATE TABLE IF NOT EXISTS alone doesn't add a column to
    # an existing table with existing rows, so a new column needs an
    # explicit, idempotent ALTER TABLE guarded by a check - same pattern
    # services/paper_broker.py already established for config_fingerprint.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


async def _ensure_schema_aio(conn) -> None:
    """_aio_db.connection_for()'s schema_init hook for this module's async
    read path (issue #410).

    Deliberately does NOT restate _init_schema()'s DDL as a parallel set of
    `await conn.execute(...)` calls, the way services/series_watcher.py's
    function of this name does. That module's DDL is four short CREATE
    INDEX statements plus two shared constants; this module's is a CREATE
    TABLE, four CREATE INDEXes and seven guarded _add_column_if_missing
    migrations (corrected 2026-09-05 - an earlier draft of this docstring
    said three/eight; recounted directly against _init_schema below), several
    with real history behind them. A hand-maintained async copy of that
    would be a second source of truth for a schema that still changes, and
    the failure mode - one path silently missing a column another path
    expects - is exactly the kind of drift this codebase has already been
    bitten by.

    So it runs the ONE existing definition instead, on a worker thread
    (_init_schema is synchronous and takes a sqlite3.Connection). The DDL
    is visible to the aiosqlite connection already open against the same
    file by the time this returns because _init_schema(conn) runs BEFORE
    _connect()'s `with conn:` block, under sqlite3's default autocommit
    mode - not because that block later commits (corrected 2026-09-05: an
    earlier draft of this docstring named the wrong mechanism; the `with
    conn:` wrapper only covers the caller's own write, which happens after
    schema init has already committed). The `conn` parameter is unused for
    that reason; the hook's contract is only "run once on this key's first
    open".

    Costs one short-lived sync connection on first open per (loop, path) -
    not per call."""
    await asyncio.to_thread(_ensure_schema_sync)


def _ensure_schema_sync() -> None:
    """Opens and immediately closes a _connect(), purely for the schema
    initialization _connect() performs on every open. See
    _ensure_schema_aio above for why this indirection exists rather than a
    duplicated async DDL block."""
    with _connect():
        pass


def _init_schema(conn: sqlite3.Connection) -> None:
    """Every CREATE TABLE / CREATE INDEX / _add_column_if_missing call this
    module needs, extracted out of _connect() (write-path capacity fix Task
    2) so the cached scoring-read connection (_scoring_read_connection
    below, via services/whalewatchers/_scoring_pool.py) can run the exact
    same schema init on its own first connect, without also going through
    _connect()'s per-call sqlite3.connect()/WAL-pragma path."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            series TEXT NOT NULL,
            side TEXT NOT NULL,
            size INTEGER NOT NULL,
            confidence REAL NOT NULL,
            source TEXT NOT NULL,
            seen_at REAL NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            correct INTEGER,
            resolved_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_resolved ON signals (resolved)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_series ON signals (series)")
    # for_ticker() below - 2026-08-16 direct report ("my position doesnt show
    # hardly ANY whale signal relationship... since to drive the decision
    # making"): without this, a per-ticker/since-timestamp query is a full
    # table scan, increasingly expensive as this table grows (38k+ rows and
    # climbing fast under the current stress-test config).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_ticker_seen ON signals (ticker, seen_at)")
    # services/whale_calibration/confidence_calibration.py's whole input - the individual
    # confidence factors, not just the blended score, so a future pass can
    # ask "which factors actually predicted a correct call" instead of only
    # ever seeing the number they were already blended into. Added after
    # the table above already shipped with live rows, hence the guarded
    # ALTER TABLE rather than a column in the CREATE statement. Nullable:
    # only real providers that compute a breakdown populate it (see
    # services/whalewatchers/kalshi_trade_tape.py) - simulator-sourced rows
    # leave it null, and calibration explicitly filters to real ones anyway.
    _add_column_if_missing(conn, "signals", "factors_json", "TEXT")
    # Gap 8 of docs/config-tuning-data-gaps-2026-08-10.md - the raw inputs
    # behind the factor breakdown, not just the already-derived 0-1 scores
    # factors_json holds. Nullable/separate columns rather than folded into
    # factors_json: these are real dollar/ratio magnitudes, not 0-1 scores,
    # and confidence_calibration.py's bucketing assumes every factors_json
    # key IS a 0-1 score - mixing raw magnitudes in there would corrupt
    # that. Only real providers that capture a raw_context populate these
    # (see services/whalewatchers/kalshi_trade_tape.py) - simulator-sourced
    # rows leave them null, same convention as factors_json itself.
    _add_column_if_missing(conn, "signals", "raw_notional_usd", "REAL")
    _add_column_if_missing(conn, "signals", "raw_spread", "REAL")
    _add_column_if_missing(conn, "signals", "raw_volume_24h", "REAL")
    # Head-of-line-blocking fix (2026-08-15, direct live report: "why are
    # there SO MANY unresolved signals???" - confirmed live: 31,833 of
    # 32,101 signals unresolved, 31,469 of those over 24h old). Root cause:
    # unresolved_batch's query was a bare "ORDER BY seen_at ASC LIMIT N"
    # with no memory of ever having tried a row before. A signal logged
    # against a genuinely long-horizon market (confirmed live examples:
    # KXMAYORLA-26-KBAS closes 2027-06-02, KXFEDDECISION-26SEP-H0 closes
    # 2026-09-16 - both legitimately still active, not broken) sorts to the
    # front of "oldest unresolved" and STAYS there tick after tick forever,
    # since nothing ever marks it resolved - meaning it (and any other
    # long-horizon signal) permanently occupies a slot in every single
    # batch, forever, starving every signal logged after it of ever being
    # checked even once. This is why the backlog is one-sided (269 resolved
    # vs tens of thousands never even attempted) rather than a healthy mix
    # of resolved/genuinely-pending. last_checked_at lets unresolved_batch
    # skip anything checked recently (see its own recheck_cooldown_sec) so
    # a handful of long-horizon rows can no longer crowd out everything
    # behind them - they still get periodically re-checked (in case they
    # DO resolve), just not on literally every tick forever.
    _add_column_if_missing(conn, "signals", "last_checked_at", "REAL")
    # 2026-08-16 direct report: "the signal history doesnt show the price a
    # position was bought at, in the log nor in the whale watch signal
    # stream." WhaleSignal.price (always the yes-side implied probability at
    # print time, same convention as everywhere else in this app) was only
    # ever forwarded to PaperBroker.open_position/Trade - never persisted
    # here, so a signal that never became a trade (the overwhelming
    # majority) had no price on record anywhere, and even a signal that DID
    # trade required joining back to the trades table to find out at what
    # price. Nullable: rows logged before this field existed have no price
    # to backfill.
    _add_column_if_missing(conn, "signals", "price", "REAL")
    # excluded (2026-08-17 direct request: "protect against tests that
    # corrupt and keeping logs where valuable insights are gained
    # pristine"). A row logged during a deliberate experiment - a threshold
    # dropped to probe latency, a config being swept - is real data about
    # the exchange but is NOT evidence about how the strategy performs, and
    # averaging it into a 30-day statistic silently corrupts every
    # sample-size-gated heuristic downstream. Before this the only remedy
    # was deletion (services/reset/reset_log.py records a real instance: 19,995
    # rows destroyed to move a headline win rate off 64.8% back to its true
    # 74.2%), which fixes the number by throwing away history CLAUDE.md
    # explicitly calls a first-class asset.
    #
    # Default 0 so every existing row, and every row written by any code
    # path that doesn't know about this column, counts exactly as it did
    # before - this is opt-in exclusion, never opt-out inclusion.
    _add_column_if_missing(conn, "signals", "excluded", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_excluded ON signals (excluded, seen_at)")


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site (21 of them)
    keeps working unchanged - this yields the same conn as before, but now
    closes it on exit (2026-09-03, Task 5 of docs/archive/lane-6-observability-quality-safety/
    plans/2026-09-03-tier0-live-incident-remediation.md, moved there
    2026-09-06, planning-lanes migration), same fix and same
    reasoning as market_history.py's Task 2.

    The `try:` starts immediately after `sqlite3.connect()` succeeds, not
    after the PRAGMA/schema-init setup below (2026-09-03 follow-up fix): a
    setup failure would otherwise leave `conn` open with nothing left to
    close it."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        # WAL mode (2026-08-11, real live incident): rollback-journal mode
        # serializes ALL writers and readers against each other for the whole
        # transaction; WAL lets readers proceed concurrently with a writer and
        # is the standard hardening step for exactly the bursty-write scenario
        # that took the app down (trade-tape volume overwhelming a per-call
        # sqlite3.connect()). idempotent - safe to run on every connect.
        conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def _scoring_read_connection() -> sqlite3.Connection:
    """Cached connection for the two whale-scoring read functions only
    (recent_sides_for_ticker, cluster_factor) - write-path capacity fix Task
    2. services/whalewatchers/kalshi_trade_tape.py calls these once per
    incoming whale trade; a fresh sqlite3.connect() per call was a measured
    contributor to a live write-path capacity incident. Every other caller
    of this module keeps using _connect() unchanged - this cache is scoped
    to just these two read paths, not a general replacement.

    Import deferred to call time, not module load time: services/
    whalewatchers/__init__.py unconditionally imports kalshi_trade_tape.py,
    which (via market_history.py) does `from services.signal_log import
    series_of` at ITS OWN module top level - so a top-level `from
    services.whalewatchers import _scoring_pool` here would be a genuine,
    unavoidable circular import (whichever module starts loading first,
    the other needs a name that doesn't exist yet mid-import). By the time
    this function actually runs, both modules are already fully loaded, so
    the import is a cheap sys.modules lookup with no cycle."""
    from services.whalewatchers import _scoring_pool
    DB_PATH.parent.mkdir(exist_ok=True)
    return _scoring_pool.cached_read_connection(DB_PATH, _init_schema)


def series_of(ticker: str) -> str:
    """Real series_ticker via title_cache's market_titles -> event_titles join
    when resolvable (services/title_cache.py:series_ticker_for) - the market's
    own documented event_ticker/series_ticker chain (docs/kalshi/get-market.md,
    get-events.md), never the ticker-string prefix. Falls back to the prefix
    heuristic ONLY for a ticker this app hasn't cached an event for yet - the
    general case this used to be, now the exception. Public (not
    underscore-prefixed) since strategy_engine.py's manual excluded_series
    gate needs the exact same series definition the automatic win-rate filter
    already uses - one definition, not two that could quietly drift apart.

    2026-08-30 fix (kalshi-category-data-completeness Task 3): the old
    ticker.split("-")[0] mis-derived MVE/sharded tickers like
    "KXMVECROSSCATEGORY0-SHARD1" as their own series instead of the real
    "KXMVECROSSCATEGORY0" (docs/kalshi/CHEATSHEET.md). Pre-cutover
    signals.series rows are NOT backfilled - measured coverage for the
    join against historical MVE/sharded tickers is 0 of 12,357 tickers
    (see the design spec's §1.4 "Correction, found in design review"), so a
    backfill would rewrite accumulated history (CLAUDE.md) for zero actual
    gain; old and new rows simply disagree for that minority going forward,
    a known, named accounting seam, not a bug to chase further.

    2026-08-31 fix round (PR review): this function is called unconditionally
    on the exchange-wide trade-tape hot path (kalshi_trade_tape.py's
    min_contracts_for() and its trades_observed_by_series build), so
    title_cache.series_ticker_for() memoizes its own DB round trip in-process
    (see that function's own docstring) - callers of series_of() do not need
    to memoize independently. It also still never raises: a title_cache DB
    failure degrades to the prefix fallback inside series_ticker_for() itself,
    the same as an uncached ticker, so every existing call site (none of
    which were written to catch an exception from this function) keeps its
    original never-raises contract."""
    if not ticker:
        return ticker
    real = title_cache.series_ticker_for(ticker)
    return real or ticker.split("-")[0]


def log_signal(
    ticker: str, side: str, size: int, confidence: float, source: str,
    seen_at: float | None = None, factors: dict | None = None, raw_context: dict | None = None,
    price: float | None = None, excluded: bool = False,
):
    """excluded=True marks this signal as not-evidence at write time - used
    when an experiment is active (services/data_quarantine.is_active), so a
    deliberate test window never enters the statistics in the first place
    rather than having to be cleaned up afterwards. The row is still
    written in full; only the stats readers skip it."""
    raw_context = raw_context or {}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO signals "
            "(ticker, series, side, size, confidence, source, seen_at, factors_json, "
            "raw_notional_usd, raw_spread, raw_volume_24h, price, excluded) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ticker, series_of(ticker), side, size, confidence, source, seen_at or time.time(),
                json.dumps(factors) if factors is not None else None,
                raw_context.get("notional_usd"), raw_context.get("spread"), raw_context.get("volume_24h"),
                price, 1 if excluded else 0,
            ),
        )


def mark_excluded_range(after: float, before: float, excluded: bool = True) -> int:
    """Flip the excluded flag on every signal in (after, before] WITHOUT
    deleting anything - the non-destructive alternative to clear_range()
    (2026-08-17 direct request: "protect against tests that corrupt and
    keeping logs where valuable insights are gained pristine").

    Deletion was the only tool for this before, and it worked but cost
    real history: a 28-minute deliberate latency test on 2026-08-16 dragged
    the 30-day headline win rate from 74.2% to 64.8%, and the only way to
    fix the number was to destroy 19,995 rows that still described real
    exchange behaviour. Excluding instead keeps every row queryable and is
    fully reversible (excluded=False restores them), which matters because
    CLAUDE.md treats accumulated history as a first-class asset.

    Returns how many rows changed. See services/data_quarantine.py for the
    provenance layer that records WHY a range was excluded."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE signals SET excluded = ? WHERE seen_at > ? AND seen_at <= ?",
            (1 if excluded else 0, after, before),
        )
        return cur.rowcount


def recent_sides_for_ticker(ticker: str, since_ts: float) -> list[str]:
    """Every side ("yes"/"no") logged for this exact ticker since since_ts -
    services/whalewatchers/kalshi_trade_tape.py's input for scoring whether
    a new print agrees with recent ones on the same market (composite_confidence_
    breakdown's agreement_factor). Ticker-scoped, not series-scoped like
    series_stats - "did whales agree on THIS market" is a narrower, more
    literal question than "how do whales usually do on this type of
    market."""
    conn = _scoring_read_connection()
    rows = conn.execute(
        "SELECT side FROM signals WHERE ticker = ? AND seen_at >= ?", (ticker, since_ts),
    ).fetchall()
    return [r[0] for r in rows]


def cluster_factor(ticker: str, side: str, size: float, since_ts: float, max_size_ratio: float = 4.0) -> float:
    """How much this (not-yet-logged) trade looks like it's extending an
    active same-actor accumulation pattern, rather than standing alone as an
    isolated large print - the live, per-signal analog of find_clusters()
    below, feeding composite_confidence_breakdown's cluster_factor (see
    services/confidence_scoring.py). Barclay & Warner's stealth-trading finding
    (docs/prediction-market-strategy-alignment-plan.md Part 2.1) is why this
    exists: the strongest real evidence on which large trades are actually
    informed says sophisticated informed traders deliberately split into a
    run of similar-sized prints rather than one conspicuous block - so a
    print that's part of such a run is a stronger signal than an equally
    large one with nothing else like it nearby, not a weaker one.

    Unlike agreement_factor's "no history = None" honest-absence idiom
    (services/confidence_scoring.py, Task 5/6), "no similar-sized recent
    prints" is itself informative here (an isolated print, exactly the
    profile a pure notional-size threshold already treats as its only
    signal) - so this returns 0.0, not 0.5, when nothing qualifies. Scales
    toward 1.0 as more size-compatible prints pile
    up, capped at 3 (matching find_clusters' own "more prints = more likely
    real accumulation" intuition without trying to reproduce its full
    sequential-run algorithm here - this is a cheaper, real-time proxy for
    one trade, not a retrospective full-history scan)."""
    conn = _scoring_read_connection()
    rows = conn.execute(
        "SELECT size FROM signals WHERE ticker = ? AND side = ? AND seen_at >= ?",
        (ticker, side, since_ts),
    ).fetchall()
    matches = sum(1 for (s,) in rows if _size_ratio_ok(s, size, max_size_ratio))
    return min(matches / 3, 1.0)


def unresolved_batch(limit: int = 3, older_than_sec: float = 600, recheck_cooldown_sec: float = 3600) -> list[dict]:
    """Oldest unresolved signals whose market has had at least `older_than_sec`
    to plausibly settle — avoids re-checking a market seconds after the print,
    and keeps each poll tick's extra API calls small.

    recheck_cooldown_sec (2026-08-15, see this module's own last_checked_at
    column comment for the real incident this closes): also skips anything
    checked within the last recheck_cooldown_sec, and stamps last_checked_at
    on every row this call returns before returning them - a long-horizon
    signal that keeps coming back unresolved now only re-claims a batch
    slot once per cooldown window instead of every single tick forever,
    letting the query's own ORDER BY seen_at ASC actually progress into the
    rest of the backlog. Stamping happens here (not left to the caller) so
    a row is "claimed" the moment it's selected, regardless of what the
    caller does with it afterward - mark_resolved() is still the only thing
    that ever sets resolved=1."""
    now = time.time()
    cutoff = now - older_than_sec
    recheck_cutoff = now - recheck_cooldown_sec
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side FROM signals "
            "WHERE resolved = 0 AND seen_at < ? AND (last_checked_at IS NULL OR last_checked_at < ?) "
            "ORDER BY seen_at ASC LIMIT ?",
            (cutoff, recheck_cutoff, limit),
        ).fetchall()
        if rows:
            conn.executemany(
                "UPDATE signals SET last_checked_at = ? WHERE id = ?",
                [(now, r[0]) for r in rows],
            )
    return [{"id": r[0], "ticker": r[1], "side": r[2]} for r in rows]


def mark_resolved(signal_id: int, correct: bool):
    with _connect() as conn:
        conn.execute(
            "UPDATE signals SET resolved = 1, correct = ?, resolved_at = ? WHERE id = ?",
            (1 if correct else 0, time.time(), signal_id),
        )
    # History-push hook (design §4.3/§2 - loadBacktestSweeps/
    # loadCalibrationReport are signal-resolution-driven, not trade-close-
    # driven). This module's other resolver (resolve_from_market_results
    # below) fires its own call, since it does its own UPDATE rather than
    # calling this function.
    history_push.mark_history_changed()


def resolve_from_market_results(ticker: str, result: str) -> int:
    """Resolve every still-unresolved signal for one ticker against its final
    market result - the ticker-scoped entry point the market_lifecycle_v2
    `settled` handler calls (P8 Task 30, 2026-08-27). Until this existed,
    mark_resolved had exactly one caller in the whole application - main.py's
    30s/200-batch REST poll - while the same settled event was already
    resolving market_history, settlement_edge, market_analyst_agent and
    candidate_log for the same ticker the instant it arrived; signal_log was
    the one store left waiting for the next REST batch regardless.

    Each signal carries its own side, so correctness is per row, not uniform
    per ticker (unlike the other four resolvers). Idempotent by the same
    `WHERE resolved = 0` guard the rest of this module relies on: a row
    resolved by either path is never reopened or re-graded, so firing from
    both the WS path and the REST-poll fallback stays safe. Same naming as
    the sibling resolvers. Returns the number of rows resolved."""
    result = (result or "").strip().lower()
    if result not in ("yes", "no"):
        return 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, side FROM signals WHERE ticker = ? AND resolved = 0", (ticker,),
        ).fetchall()
        if not rows:
            return 0
        now = time.time()
        conn.executemany(
            "UPDATE signals SET resolved = 1, correct = ?, resolved_at = ? WHERE id = ? AND resolved = 0",
            [(1 if result == side else 0, now, row_id) for row_id, side in rows],
        )
    # History-push hook, only when rows were actually resolved (the `if not
    # rows: return 0` guard above already makes this branch dead when
    # nothing changed) - see mark_resolved's own comment above.
    history_push.mark_history_changed()
    return len(rows)


def series_stats(ticker: str, days: int = 30) -> dict:
    """Whale accuracy scoped to this market's series/category — e.g. "how have
    whales done on Best Picture predictions", not just "how have they done
    on this one already-mostly-decided market"."""
    series = series_of(ticker)
    since = time.time() - days * 86400
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE series = ? AND seen_at >= ?", (series, since)
        ).fetchone()[0]
        resolved_count, correct_sum = conn.execute(
            "SELECT COUNT(*), SUM(correct) FROM signals WHERE series = ? AND seen_at >= ? AND resolved = 1 "
            "AND excluded = 0",
            (series, since),
        ).fetchone()
    resolved_count = resolved_count or 0
    correct_count = correct_sum or 0
    return {
        "series": series,
        "window_days": days,
        "total_signals": total,
        "resolved": resolved_count,
        "correct": correct_count,
        "win_rate": round(correct_count / resolved_count * 100, 1) if resolved_count else None,
    }


def series_stats_bulk(tickers: list[str], days: int = 30) -> dict[str, dict]:
    """Same per-ticker result series_stats(ticker, days) would return for
    each of tickers, computed on one connection instead of one _connect()
    per ticker (main.py's series_track_record build, root-cause report
    C1's specifically named series_stats N+1 at main.py:736 - realtime
    data-plane remediation plan P1 Task 8). _connect() alone costs ~12.6ms;
    with N markets watched that's N x 12.6ms of loop-blocking connection
    overhead for what is, after series_of() collapses tickers to their
    series, usually a handful of distinct queries.

    Also deduplicates by series (many tickers - e.g. every BTC 15-minute
    market - share one series), so two tickers in the same series cost one
    query pair, not two: same output shape as calling series_stats()
    individually, fewer redundant round-trips (CLAUDE.md's efficiency
    axis), not a behavior change."""
    if not tickers:
        return {}
    since = time.time() - days * 86400
    series_by_ticker = {ticker: series_of(ticker) for ticker in tickers}
    stats_by_series: dict[str, dict] = {}
    with _connect() as conn:
        for series in set(series_by_ticker.values()):
            total = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE series = ? AND seen_at >= ?", (series, since)
            ).fetchone()[0]
            resolved_count, correct_sum = conn.execute(
                "SELECT COUNT(*), SUM(correct) FROM signals WHERE series = ? AND seen_at >= ? AND resolved = 1 "
                "AND excluded = 0",
                (series, since),
            ).fetchone()
            resolved_count = resolved_count or 0
            correct_count = correct_sum or 0
            stats_by_series[series] = {
                "series": series,
                "window_days": days,
                "total_signals": total,
                "resolved": resolved_count,
                "correct": correct_count,
                "win_rate": round(correct_count / resolved_count * 100, 1) if resolved_count else None,
            }
    # dict(...) per ticker: two tickers sharing a series must not share the
    # same dict object, or an in-place mutation by one caller would leak
    # into the other's "independent" entry.
    return {ticker: dict(stats_by_series[series]) for ticker, series in series_by_ticker.items()}


def resolved_signals_with_series(days: int = 30) -> list[dict]:
    """Every resolved signal's series + outcome, no factors_json filter
    (unlike resolved_signals_with_factors, which exists for confidence
    calibration specifically and deliberately excludes simulator-sourced
    rows) - services/backtest/backtest.py's min_whale_winrate_pct_sweep needs the
    full resolved population, tagged by series, to recombine under a
    candidate floor."""
    since = time.time() - days * 86400
    with _connect() as conn:
        rows = conn.execute(
            "SELECT series, correct FROM signals WHERE seen_at >= ? AND resolved = 1 AND excluded = 0",
            (since,),
        ).fetchall()
    return [{"series": series, "correct": bool(correct)} for series, correct in rows]


def all_series_stats(days: int = 30) -> dict[str, dict]:
    """Same shape as series_stats() but for every series at once (one GROUP
    BY query instead of N per-series ones) - what services/backtest/backtest.py's
    min_whale_winrate_pct_sweep (Gap 2, docs/config-tuning-data-gaps-
    2026-08-10.md) needs: "if the floor were X instead of Y, which series
    would be excluded, and what would the aggregate win rate of what's left
    look like." win_rate is None for a series with 0 resolved signals in
    the window, same "don't show a number you can't back" convention as
    series_stats()."""
    since = time.time() - days * 86400
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT series, COUNT(*) AS resolved, SUM(correct) AS correct_sum
            FROM signals WHERE seen_at >= ? AND resolved = 1 AND excluded = 0
            GROUP BY series
            """,
            (since,),
        ).fetchall()
    out = {}
    for series, resolved, correct_sum in rows:
        resolved = resolved or 0
        correct_count = correct_sum or 0
        out[series] = {
            "series": series,
            "window_days": days,
            "resolved": resolved,
            "correct": correct_count,
            "win_rate": round(correct_count / resolved * 100, 1) if resolved else None,
        }
    return out


def signal_count_for_series_since(series: str, since_ts: float) -> int:
    """How many whale-qualifying signals this series has produced since
    since_ts - the numerator services/series_evaluator.py needs for its
    qualifying-rate verdict. Mirrors series_stats()'s query shape but
    scoped to an arbitrary timestamp (a series' own first_seen_at) rather
    than a fixed days window, and without the win/loss resolution logic
    series_stats needs - series_evaluator only cares about *how many*
    prints qualified, not whether they were later correct."""
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM signals WHERE series = ? AND seen_at >= ?", (series, since_ts)
        ).fetchone()[0]


def count_for_ticker(ticker: str, since_ts: float | None = None) -> int:
    """The true count behind for_ticker()'s row cap - signals_since_entry_
    count needs the real number (a stress-test-load ticker can clear
    for_ticker's own row limit easily), not however many rows happened to
    be fetched for the lean computation. Same index, a COUNT(*) query is
    cheap regardless of how large the underlying result set is."""
    where = "WHERE ticker = ?"
    params: list = [ticker]
    if since_ts is not None:
        where += " AND seen_at >= ?"
        params.append(since_ts)
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM signals {where}", params).fetchone()[0]


def for_ticker(ticker: str, since_ts: float | None = None, limit: int = 500) -> list[dict]:
    """Every signal on one exact ticker, newest first, optionally since a
    given timestamp (a position's own opened_at) - 2026-08-16 direct
    report: an open position's card showed only the single whale print
    that triggered its entry, nothing about whale activity on that same
    ticker since then, making the ongoing sentiment-driven exit reasoning
    (_whale_lean/_exit_confidence in strategy_engine.py) invisible even
    though it's real and running. state["signal_feed"] can't answer this -
    it's a single 50-slot window shared across every ticker in the app, so
    a busy ticker crowds out a quiet one within seconds. This queries the
    full persisted history instead, scoped to exactly the ticker/window
    that matters. limit is a safety cap, not a real expectation - a
    position held for the router's stress-test load already produces far
    fewer than 500 signals per ticker in normal (non-$1-notional) use."""
    cols = ["id", "ticker", "series", "side", "size", "confidence", "source", "seen_at", "price"]
    where = "WHERE ticker = ?"
    params: list = [ticker]
    if since_ts is not None:
        where += " AND seen_at >= ?"
        params.append(since_ts)
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM signals {where} ORDER BY seen_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def clear_all():
    """Wipes the entire whale track record - every logged signal and its
    resolution outcome. Only ever triggered deliberately (Config tab's Danger
    Zone): this is the "how have whales actually done on real markets" history,
    normally kept intact across paper/shadow resets on purpose."""
    with _connect() as conn:
        conn.execute("DELETE FROM signals")


def count_range(before: float | None = None, after: float | None = None) -> int:
    """How many signal rows fall in (after, before] - the Danger Zone
    preview step's answer to "how much am I about to lose" before
    clear_range() actually removes anything. Both bounds optional/either
    order works: after alone means "everything from here on", before
    alone means "everything up to here", both means a closed window -
    same convention as every other optional-bound pair in this app
    (None means unlimited on that side)."""
    where, params = _range_where(before, after)
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM signals {where}", params).fetchone()[0]


def clear_range(before: float | None = None, after: float | None = None) -> int:
    """Deletes only signals in (after, before] instead of the whole table -
    2026-08-16 direct request: a noisy tuning/dev stretch should be
    purgeable without losing the valid history on either side of it. No
    bounds at all (both None) is equivalent to clear_all() but still
    returns a real deleted-row count, which clear_all() doesn't - callers
    that need a count for the reset audit log (services/reset/reset_log.py)
    should call this even for a full wipe."""
    where, params = _range_where(before, after)
    with _connect() as conn:
        cur = conn.execute(f"DELETE FROM signals {where}", params)
        return cur.rowcount


def _range_where(before: float | None, after: float | None) -> tuple[str, list]:
    clauses, params = [], []
    if after is not None:
        clauses.append("seen_at > ?")
        params.append(after)
    if before is not None:
        clauses.append("seen_at <= ?")
        params.append(before)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def recent(limit: int = 50, offset: int = 0, resolved_only: bool = False) -> list[dict]:
    """Individual signals, newest first - the browsable signal history
    (ROADMAP.md Phase 0.5). WhaleScanr's framing, copied directly: "every
    flag and how it settled, misses included" - not just the rolled-up
    win-rate percentage `stats()` already provides. `correct` is None for
    anything not yet resolved (still in flight), not conflated with a
    resolved-and-wrong 0."""
    cols = ["id", "ticker", "series", "side", "size", "confidence", "source", "seen_at", "price", "resolved", "correct", "resolved_at"]
    where = "WHERE resolved = 1 " if resolved_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM signals {where}ORDER BY seen_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def total_count(resolved_only: bool = False) -> int:
    where = "WHERE resolved = 1" if resolved_only else ""
    with _connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM signals {where}").fetchone()[0]


def resolved_with_factors_count() -> int:
    """Same filter as resolved_signals_with_factors() below, but a COUNT(*)
    instead of fetching and JSON-parsing every matching row - added
    2026-08-26 for GET /api/confidence-calibration/status (services/
    whale_calibration/routes.py), which only ever needed the count. Proven
    live via a py-spy stack trace (ROADMAP.md's event-loop-stall entry)
    that this route was fetching+parsing every resolved-with-factors row
    on every dashboard poll just to call len() on the result."""
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM signals WHERE resolved = 1 AND excluded = 0 AND factors_json IS NOT NULL",
        ).fetchone()[0]


def resolved_signals_with_factors(since_ts: float | None = None) -> list[dict]:
    """services/whale_calibration/confidence_calibration.py's entire input: resolved signals
    that carry a real per-factor confidence breakdown. factors_json IS NOT
    NULL is the filter, not a source string match - only real providers
    (services/whalewatchers/kalshi_trade_tape.py) ever populate it, so this
    naturally excludes every simulator-sourced row without needing a second,
    possibly-drifting definition of "real" to maintain. No limit scoping,
    and date scoping is optional (see since_ts below) rather than default -
    the calibration gate cares about total resolved count, not recency.
    An unscoped scan is NOT cheap at real production volume - measured at
    ~1s against 103k+ rows (2026-09-01, whale-confidence-scoring-remediation
    final review) - a caller on a tight polling budget (e.g. a route hit
    every few seconds) should pass since_ts to bound it; the calibration
    gate itself stays unscoped by design since it cares about total
    resolved count, not recency. `series` is already a stored, indexed
    column (issue #60 - it existed but was never selected here, so every
    consumer of this function was blind to it).

    since_ts=None keeps today's full-history behavior (design §4: the
    calibration gate cares about total resolved count, not recency) - a caller
    wanting a recency-scoped view (e.g. a future report asking "does the gap
    look different in the last 30 days") passes it explicitly. ORDER BY
    seen_at ASC makes today's de facto row order (SQLite rowid order) an
    explicit, stated property instead of an accident a future VACUUM/migration
    could silently reorder history out from under."""
    # `series` corrected into this SELECT during the 2026-08-31 catch-up review: a
    # same-day but unrelated commit (5bb29be, "expose the series dimension") already
    # added it to the real current query before this task's own commit ever landed -
    # dropping it here would silently regress a column services/whale_calibration/
    # README.md:97-101 documents as feeding the by_series report field.
    query, params = _resolved_with_factors_query(since_ts)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return materialize_signals_with_factors(rows)


def _resolved_with_factors_query(since_ts: float | None) -> tuple[str, tuple]:
    """The SELECT shared by resolved_signals_with_factors() and its async
    sibling (issue #410), so the two can never drift into fetching
    different columns or a different filter."""
    query = (
        "SELECT confidence, correct, factors_json, raw_notional_usd, raw_spread, raw_volume_24h, series "
        "FROM signals WHERE resolved = 1 AND excluded = 0 AND factors_json IS NOT NULL"
    )
    params: tuple = ()
    if since_ts is not None:
        query += " AND seen_at >= ?"
        params = (since_ts,)
    query += " ORDER BY seen_at ASC"
    return query, params


def materialize_signals_with_factors(rows) -> list[dict]:
    """Turns raw result rows into this function family's dict shape. Public
    (no underscore) because it crosses a module boundary: the async path
    below hands it to asyncio.to_thread, and it is the half that must NOT
    run on the event loop.

    This is pure-Python CPU, and it is the DOMINANT cost of what
    issue #410's design calls the "~2s fetch" - measured at implementation
    time against the live 295,807-row table:

        SQL fetch only .............. 0.884s  (24.7%)
        json.loads + dict build ..... 2.691s  (75.3%)

    That split is why converting only the SQL to aiosqlite would be a
    REGRESSION rather than a fix for this route: it moves 0.9s off a worker
    thread and leaves 2.7s of GIL-holding work on the event loop. The same
    trap services/diagnostics/_aio_db.py's own docstring records for
    run_offline() ("pure-Python aggregation used to run on a worker thread
    and now runs on the event loop"). Takes plain sqlite3 tuples or
    aiosqlite.Row objects - both are sequences, so the unpacking is
    identical."""
    results = []
    for confidence, correct, factors_json, raw_notional_usd, raw_spread, raw_volume_24h, series in rows:
        try:
            factors = json.loads(factors_json)
        except (TypeError, ValueError):
            continue  # malformed row - skip rather than crash the whole report
        results.append({
            "confidence": confidence, "correct": bool(correct), "factors": factors,
            # Gap 8 (docs/config-tuning-data-gaps-2026-08-10.md) - null for
            # every signal logged before this column existed; a future
            # analysis over these needs to filter for non-null the same way
            # confidence_calibration.py already excludes rows missing a
            # given factors_json key.
            "raw_notional_usd": raw_notional_usd, "raw_spread": raw_spread, "raw_volume_24h": raw_volume_24h,
            "series": series,
        })
    return results


async def resolved_signals_with_factors_async(since_ts: float | None = None) -> list[dict]:
    """Async sibling of resolved_signals_with_factors() above, for callers
    already on the event loop (services/whale_calibration/routes.py). Same
    query, same output, same contract. Issue #410, implementing
    docs/archive/lane-5-runtime-infrastructure/specs/2026-09-04-issue-410-pool-vs-aiosqlite-design.md (moved there 2026-09-06, planning-lanes migration).

    BOTH halves are kept off the event loop, and that is the whole point:
    the SQL goes through aiosqlite (which yields natively rather than
    occupying a worker for the query's duration), and the per-row
    json.loads goes through asyncio.to_thread. See
    materialize_signals_with_factors' own docstring for the measurement
    that makes the second half non-optional - it is 75% of the cost the
    design's Sec 1 table folds into the word "fetch".

    asyncio.to_thread runs on the loop's DEFAULT executor, shared
    process-wide. Confirmed safe here at implementation time rather than
    assumed (the design's Sec 3 leaves it as an explicit open question and
    its Sec 6 names it as a falsifier): the executor's ceiling on this
    container is 20 workers (cpu_count 16, min(32, n+4)), against
    tick_executor's 2, and no SUSTAINED trading hot-path work contends for
    it - the WS trade path uses services/whalewatchers/_scoring_pool.py (4
    dedicated workers) and candidate retry uses _candidate_retry_pool.py,
    both of which chose a private pool over this executor for exactly this
    reason. Its other users are diagnostics/quality/research/backup route
    work and loop_watchdog's fire-and-forget fault write, plus CPython's own
    brief, incidental use of it for DNS resolution (getaddrinfo) during WS
    reconnects - corrected 2026-09-05: an earlier draft claimed no trading
    hot-path work uses this executor at all, which is not quite true, though
    the conclusion (no realistic saturation risk) still holds at 20 workers
    against at most two serial jobs added per calibration miss.

    A schema_init IS passed, unlike this same DB_PATH's three OTHER existing
    _aio_db consumers - services/diagnostics/diagnostics.py (twice) and
    services/series_watcher.py's per-series read (corrected 2026-09-05:
    an earlier draft of this docstring said "two", counting only
    diagnostics.py and missing series_watcher.py's consumer of this file).
    The design's rule is "pass nothing when the target DB file's schema is
    already guaranteed to exist by its own write-path module" - and both of
    those are exactly that case, readers of somebody else's file. This
    module is not: signal_log IS signal_log.db's owning write-path module,
    and its own _connect() re-runs _init_schema() on every call, so every
    existing read here self-heals a missing table rather than raising.
    Dropping that on the async path was not theoretical - it surfaced
    immediately as `sqlite3.OperationalError: no such table: signals`
    against a fresh DB, i.e. "no data yet" turning into a 500.

    Serialization tradeoff (F7, stated explicitly per the data-plane HARD
    RULE rather than left implicit): _aio_db caches ONE connection per
    (loop, db_path) and serializes every operation on it onto that
    connection's single worker thread. This function is now a fourth
    concurrent consumer of signal_log.DB_PATH's cached aiosqlite connection,
    alongside the three named above - a cache miss here now queues behind
    whichever of the other three is mid-read, rather than running in
    parallel the way tick_executor's 2 workers allowed. The direction of
    this tradeoff is deliberate and correct (protect the shared trade-path
    pool; degrade dashboard/diagnostics latency under overlap instead), not
    an oversight - but it is a real, measured-as-a-property-not-a-number
    change in this file's own concurrency behavior, not a free lunch.

    Caveat, stated rather than left as a trap: _aio_db caches per (event
    loop, db_path) and only runs schema_init on a key's FIRST open, so if
    diagnostics opens signal_log.db first this hook never runs. Passing it
    is therefore strictly better than not passing it, never a guarantee."""
    conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
    query, params = _resolved_with_factors_query(since_ts)
    rows = await conn.execute_fetchall(query, params)
    return await asyncio.to_thread(materialize_signals_with_factors, rows)


def resolved_signals_for_edge_calibration(since_ts: float | None = None) -> list[dict]:
    """Δ_calibrated's entire input population (services/whale_calibration/
    confidence_calibration.py's _bucket_delta_by_category_price_band) -
    every resolved, non-excluded signal, NOT filtered to factors_json IS
    NOT NULL like resolved_signals_with_factors above, because
    Δ_calibrated only needs price/seen_at/correct/series, not a per-factor
    breakdown - restricting to the factors-populated subset would silently
    under-cover the data-plane HARD RULE's completeness requirement for no
    reason this function's own job needs. Same since_ts-bounding contract
    as its sibling - always call with a bounded since_ts in production
    (resolved_signals_with_factors' own docstring measured ~1s/103k+ rows
    unscoped); this function keeps the unscoped default for parity, not
    because an unscoped call here is cheap."""
    query = (
        "SELECT ticker, side, price, seen_at, correct, factors_json, series "
        "FROM signals WHERE resolved = 1 AND excluded = 0"
    )
    params: tuple = ()
    if since_ts is not None:
        query += " AND seen_at >= ?"
        params = (since_ts,)
    query += " ORDER BY seen_at ASC"
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {"ticker": t, "side": s, "price": p, "seen_at": ts, "correct": c, "factors_json": fj, "series": sr}
        for t, s, p, ts, c, fj, sr in rows
    ]


def _size_ratio_ok(a: float, b: float, max_ratio: float) -> bool:
    lo, hi = min(a, b), max(a, b)
    return lo > 0 and (hi / lo) <= max_ratio


def _summarize_cluster(signals: list[dict]) -> dict:
    total_size = sum(s["size"] for s in signals)
    avg_confidence = sum(s["confidence"] for s in signals) / len(signals)
    span_sec = signals[-1]["seen_at"] - signals[0]["seen_at"]
    # More prints, tighter timing = more likely one actor accumulating, not
    # coincidence - capped well under 1.0 since this is inference on
    # anonymous data, never a claim of verified identity.
    cluster_confidence = 0.3 + 0.15 * (len(signals) - 1) - min(span_sec / 3600 * 0.1, 0.3)
    cluster_confidence = round(min(max(cluster_confidence, 0.1), 0.95), 2)
    return {
        "ticker": signals[0]["ticker"],
        "side": signals[0]["side"],
        "print_count": len(signals),
        "total_size": total_size,
        "avg_confidence": round(avg_confidence, 2),
        "cluster_confidence": cluster_confidence,
        "span_sec": round(span_sec),
        "first_seen": signals[0]["seen_at"],
        "last_seen": signals[-1]["seen_at"],
    }


def find_clusters(hours: int = 24, time_window_min: int = 30, max_size_ratio: float = 4.0) -> list[dict]:
    """Groups recent signals into probable-same-actor "clusters" using
    statistical/behavioral similarity, WhaleScanr's real approach
    (researched directly, see ROADMAP.md) to a genuine constraint this app
    already respects: Kalshi's real trade tape is anonymous, no usernames
    or account data exists, confirmed directly on their site. So this never
    claims verified identity - `cluster_confidence` is capped at 0.95 and
    is inference on top of already-good data, nothing more.

    A cluster is a same-ticker, same-side run of signals where each one is
    within time_window_min of the previous one AND within max_size_ratio of
    it (so a lone 500-contract print doesn't get lumped in with an
    unrelated 50,000-contract one just because they share a ticker/side).
    Single, non-clustered signals aren't returned - a "cluster" of one
    print isn't accumulation, it's just a print."""
    since = time.time() - hours * 3600
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ticker, side, size, confidence, seen_at FROM signals "
            "WHERE seen_at >= ? ORDER BY ticker, side, seen_at",
            (since,),
        ).fetchall()
    cols = ["id", "ticker", "side", "size", "confidence", "seen_at"]
    signals = [dict(zip(cols, r)) for r in rows]

    clusters = []
    current: list[dict] = []
    for s in signals:
        if current and (
            s["ticker"] == current[-1]["ticker"]
            and s["side"] == current[-1]["side"]
            and (s["seen_at"] - current[-1]["seen_at"]) <= time_window_min * 60
            and _size_ratio_ok(s["size"], current[-1]["size"], max_size_ratio)
        ):
            current.append(s)
        else:
            if len(current) > 1:
                clusters.append(_summarize_cluster(current))
            current = [s]
    if len(current) > 1:
        clusters.append(_summarize_cluster(current))

    clusters.sort(key=lambda c: c["total_size"], reverse=True)
    return clusters


def stats(days: int = 30) -> dict:
    since = time.time() - days * 86400
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM signals WHERE seen_at >= ?", (since,)).fetchone()[0]
        resolved_count, correct_sum = conn.execute(
            "SELECT COUNT(*), SUM(correct) FROM signals WHERE seen_at >= ? AND resolved = 1 AND excluded = 0", (since,)
        ).fetchone()
        first_seen = conn.execute("SELECT MIN(seen_at) FROM signals").fetchone()[0]
    resolved_count = resolved_count or 0
    correct_count = correct_sum or 0
    return {
        "window_days": days,
        "total_signals": total,
        "resolved": resolved_count,
        "correct": correct_count,
        "win_rate": round(correct_count / resolved_count * 100, 1) if resolved_count else None,
        "tracking_since": first_seen,
    }
