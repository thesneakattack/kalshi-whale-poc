"""
Rejected-candidate logging - closes Gap 1 of docs/config-tuning-data-gaps-
2026-08-10.md (the "counterfactual gap"). Every entry/discovery gate in this
app (strategy.entry_threshold, strategy.min_whale_winrate_pct,
whale_watcher_kalshi.min_contracts) only ever produces a boolean "did
this candidate pass" - nothing previously recorded what happened to a
candidate that failed. advisory_engine's own entry-threshold/longshot
recommendations return None against real trade history specifically
because there's no data on candidates that scored just below the bar,
only ones that cleared it - this module is what that data would come
from.

This does NOT act on rejected candidates (no trade is ever placed from
this module) - it only observes. Dedup key is (ticker, strategy,
gate_name): a candidate that keeps failing the same gate on repeated
evaluation updates its one row in place rather than growing a new row per
tick - the meaningful data point is "what did this gate's most recent
observed value look like, and how did the market eventually resolve," not
a full tick-by-tick history of a value that mostly drifts slowly. Once a
ticker resolves, the strategy already skips it before reaching any gate
(the market_results check runs first in evaluate()),
so a resolved row is never overwritten by a later rejection - no extra
guard needed for that race.

Same persistence idiom as every other module in services/ (own SQLite
file, CREATE TABLE IF NOT EXISTS, resolved via the already-fetched
market_results dict each tick - see market_analyst_agent.
resolve_from_market_results for the precedent this mirrors, zero new API
calls needed).

POPULATION STATISTICS (rejection_events, added 2026-08-23)

ROADMAP.md named the gap directly: rejected_candidates' own dedup key
means a ticker rejected fifty times by the same gate over its life counts
as ONE data point, not fifty - "unusable for population statistics." Fixed
additively, not by changing the existing table's behavior (every current
consumer of gate_summary()/rejected_candidates keeps working unchanged):
record_rejection() now also submits one row per call into a second table,
rejection_events, with no dedup key at all - the true population.
population_gate_summary() answers the same "what would a gate's rejected
candidates have done" question gate_summary() does, from that real
population, with the same honest "insufficient" gating
services/settlement_edge.py's edge_report() uses rather than reporting a
number earned from too few samples.

rejection_events writes route through services/capture_writer.py (P3 Task
16, 2026-08-27) instead of a second synchronous connect()+INSERT per call
- the plan's own original design asked to aggregate this table by
(ticker, side, minute) instead, which was rejected: that would have
destroyed the exact per-row unit_cost/observed_value detail this table
exists to preserve (see the "cost-blindness gap" naming above and
CLAUDE.md's Standing goal section, which still calls per-unit-cost-band
analysis an open research target). Batching preserves every row, only
delaying when it lands on disk - population_gate_summary()/clear_all()/
count_range()/clear_range() each flush the buffer first so no caller ever
sees a stale undercount or an incomplete wipe.
"""
import sqlite3
import time
from pathlib import Path

from services import capture_writer

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_log.db"


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, coltype: str):
    # Same idiom as services/risk_manager.py/paper_broker.py - CREATE TABLE
    # IF NOT EXISTS alone doesn't add a column to an existing table with
    # existing rows.
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    # WAL mode (2026-08-11, real live incident): rollback-journal mode
    # serializes ALL writers and readers against each other for the whole
    # transaction; WAL lets readers proceed concurrently with a writer and
    # is the standard hardening step for exactly the bursty-write scenario
    # that took the app down (trade-tape volume overwhelming a per-call
    # sqlite3.connect()). idempotent - safe to run on every connect.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(capture_writer.REJECTED_CANDIDATES_DDL_SQL)
    # No PRIMARY KEY / dedup on (ticker, strategy, gate_name) - deliberately
    # the opposite of rejected_candidates above, so this is the true
    # population every individual rejection, not one row per key. See this
    # module's own "POPULATION STATISTICS" docstring section.
    conn.execute(capture_writer.REJECTION_EVENTS_DDL_SQL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rejection_events_gate ON rejection_events (strategy, gate_name)"
    )
    # Resolution below is driven by ticker, once per market_results entry -
    # this index is what keeps that an indexed UPDATE instead of a full
    # table scan once the table grows past a trivial size.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rejection_events_unresolved ON rejection_events (ticker) "
        "WHERE resolved = 0"
    )
    # 2026-08-23: closes the cost-blindness gap population_gate_summary()'s
    # own docstring flags - the rejected candidate's unit_cost (0-1, YES-
    # side-adjusted per side, same convention as PaperBroker's own trades)
    # at the moment it was rejected, so a future pass can bucket hypothetical
    # win rate by unit_cost band instead of averaging across all of them (the
    # same trap CLAUDE.md's HARD COMMANDMENT table already proved: the
    # >=0.95 band wins 96.3% of the time and *loses* money, forever).
    # Nullable - not every rejection can supply this (e.g. unparseable_price
    # itself, by definition).
    _add_column_if_missing(conn, "rejected_candidates", "unit_cost", "REAL")
    _add_column_if_missing(conn, "rejection_events", "unit_cost", "REAL")
    return conn


def record_rejection(
    ticker: str, strategy: str, gate_name: str,
    observed_value: float | None, threshold_value: float | None,
    side: str | None = None, now: float | None = None, unit_cost: float | None = None,
) -> None:
    """side, when known, is the direction a trade would have taken had this
    gate not rejected the candidate (e.g. the whale print's own side) - not
    every gate can supply this, and that's fine: gate_summary() only computes a
    hypothetical win rate for rows where side is present, and reports the
    plain yes/no resolution split otherwise.

    unit_cost, when known (2026-08-23), is the real per-contract cost
    (0-1, side-adjusted - see every call site's own "signal.price is always
    the YES price" comment) at rejection time - closes the cost-blindness
    gap population_gate_summary()'s own docstring names: without this, a
    high hypothetical win rate can't be told apart from a near-certainty-
    band gate that would have won often while losing money on every
    settlement (CLAUDE.md's HARD COMMANDMENT table). Not every gate can
    supply this either (a gate that rejects before price is even parsed
    genuinely has none) - None here means "unknown," not 0.

    Fully non-blocking now (P3 Task 17, 2026-08-27) - both writes route
    through capture_writer, zero synchronous DB I/O anywhere in this
    function. Started as just rejection_events (Task 16); extended to
    rejected_candidates too once Task 17's reader-gate call site made it
    clear this function needed to be safe to call directly from the WS
    reader hot path, not just from strategy_engine.py/kalshi_trade_tape.py's
    already-less-latency-critical call sites. capture_writer's own
    "upsert" store mode (_STORE_KEY/_STORE_UPSERT_SQL) now runs the exact
    UPSERT statement that used to execute here synchronously - see
    capture_writer.py's own module docstring for the two store-mode
    shapes. Every reader of either table (gate_summary,
    population_gate_summary, clear_all, count_range, clear_range,
    resolve_from_market_results) flushes both stores before it
    reads/deletes, so no caller has to know either table's writes are
    asynchronous now."""
    now = now if now is not None else time.time()
    capture_writer.submit(
        "rejected_candidates",
        (ticker, strategy, gate_name, observed_value, threshold_value, side, now, unit_cost),
    )
    capture_writer.submit(
        "rejection_events",
        (None, ticker, strategy, gate_name, observed_value, threshold_value, side, now, 0, None, None, unit_cost),
    )


def resolve_from_market_results(market_results: dict) -> int:
    """Same shape as market_analyst_agent.resolve_from_market_results -
    market_results is the {ticker: "yes"/"no"/""/None} mapping main.py's
    trading loop already builds from that tick's fetched markets, so this
    costs zero new API calls. Returns how many rejected_candidates rows
    were resolved this call - unchanged contract, existing callers already
    use this count (e.g. whale_stream_handlers.py's
    outcomes_resolved_via_lifecycle stat).

    Also resolves rejection_events (the population table) as a side
    effect, not counted in the return value - by ticker rather than by
    row, since a busy ticker can carry far more event rows than the
    deduped table ever would; one indexed UPDATE per resolved ticker
    resolves all of that ticker's pending rows at once.

    Flushes capture_writer's rejected_candidates and rejection_events
    buffers first (P3 Task 16/17, 2026-08-27): a row still sitting in
    either buffer doesn't exist in its table yet for this SELECT/UPDATE
    to find, and would otherwise stay permanently unresolved once a
    settled market drops out of a later tick's market_results - not just
    delayed, genuinely lost data, unlike the same tradeoff elsewhere in
    this plan where a late flush only delays visibility. Usually a near
    no-op in practice: capture_writer's own background thread already
    flushes within ~1s on its own cadence, well inside typical
    poll_interval_sec tick spacing - this only does real work in the rare
    case a rejection landed in the last <1s before this tick's resolve
    call.

    Both UPDATE passes below batch via executemany rather than issuing one
    execute() per resolved row (2026-09-01 fix, capture_writer-adjacent
    lock contention): this function runs once per tick against
    candidate_log.db, the same file capture_writer's own daemon thread
    flushes rejected_candidates/rejection_events into on its own ~1s
    cadence - a Python loop of individual UPDATEs held the write
    transaction open for the whole loop, and capture_writer's daemon only
    waits 1s before giving up, so it lost that race with real, measured
    frequency (91 'rejected_candidates: N row(s) retained on lock' faults
    in one recent window, /api/health/faults, 2026-09-01). Batching keeps
    the write phase to at most two statements regardless of how many rows
    resolve in a given tick, shrinking the window a collision can happen
    in - the actual mechanism, not a busy_timeout/retry tune."""
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    now = time.time()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT rowid, ticker FROM rejected_candidates WHERE resolved = 0",
        ).fetchall()
        to_resolve = []
        for rowid, ticker in rows:
            result = (market_results.get(ticker) or "").strip().lower()
            if result not in ("yes", "no"):
                continue
            to_resolve.append((result, now, rowid))
        if to_resolve:
            conn.executemany(
                "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? WHERE rowid = ?",
                to_resolve,
            )

        events_to_resolve = []
        for ticker, result in market_results.items():
            result = (result or "").strip().lower()
            if result not in ("yes", "no"):
                continue
            events_to_resolve.append((result, now, ticker))
        if events_to_resolve:
            conn.executemany(
                "UPDATE rejection_events SET resolved = 1, result = ?, resolved_at = ? "
                "WHERE ticker = ? AND resolved = 0",
                events_to_resolve,
            )
        return len(to_resolve)


def gate_summary() -> list[dict]:
    """One row per (strategy, gate_name) - the direct answer to "what would
    have happened to the candidates this gate rejected." hypothetical_win_rate
    is only populated when at least one resolved row for this gate carries a
    known side (see record_rejection's docstring) - None otherwise, not 0,
    since a missing side means "can't be computed," not "0% win rate."

    avg_unit_cost (2026-08-23), when known, is the mean unit_cost across
    this gate's rows that captured one - see population_gate_summary's own
    docstring for why this matters (a high win rate at a near-certainty
    unit_cost tells a different story than the same win rate at 0.5-0.8).

    Flushes capture_writer's rejected_candidates buffer first (P3 Task 17,
    2026-08-27) - record_rejection() no longer writes this table
    synchronously, so without this a caller could read a stale/incomplete
    view for up to ~1s after the most recent rejection."""
    capture_writer.flush_now("rejected_candidates")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT strategy, gate_name, side, result, resolved, unit_cost FROM rejected_candidates",
        ).fetchall()
    grouped: dict[tuple, dict] = {}
    for strategy, gate_name, side, result, resolved, unit_cost in rows:
        key = (strategy, gate_name)
        g = grouped.setdefault(key, {
            "strategy": strategy, "gate_name": gate_name,
            "rejected_count": 0, "resolved_count": 0,
            "yes_count": 0, "no_count": 0,
            "_sided_total": 0, "_sided_wins": 0,
            "_unit_cost_total": 0.0, "_unit_cost_n": 0,
        })
        g["rejected_count"] += 1
        if unit_cost is not None:
            g["_unit_cost_total"] += unit_cost
            g["_unit_cost_n"] += 1
        if resolved:
            g["resolved_count"] += 1
            if result == "yes":
                g["yes_count"] += 1
            elif result == "no":
                g["no_count"] += 1
            if side in ("yes", "no"):
                g["_sided_total"] += 1
                if result == side:
                    g["_sided_wins"] += 1
    out = []
    for g in grouped.values():
        sided_total = g.pop("_sided_total")
        sided_wins = g.pop("_sided_wins")
        unit_cost_n = g.pop("_unit_cost_n")
        unit_cost_total = g.pop("_unit_cost_total")
        g["hypothetical_win_rate"] = round(100 * sided_wins / sided_total, 1) if sided_total > 0 else None
        g["hypothetical_win_rate_n"] = sided_total
        g["avg_unit_cost"] = round(unit_cost_total / unit_cost_n, 3) if unit_cost_n > 0 else None
        g["avg_unit_cost_n"] = unit_cost_n
        out.append(g)
    out.sort(key=lambda g: (-g["resolved_count"], g["strategy"], g["gate_name"]))
    return out


def population_gate_summary(min_samples: int = 30) -> list[dict]:
    """The same question gate_summary() answers - what would a gate's
    rejected candidates have done - from rejection_events, the true
    population (see this module's "POPULATION STATISTICS" docstring
    section), instead of one deduped row per (ticker, strategy, gate_name).

    Reports "insufficient" per gate rather than a hypothetical_win_rate
    computed from too few samples - same honesty convention
    services/settlement_edge.py's edge_report() uses. min_samples counts
    SIDED resolved events (a rejection whose side is known and whose
    market has settled), the same denominator gate_summary's
    hypothetical_win_rate_n already uses, not raw rejected_count.

    PARTIALLY COST-BLIND, same root gap gate_summary() has - services/
    advisory/README.md's own audit finding names the general trap:
    comparing win rate alone, with no cost_basis/realized_pnl term, can't
    tell "this bucket wins more because the signal is better" from "this
    bucket wins more because it's mechanically priced into the near-
    certainty band" (CLAUDE.md's HARD COMMANDMENT table: the >=0.95
    unit-cost band wins 96.3% of the time and *loses* money, forever).
    record_rejection() now captures unit_cost where the calling gate can
    supply it (2026-08-23 - previously it captured nothing but
    observed_value, which means something different per gate: confidence
    for entry_threshold, contract count for min_contracts, spread for
    max_spread) - avg_unit_cost/avg_unit_cost_n below are that data,
    exposed. This is still only a per-gate MEAN, not a banded cost-aware
    hypothetical_win_rate - the column is brand new as of this date, so
    there isn't yet enough accumulated history to bucket by unit_cost band
    and sample-size-gate each band the way _confidence_calibration_bands
    (services/whale_calibration/confidence_calibration.py) does for
    confidence; that's the real next step once this has had time to
    accumulate, same pattern this table's own rejection_events history
    followed. A high hypothetical_win_rate here is a real, useful
    counterfactual signal but not by itself proof a gate should be
    loosened - see ROADMAP.md.

    Aggregates via SQL GROUP BY, not a per-row Python loop (2026-08-26 fix
    - see ROADMAP.md's "event loop stalls for 17-38+ seconds" entry). The
    prior version fetched every one of rejection_events' 6.2M+ rows
    (undeduped, no retention - see this function's own docstring above)
    into Python before grouping; even off the event loop via
    tick_executor, that stayed slow enough to matter (measured live:
    18.2s total, 15.3s of it just constructing 6.2M row tuples) because
    building millions of Python objects holds the GIL regardless of which
    OS thread runs it - a thread offload only helps genuinely I/O-bound
    work, not this. Doing the grouping in SQL instead (measured: 4.8s for
    the same 6.2M rows, returning only ~10 grouped rows) cuts the
    Python-object cost to near zero and makes the remaining time actually
    I/O-bound again, so the tick_executor offload at this function's own
    call site (services/analytics/routes.py) is now doing real work.

    Flushes capture_writer's rejection_events buffer first (P3 Task 16,
    2026-08-27) - record_rejection() no longer writes this table
    synchronously, so without this a caller could read an undercount for
    up to ~1s after the most recent rejection. Cheap: bounded by
    _FLUSH_BATCH, a no-op when nothing is buffered."""
    capture_writer.flush_now("rejection_events")
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT strategy, gate_name,
                   COUNT(*) AS rejected_count,
                   SUM(CASE WHEN resolved THEN 1 ELSE 0 END) AS resolved_count,
                   SUM(CASE WHEN resolved AND side IN ('yes', 'no') THEN 1 ELSE 0 END) AS sided_total,
                   SUM(CASE WHEN resolved AND side IN ('yes', 'no') AND result = side
                       THEN 1 ELSE 0 END) AS sided_wins,
                   SUM(unit_cost) AS unit_cost_total,
                   COUNT(unit_cost) AS unit_cost_n
            FROM rejection_events
            GROUP BY strategy, gate_name
            """,
        ).fetchall()
    out = []
    for strategy, gate_name, rejected_count, resolved_count, sided_total, sided_wins, \
            unit_cost_total, unit_cost_n in rows:
        g = {
            "strategy": strategy, "gate_name": gate_name,
            "rejected_count": rejected_count, "resolved_count": resolved_count,
        }
        if sided_total == 0 or sided_total < min_samples:
            g["status"] = "insufficient"
            g["hypothetical_win_rate"] = None
        else:
            g["status"] = "ready"
            g["hypothetical_win_rate"] = round(100 * sided_wins / sided_total, 1)
        g["hypothetical_win_rate_n"] = sided_total
        g["min_samples"] = min_samples
        g["avg_unit_cost"] = round(unit_cost_total / unit_cost_n, 3) if unit_cost_n > 0 else None
        g["avg_unit_cost_n"] = unit_cost_n
        out.append(g)
    out.sort(key=lambda g: (-g["rejected_count"], g["strategy"], g["gate_name"]))
    return out


def clear_all() -> None:
    """Danger-zone reset support, same convention as market_catalog.
    clear_all()/market_history.clear_all() - drops accumulated rows, not
    the table itself. Clears rejection_events too - it's the same logical
    dataset (see this module's "POPULATION STATISTICS" docstring section),
    and leaving it behind would silently defeat a user's "wipe candidate
    log" intent for anything reading the population table instead of the
    deduped one.

    Flushes capture_writer's rejected_candidates and rejection_events
    buffers first (P3 Task 16/17, 2026-08-27) - a row sitting in either
    buffer isn't in its table yet for DELETE to find, and would otherwise
    land moments after a "wipe" silently un-wipes it."""
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    with _connect() as conn:
        conn.execute("DELETE FROM rejected_candidates")
        conn.execute("DELETE FROM rejection_events")


def count_range(before: float | None = None, after: float | None = None) -> int:
    """Danger Zone preview support (2026-08-16) - mirrors signal_log.
    count_range's (after, before] convention exactly. Note rejected_
    candidates is an upsert-per-(ticker,strategy,gate_name) table (only
    the most recent rejection survives per key, see record_rejection's own
    docstring) - a range here scopes by that latest rejected_at, not a
    full rejection history, same caveat that applies to clear_range.
    Sums in rejection_events (the population table, uncapped by that same
    dedup) so this preview matches what clear_range would actually delete,
    not just the deduped table's half of it.

    Flushes capture_writer's rejected_candidates and rejection_events
    buffers first (P3 Task 16/17, 2026-08-27) so a just-submitted-but-not-
    yet-flushed row isn't undercounted in this preview."""
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    where, params = _range_where(before, after)
    with _connect() as conn:
        deduped = conn.execute(f"SELECT COUNT(*) FROM rejected_candidates {where}", params).fetchone()[0]
        population = conn.execute(f"SELECT COUNT(*) FROM rejection_events {where}", params).fetchone()[0]
        return deduped + population


def clear_range(before: float | None = None, after: float | None = None) -> int:
    """Deletes only rows whose rejected_at falls in (after, before],
    instead of the whole table - same "purge a noisy stretch without
    losing what's on either side of it" reasoning as signal_log.
    clear_range. Deletes from rejection_events too, same reasoning as
    clear_all - returns the combined row count so this matches what
    count_range previews.

    Flushes capture_writer's rejected_candidates and rejection_events
    buffers first (P3 Task 16/17, 2026-08-27), same reasoning as
    clear_all - a buffered row isn't in its table yet for DELETE to find."""
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    where, params = _range_where(before, after)
    with _connect() as conn:
        cur = conn.execute(f"DELETE FROM rejected_candidates {where}", params)
        cur2 = conn.execute(f"DELETE FROM rejection_events {where}", params)
        return cur.rowcount + cur2.rowcount


def _range_where(before: float | None, after: float | None) -> tuple[str, list]:
    clauses, params = [], []
    if after is not None:
        clauses.append("rejected_at > ?")
        params.append(after)
    if before is not None:
        clauses.append("rejected_at <= ?")
        params.append(before)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params
