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

SAMPLING (min_contracts only, added 2026-09-05, issue #532)

"No dedup key at all" above stopped being literally true for one gate:
min_contracts alone was 98.98% of 29.8M rows (23,841,626 of them
resolved, against a min_samples=30 statistical-precision gate -
23,841,626 / 30 =~ 794,721x oversampled for that purpose) and unbounded,
so record_rejection() now
writes a Bernoulli sample of min_contracts rejections to rejection_events
instead of every one - see _MIN_CONTRACTS_SAMPLE_RATE below. Every other
gate is untouched: still one row per call, weight always 1.0. rejected_
candidates (the deduped table) is never sampled either, for any gate - it
was never the growth problem (one row per (ticker, strategy, gate_name),
not per rejection) and every existing consumer of gate_summary() depends
on seeing every distinct candidate.

A sampled row's sample_weight column (1/sample_rate) makes _POPULATION_
GATE_SQL's rejected_count/resolved_count unbiased population-size
ESTIMATES again (SUM(sample_weight) instead of COUNT(*) - a uniform
random sample's constant weight is exactly what Horvitz-Thompson
weighting cancels back out to the true total). sided_total/sided_wins/
unit_cost_total/unit_cost_n stay plain unweighted counts/sums on purpose:
a ratio or mean over a uniform sample is unbiased without any weighting
(the constant weight cancels in both numerator and denominator), and the
min_samples gate specifically must reflect the real number of observed
samples, not a scaled-up estimate - weighting it would make the gate LESS
protective for the one gate that most needs it. See _summarize_population_
rows()'s own docstring for where this split is implemented.
"""
import asyncio
import contextlib
import random
import sqlite3
import time
from pathlib import Path

from services import capture_writer, db, fault_log
from services.diagnostics import _aio_db

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "candidate_log.db"

db.register_schema("rejected_candidates", capture_writer.init_rejected_candidates)
db.register_schema("rejection_events", capture_writer.init_rejection_events)

# Issue #532: min_contracts alone was 98.98% of rejection_events (29.5M of
# 29.8M rows measured 2026-09-05) and unbounded - every other gate combined
# was under 300k rows over the same ~13-day window. 1/100 brings a future
# day's min_contracts contribution down near that same order of magnitude
# (measured ~2.27M rows/day -> ~22.7k/day) while leaving its resolved
# sample count (which gates hypothetical_win_rate's min_samples=30 check,
# see _summarize_population_rows) in the tens of thousands per day - see
# this module's own "SAMPLING" docstring section above for why min_samples
# itself is never scaled by this rate. A round number, not derived from a
# formula: no single "right" rate exists, this one just moves the gate from
# wildly-oversampled-and-growing to roughly in line with the rest of the
# table's growth rate.
_MIN_CONTRACTS_SAMPLE_RATE = 0.01


# Shared by _connect() and _ensure_schema_aio() below (issue #410) rather
# than being written out twice - same reason services/series_watcher.py
# extracted its own _IDX_*_SQL constants when it grew a second, async
# schema path: two hand-kept-in-sync copies of an index definition is one
# more thing that can silently drift, and an index that exists on only one
# of the two paths is a 15-22s query plan difference, not a cosmetic one.
_IDX_REJECTION_EVENTS_GATE_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_rejection_events_gate ON rejection_events (strategy, gate_name)"
)
_IDX_REJECTION_EVENTS_UNRESOLVED_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_rejection_events_unresolved ON rejection_events (ticker) "
    "WHERE resolved = 0"
)
# Covers prune_gate()'s DELETE (issue #532) - adversarial review of that
# PR measured a genuine full-table SCAN for its `gate_name = ? AND
# rejected_at < ?` filter against a synthetic table shaped like
# production. Safe to defer for the one gate this is actually designed to
# purge (min_contracts is ~99% of the table, so a chronological scan
# still finds batch-size matches quickly - same reasoning
# market_history.prune()'s own unindexed age filter already relies on,
# confirmed empirically), but the function takes an arbitrary gate_name
# with no guard - calling it against any of the other, much sparser gates
# at real multi-million-row scale measured ~2x slower per row and a scan
# of the ENTIRE pre-cutoff range to confirm a small match count. Adding
# the index removes the whole risk category rather than just guarding one
# caller's intended use.
_IDX_REJECTION_EVENTS_GATE_REJECTED_AT_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_rejection_events_gate_rejected_at "
    "ON rejection_events (gate_name, rejected_at)"
)


@contextlib.contextmanager
def _connect():
    """Every existing `with _connect() as conn:` call site keeps working
    unchanged - now backed by services/db.py's closing connect(). The two
    CREATE INDEX statements and two add_column_if_missing calls aren't
    expressible in a single table's registered init_fn (they span both
    tables / aren't CREATE TABLE at all), so this wrapper still runs them
    itself on the yielded connection."""
    with db.connect(DB_PATH, tables=("rejected_candidates", "rejection_events")) as conn:
        conn.execute(_IDX_REJECTION_EVENTS_GATE_SQL)
        conn.execute(_IDX_REJECTION_EVENTS_UNRESOLVED_SQL)
        conn.execute(_IDX_REJECTION_EVENTS_GATE_REJECTED_AT_SQL)
        db.add_column_if_missing(conn, "rejected_candidates", "unit_cost", "REAL")
        db.add_column_if_missing(conn, "rejection_events", "unit_cost", "REAL")
        db.add_column_if_missing(
            conn, "rejection_events", "sample_weight", "REAL NOT NULL DEFAULT 1.0",
        )
        yield conn


# One definition, executed by both population_gate_summary() and its async
# sibling - issue #410. Kept as a module constant rather than inlined twice
# so the two paths cannot drift into answering the same question with
# different SQL.
#
# rejected_count/resolved_count sum sample_weight rather than COUNT(*)
# (issue #532) so a sampled gate's row still reports an unbiased estimate
# of its true population size - every unsampled row's weight is 1.0, so
# this is identical to COUNT(*)/a plain conditional count for every gate
# except min_contracts. sided_total/sided_wins/unit_cost_total/unit_cost_n
# deliberately stay plain unweighted counts/sums - see this module's own
# "SAMPLING" docstring section and _summarize_population_rows()'s
# docstring for why weighting those would be a bug, not a refinement.
_POPULATION_GATE_SQL = """
    SELECT strategy, gate_name,
           SUM(sample_weight) AS rejected_count,
           SUM(CASE WHEN resolved THEN sample_weight ELSE 0 END) AS resolved_count,
           SUM(CASE WHEN resolved AND side IN ('yes', 'no') THEN 1 ELSE 0 END) AS sided_total,
           SUM(CASE WHEN resolved AND side IN ('yes', 'no') AND result = side
               THEN 1 ELSE 0 END) AS sided_wins,
           SUM(unit_cost) AS unit_cost_total,
           COUNT(unit_cost) AS unit_cost_n
    FROM rejection_events
    GROUP BY strategy, gate_name
"""


# population_gate_summary_banded()'s bands - issue #616 D1 (docs/superpowers/
# specs/2026-08-26-economic-strategy-remediation-design.md). Reproduces
# docs/superpowers/research/2026-08-26-economic-gate-marginal-contribution.md's
# own E4 results table exactly ([0,.2) [.2,.4) [.4,.6) [.6,.8) [.8,.95)
# [.95,1.01) - e.g. that table's whale_watcher.min_contracts/0.00-0.20 row is
# n=601,757, win rate 9.9%, mean_unit_cost 0.074, EV/contract +0.0252) -
# VERIFIED, not copied blind from that document's own framing. That research
# doc's prose calls these "the same six bands CLAUDE.md's HARD COMMANDMENT
# table uses" - checked directly against `git log -S'HARD COMMANDMENT' --
# CLAUDE.md` and against candidate_log.py's/record_rejection's own existing
# docstrings (both cite the real table's numbers) rather than trusted, and
# that attribution is WRONG: CLAUDE.md's HARD COMMANDMENT table (introduced
# commit 3193843; retired alongside the 70%/70% target in 78e5aaf, purged
# from the file entirely in 385623c - grep the CURRENT CLAUDE.md for "HARD
# COMMANDMENT" and it returns nothing) used a coarser FOUR-band scheme with
# different boundaries: 0.50-0.65 / 0.65-0.80 / 0.80-0.95 / >=0.95 - no band
# at all below 0.50, and none of these six 0.2-wide boundaries. The six bands
# below are still the right ones for THIS function (they are what E4 actually
# measured, and issue #616's decision record's own "-0.073 to -0.049 per
# contract in the 0.60-0.95 band" citation is exactly this scheme's 0.60-0.80
# and 0.80-0.95 bands) - only the "same as HARD COMMANDMENT" attribution in
# the research doc is a misattribution, not the bands themselves.
DEFAULT_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.01),
)


def _band_case_sql(bands) -> tuple[str, list]:
    """Builds a parameterized SQL CASE expression that buckets `unit_cost`
    into the caller's half-open [low, high) bands, first-matching-WHEN-wins -
    the same semantics as a sequential if/elif, so `bands` must be sorted
    ascending and non-overlapping (validated below rather than trusted: a
    mis-ordered list would silently misclassify every row past the first
    inversion instead of erroring).

    A unit_cost that clears every band (or is negative - nothing upstream
    enforces unit_cost's [0,1] range at write time) falls into an explicit
    'out_of_range' bucket via ELSE, rather than SQLite's default CASE
    behaviour of NULL for "no WHEN matched" - a NULL group would otherwise
    silently drop those rows from a naive read of this function's output
    instead of surfacing them (CLAUDE.md's data-plane HARD RULE: "a dropped
    message, skipped candidate, or DB hole is a defect").

    Returns (case_sql, params) - params is a flat list of bound values in
    the exact order their `?` placeholders appear in case_sql, meant to
    prefix the query's own parameter list (this expression is the first
    thing SQLite binds placeholders for, since it sits in the SELECT list
    before the WHERE clause)."""
    prev_high = None
    parts: list[str] = []
    params: list = []
    for low, high in bands:
        if not (low < high):
            raise ValueError(f"invalid band, low must be < high: ({low}, {high})")
        if prev_high is not None and low < prev_high:
            raise ValueError(f"bands must be sorted ascending and non-overlapping: {bands}")
        prev_high = high
        parts.append("WHEN unit_cost >= ? AND unit_cost < ? THEN ?")
        params.extend([low, high, f"{low:.2f}-{high:.2f}"])
    case_sql = "CASE " + " ".join(parts) + " ELSE 'out_of_range' END"
    return case_sql, params


def _population_gate_banded_query(bands) -> tuple[str, list]:
    """Builds the GROUP BY query for population_gate_summary_banded() and its
    async sibling. Built per call rather than a module constant like
    _POPULATION_GATE_SQL, because `bands` is a caller-supplied parameter, not
    a fixed shape - DEFAULT_BANDS above is what every real caller passes
    today.

    Same SQL-side-aggregation discipline as _POPULATION_GATE_SQL, and for
    the same reason: issue #616's decision record explicitly warns this
    function "must not add another unindexed scan of rejected_candidates/
    rejection_events" - the exact shape issue #601/#617 just fixed
    elsewhere in this file. Grouping by (strategy, gate_name, unit_cost_band)
    still starts from the same (strategy, gate_name) prefix
    idx_rejection_events_gate already indexes, so this adds no new
    index-shape risk over the existing unbanded query.

    win/n are plain unweighted COUNT(*)/SUM(*) - not SUM(sample_weight) -
    deliberately: this module's own "SAMPLING" docstring section already
    establishes that a ratio (here, wins/n) over a uniform sample is
    unbiased without weighting, and that the statistical-precision gate
    (here, n itself) must reflect the REAL observed sample count, never a
    scaled-up population estimate, or it becomes LESS protective for
    exactly the gate (min_contracts) that most needs it."""
    case_sql, band_params = _band_case_sql(bands)
    sql = f"""
        SELECT strategy, gate_name, {case_sql} AS unit_cost_band,
               COUNT(*) AS n,
               SUM(CASE WHEN result = side THEN 1 ELSE 0 END) AS wins,
               SUM(unit_cost) AS unit_cost_total
        FROM rejection_events
        WHERE unit_cost IS NOT NULL AND resolved = 1 AND side IN ('yes', 'no')
        GROUP BY strategy, gate_name, unit_cost_band
    """
    return sql, band_params


def _summarize_population_rows_banded(rows, min_samples: int, band_bounds: dict) -> list[dict]:
    """The pure-Python half of population_gate_summary_banded(), shared
    verbatim by the sync and async paths - same reason _summarize_population_
    rows() above is shared: the two transports can never drift into
    reporting different numbers from the same rows.

    n is the single sample-size denominator for this function (unlike
    _summarize_population_rows() above, which tracks rejected_count/
    resolved_count/sided_total separately) because _population_gate_banded_
    query()'s WHERE clause already restricts to resolved+sided+known-
    unit_cost rows before GROUP BY ever runs - there is no broader
    "rejected but not yet resolved" count left to report at this
    granularity. mean_unit_cost is reported whenever n > 0 regardless of the
    min_samples gate (mirroring avg_unit_cost's own always-shown behaviour
    in gate_summary()/population_gate_summary() above) - only win_rate and
    ev_per_contract, the two quantities whose noise genuinely misleads at
    low n, go to None below min_samples.

    DIMENSIONAL ANALYSIS (CLAUDE.md's HARD RULE, 2026-08-31): a Kalshi binary
    contract pays exactly $1 if it resolves your side, $0 otherwise, so
    expected value per contract in dollars is P(win) x $1 - unit_cost x $1 -
    both terms unitless fractions of $1 (the same "breakeven accuracy IS the
    entry price" identity the now-retired HARD COMMANDMENT table stated -
    see DEFAULT_BANDS' own comment above for why that table isn't otherwise
    authoritative for this function's bands). CRITICAL: this MUST use the
    raw 0-1 fraction (wins/n), never the `win_rate` OUTPUT field below, which
    is deliberately rescaled to 0-100 to match population_gate_summary()'s
    own hypothetical_win_rate convention (percent). Subtracting a 0-1
    mean_unit_cost from a 0-100 win_rate would silently be off by ~100x -
    exactly the class of scale-confusion bug CLAUDE.md's "A displayed value
    must match its label" section already names two real precedents for
    (the no-side 1-price inversion, `equity - starting_bankroll` mislabeled
    as unrealized P&L)."""
    out = []
    for strategy, gate_name, unit_cost_band, n, wins, unit_cost_total in rows:
        bounds = band_bounds.get(unit_cost_band)
        g = {
            "strategy": strategy, "gate_name": gate_name,
            "unit_cost_band": unit_cost_band,
            "band_low": bounds[0] if bounds else None,
            "band_high": bounds[1] if bounds else None,
            "n": n,
            "min_samples": min_samples,
        }
        mean_unit_cost = (unit_cost_total / n) if n > 0 else None
        g["mean_unit_cost"] = round(mean_unit_cost, 3) if mean_unit_cost is not None else None
        if n < min_samples:
            g["status"] = "insufficient"
            g["win_rate"] = None
            g["ev_per_contract"] = None
        else:
            g["status"] = "ready"
            win_rate_frac = wins / n
            g["win_rate"] = round(100 * win_rate_frac, 1)
            # win_rate_frac (raw, 0-1) - mean_unit_cost (raw, 0-1), NOT
            # g["win_rate"] (0-100) - see this function's own docstring.
            g["ev_per_contract"] = round(win_rate_frac - mean_unit_cost, 4)
        out.append(g)
    out.sort(key=lambda g: (
        g["strategy"], g["gate_name"],
        g["band_low"] if g["band_low"] is not None else float("inf"),
    ))
    return out


def population_gate_summary_banded(bands=DEFAULT_BANDS, min_samples: int = 30) -> list[dict]:
    """Banded extension of population_gate_summary() above - issue #616 D1.
    Answers "what would a gate's rejected candidates have done, broken out
    by how expensive they were" instead of one hypothetical_win_rate
    averaged across every unit_cost a gate ever rejected, which hides the
    real 0.60-0.95-band negative-EV pattern docs/superpowers/research/
    2026-08-26-economic-gate-marginal-contribution.md's E4 analysis found
    underneath that aggregate (see e.g. this repo's own ROADMAP.md/CLAUDE.md
    "0.60-0.95 unit-cost band negative-EV" open-gaps line).

    Grouped by (strategy, gate_name, unit_cost_band) - the population is
    filtered to resolved, sided, unit_cost-known rows only (see
    _population_gate_banded_query()'s own docstring for why that's SQL-side,
    not a Python-side scan), so n IS the win_rate/ev_per_contract sample
    count directly, unlike the unbanded function's separate resolved_count/
    sided_total split.

    Same "insufficient" sample-size-gating convention as
    population_gate_summary() above: a (gate, band) with n below min_samples
    reports status "insufficient" and win_rate/ev_per_contract as None
    rather than a number computed from too few samples.

    Read-only diagnostic - explicitly NOT used here to retune entry_threshold/
    min_contracts/etc (issue #616 item 1's own stated non-scope); nothing in
    this function enables trading or weakens a gate.

    Flushes capture_writer's rejection_events buffer first, same reasoning
    and same bound (_FLUSH_BATCH) as population_gate_summary()'s own
    identical flush above."""
    capture_writer.flush_now("rejection_events")
    sql, params = _population_gate_banded_query(bands)
    band_bounds = {f"{low:.2f}-{high:.2f}": (low, high) for low, high in bands}
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return _summarize_population_rows_banded(rows, min_samples, band_bounds)


async def population_gate_summary_banded_async(bands=DEFAULT_BANDS, min_samples: int = 30) -> list[dict]:
    """Async sibling of population_gate_summary_banded() above, for callers
    already on the event loop (services/analytics/routes.py's GET
    /api/candidate-log/summary) - same reason population_gate_summary_async()
    exists for the unbanded function (issue #410): this query is entirely
    SQL-bound (same GROUP BY shape, same index prefix, as the unbanded
    query, just with one more grouping column), so a tick_executor worker
    thread would have nothing to usefully own, and the sync version above
    must not be deleted for the same reason population_gate_summary()'s own
    docstring gives (services/research/research.py's run_and_store() reaches
    a sync function from a context with no running event loop).

    capture_writer.flush_now() goes through asyncio.to_thread rather than
    running inline - same reasoning as population_gate_summary_async()'s own
    identical call: it is a blocking SQLite WRITE, never safe to run
    directly on the event loop even though it is usually a bounded no-op."""
    await asyncio.to_thread(capture_writer.flush_now, "rejection_events")
    sql, params = _population_gate_banded_query(bands)
    band_bounds = {f"{low:.2f}-{high:.2f}": (low, high) for low, high in bands}
    conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
    rows = await conn.execute_fetchall(sql, params)
    return _summarize_population_rows_banded(rows, min_samples, band_bounds)


# population_gate_summary_banded_cached_async() - issue #616 D1's own
# decision record ("must not add another unindexed scan") is satisfied by
# the query itself (see _population_gate_banded_query()'s docstring), but
# an UNCACHED call is still too expensive to pay on every poll of GET
# /api/candidate-log/summary: EXPLAIN QUERY PLAN against the real, live
# candidate_log.db (verified fresh in this task, 2026-09-06 03:xx UTC /
# 2026-09-05 22:xx local - both dates are the same instant, the container
# runs UTC while the host runs CDT, confirmed via `date` in both places
# after an earlier version of this comment's "2026-09-06" looked like a
# future date relative to the host clock and needed checking rather than
# trusted) confirms _population_gate_banded_query() still drives the exact
# same `SCAN rejection_events USING INDEX idx_rejection_events_gate` the
# unbanded _POPULATION_GATE_SQL uses - no new unindexed scan - and adds one
# `USE TEMP B-TREE FOR GROUP BY` for the extra unit_cost_band grouping
# column, which is the real mechanism for its extra cost, not a full
# rescan. The absolute wall-clock figures behind the ~2x below (34.96s
# unbanded vs 66.8-71.7s banded, against a then-31.8M-row live copy taken
# via bench/copy_dbs.py's backup-API snapshot) are INHERITED from the
# session that authored this module's WIP commit earlier the same day
# (git log -1 --format=%cI on that commit vs this comment's own edit both
# land 2026-09-05/06 in the same few-hour window) - re-verified here only
# at the query-plan/mechanism level (cheap, deterministic, independent of
# row count), not re-executed at the full ~70s cost against the live,
# still-growing table a second time in this same session (an extra ~70s+
# read against the shared live DB has a real, non-zero resource cost on a
# host also running the live trading loop - see this repo's own nice/
# ionice-guarded bench/copy_dbs.py for why that number is treated as
# something to spend deliberately, not casually). The ~2x order of
# magnitude is independently plausible from the query-plan diff alone (an
# extra per-row CASE evaluation plus a second GROUP BY dimension needing
# its own temp B-tree is a real, mechanistic cost add, not a coincidence),
# and is treated as an assumption inherited from the cited commit, not
# reproduced at full scale by this comment's author - flagged explicitly
# per CLAUDE.md's "never guess; verify or falsify" HARD RULE rather than
# presented as freshly measured.
#
# 300s (10x the unbanded field's 30s _POPULATION_GATES_CACHE_TTL_SEC in
# services/analytics/routes.py, deliberately NOT the same value or the same
# cache - see that constant's own comment for why sharing would mean almost
# every poll pays the ~70s cost anyway) keeps this diagnostic reasonably
# fresh for what is, like the unbanded field, a total-sample gate rather
# than a recency-scoped read (nothing here is timelier than what it
# extends), while keeping the ~70s worst case rare rather than routine.
# Not a retune of _POPULATION_GATES_CACHE_TTL_SEC itself - that field's own
# query and TTL are untouched by this change (CLAUDE.md's data-plane HARD
# RULE: don't retune an existing dial "because it should help" without its
# own measured bottleneck; this is a new dial for a new, separately-costed
# query).
#
# Lives here, not in services/analytics/routes.py (where the unbanded
# field's own cache lives), for two reasons checked directly rather than
# assumed: (1) services/diagnostics/diagnostics.py's check_gate_cost_bands
# below needs the SAME cached value so a GET /api/quality/summary call
# never pays this query's cost a second time within the same 300s window a
# GET /api/candidate-log/summary poll already paid it (or vice versa) - one
# cache, two callers; (2) services.diagnostics.diagnostics cannot import
# services.analytics.routes to reach a cache kept there without a real
# import cycle (checked via grep, not assumed): services/app_state.py
# already does `from services.diagnostics import diagnostics`, and
# services/analytics/routes.py already does `from services.app_state
# import broker, bump_generation` - diagnostics.diagnostics ->
# analytics.routes -> app_state -> diagnostics.diagnostics. candidate_log.py
# has no such path back to diagnostics.diagnostics (it only reaches
# services.diagnostics._aio_db, a leaf module with no import of
# diagnostics.diagnostics or candidate_log itself - checked directly).
#
# Stamped at completion, not at request receipt (issue #410's own lesson,
# docs/superpowers/research/2026-09-04-issue-410-tick-executor-measurement.md
# Sec 3.4, reapplied here rather than re-learned): a ~70s worst-case query
# stamped with the instant it was REQUESTED would burn up to ~23% of this
# cache's own 300s TTL before the entry was even written.
_POPULATION_GATES_BANDED_CACHE_TTL_SEC = 300
_population_gates_banded_cache: dict = {"cached_at": None, "value": None}


async def population_gate_summary_banded_cached_async(
    bands=DEFAULT_BANDS, min_samples: int = 30,
) -> list[dict]:
    """Cached wrapper around population_gate_summary_banded_async() above -
    see _POPULATION_GATES_BANDED_CACHE_TTL_SEC's own comment for why this
    cache lives here (shared by services/analytics/routes.py's route field
    and services/diagnostics/diagnostics.py's check_gate_cost_bands) and why
    its TTL is 300s, independent of population_gates' own 30s cache in
    routes.py.

    Same "ignore bands/min_samples in the cache key" shape as the existing
    _population_gates_cache in routes.py (checked directly, not assumed) -
    every real caller today passes DEFAULT_BANDS/min_samples=30, so this
    is parity with an already-shipped, already-tested limitation rather
    than a new one; a future caller that genuinely needs a different bands/
    min_samples value bypasses this wrapper and calls
    population_gate_summary_banded_async() directly, same escape hatch the
    unbanded pair already offers."""
    now = time.time()
    if (
        _population_gates_banded_cache["cached_at"] is not None
        and (now - _population_gates_banded_cache["cached_at"]) < _POPULATION_GATES_BANDED_CACHE_TTL_SEC
    ):
        return _population_gates_banded_cache["value"]
    value = await population_gate_summary_banded_async(bands, min_samples)
    _population_gates_banded_cache["cached_at"] = time.time()
    _population_gates_banded_cache["value"] = value
    return value


async def _ensure_schema_aio(conn) -> None:
    """The async mirror of _connect()'s own DDL, passed to
    _aio_db.connection_for() as its schema_init hook. Same shape, same
    shared SQL constants, and for the same reason as
    services/series_watcher.py's function of this name - a plain SQL string
    executes identically through `conn.execute(sql)` (sync) and
    `await conn.execute(sql)` (aiosqlite); only the caller's execute differs.

    Not optional and not defensive boilerplate: _connect() re-runs every one
    of these statements on EVERY call, so this module's read paths have
    always been self-healing - a missing table, or a rejection_events file
    that does not exist yet, repairs itself on the next read.
    _aio_db.connection_for()'s own docstring names this exact case ("pass one
    when the caller previously relied on a plain sqlite3.connect()-adjacent
    helper that also ran CREATE TABLE IF NOT EXISTS on every call"), because
    dropping it turns "no data yet" into a hard error.

    Narrower than the sync path in one way (corrected 2026-09-05, an
    earlier draft overstated this): _connect() self-heals a missing
    PARENT DIRECTORY too, via services/db.py's own
    `db_path.parent.mkdir(parents=True, exist_ok=True)`; this hook does
    not, because it runs on a raw `aiosqlite.connect()` (services/
    diagnostics/_aio_db.py) with no equivalent mkdir. Harmless in practice
    only because `data/` already exists by the time this path is ever
    reached (this process's own sync writers create it first) - not a
    guarantee for a hypothetically fresh checkout with no `data/` dir at
    all.

    Runs ONCE per (loop, db_path) on first open rather than per call - that
    is _aio_db's contract, and it is the one real behavioural difference
    from the sync path. Harmless here: the only writer to this file is this
    same process, so a table cannot disappear underneath a cached
    connection."""
    await conn.execute(capture_writer.REJECTED_CANDIDATES_DDL_SQL)
    await conn.execute(capture_writer.REJECTION_EVENTS_DDL_SQL)
    await conn.execute(_IDX_REJECTION_EVENTS_GATE_SQL)
    await conn.execute(_IDX_REJECTION_EVENTS_UNRESOLVED_SQL)
    await conn.execute(_IDX_REJECTION_EVENTS_GATE_REJECTED_AT_SQL)
    await _add_column_if_missing_aio(conn, "rejected_candidates", "unit_cost", "REAL")
    await _add_column_if_missing_aio(conn, "rejection_events", "unit_cost", "REAL")
    await _add_column_if_missing_aio(
        conn, "rejection_events", "sample_weight", "REAL NOT NULL DEFAULT 1.0",
    )
    await conn.commit()


async def _add_column_if_missing_aio(conn, table: str, column: str, coltype: str) -> None:
    """Async mirror of services/db.py's add_column_if_missing(), which takes
    a synchronous sqlite3.Connection and so cannot be reused here. Same two
    statements in the same order (PRAGMA table_info, then a guarded ALTER)
    against the same tables, so the two paths converge on identical
    schema."""
    cols = {row[1] for row in await conn.execute_fetchall(f"PRAGMA table_info({table})")}
    if column not in cols:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


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
    asynchronous now.

    min_contracts rejections are Bernoulli-sampled into rejection_events at
    _MIN_CONTRACTS_SAMPLE_RATE (issue #532) rather than written whole - see
    this module's own "SAMPLING" docstring section for why. Every other
    gate, and rejected_candidates for every gate including min_contracts,
    is unaffected: this function's caller still gets exactly the same
    dedup-table behavior it always has, on every gate."""
    now = now if now is not None else time.time()
    capture_writer.submit(
        "rejected_candidates",
        (ticker, strategy, gate_name, observed_value, threshold_value, side, now, unit_cost),
    )
    sample_weight = 1.0
    if gate_name == "min_contracts":
        if random.random() >= _MIN_CONTRACTS_SAMPLE_RATE:
            return
        sample_weight = 1.0 / _MIN_CONTRACTS_SAMPLE_RATE
    capture_writer.submit(
        "rejection_events",
        (
            None, ticker, strategy, gate_name, observed_value, threshold_value, side, now,
            0, None, None, unit_cost, sample_weight,
        ),
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
    either buffer doesn't exist in its table yet for this UPDATE to find,
    and would otherwise stay permanently unresolved once a settled market
    drops out of a later tick's market_results - not just delayed,
    genuinely lost data, unlike the same tradeoff elsewhere in this plan
    where a late flush only delays visibility. Usually a near no-op in
    practice: capture_writer's own background thread already flushes
    within ~1s on its own cadence, well inside typical poll_interval_sec
    tick spacing - this only does real work in the rare case a rejection
    landed in the last <1s before this tick's resolve call.

    TICKER-SCOPED DIRECT UPDATE (issue #601 / PR #603 Family 2, 2026-09-05):
    this used to run `SELECT rowid, ticker FROM rejected_candidates WHERE
    resolved = 0` with no ticker predicate at all - a `SCAN
    rejected_candidates` (EXPLAIN QUERY PLAN, confirmed live) over every
    unresolved row (231k+ measured live) on every single call, regardless
    of which ticker(s) actually settled this tick. Paid twice over: once
    per due ticker from settlement_resolver.py's sequential surge-drain
    loop (~260ms/call there), and unconditionally every poll_interval_sec
    (6s, config/settings.yaml) from main.py's
    _resolve_and_record_settlements - a continuous steady-state tax on
    tick_executor's shared 2-worker pool, the same pool whale_stream/
    decision_bridge.py's whale-signal decision path uses, not merely a
    rare settlement-surge problem.

    Fixed by dropping the SELECT+Python-filter round trip entirely and
    running a direct `UPDATE ... WHERE ticker = ? AND resolved = 0` per
    ticker in market_results, batched via executemany exactly like
    rejection_events' own UPDATE below already did. No new index needed:
    the table's own existing composite PRIMARY KEY (ticker, strategy,
    gate_name) already makes `ticker = ?` a fast index SEARCH (confirmed
    live via EXPLAIN QUERY PLAN: `SEARCH rejected_candidates USING INDEX
    sqlite_autoindex_rejected_candidates_1 (ticker=?)`) - PR #603's
    benchmark measured 13.87s -> 0.943s per batch at a realistic N=50
    (14.7x), re-confirmed independently in this fix's own review (live
    read-only EXPLAIN QUERY PLAN + a ~1100x read-proxy timing gap on the
    same live table). `to_resolve` below is exactly the same
    (result, now, ticker) shape the old code already built for
    rejection_events' UPDATE - the two tables' resolution predicates were
    always identical, so one list now drives both.

    The return value uses cursor.rowcount from the rejected_candidates
    executemany rather than a Python-counted list length - independently
    verified (this fix's own tests, not assumed from the sqlite3 docs)
    that executemany()'s rowcount correctly sums across every per-tuple
    UPDATE, including 0-row (absent ticker / already-resolved) and
    multi-row (multi-gate-per-ticker) cases. max(..., 0) guards against a
    driver ever reporting the -1 "not supported" sentinel leaking into a
    value this module's callers treat as a plain count.

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
    in - the actual mechanism, not a busy_timeout/retry tune.

    Call-site shapes are unchanged by this fix (issue #601's own
    investigation confirmed both, re-verified again here): settlement_
    resolver.py's per-ticker surge loop still calls this once per due
    ticker with a single-entry dict (its own try/except around
    tick_executor.run() is the per-ticker failure-isolation mechanism,
    entirely outside this function and untouched by it); main.py:487
    still calls this once per tick with the whole tick's market_results
    dict, which this fix's single executemany() naturally batches across
    every ticker in that dict in one call - already "Family 3-shaped" by
    construction there, so no separate batching change was needed at
    that call site either."""
    capture_writer.flush_now("rejected_candidates")
    capture_writer.flush_now("rejection_events")
    now = time.time()
    to_resolve = []
    for ticker, result in market_results.items():
        result = (result or "").strip().lower()
        if result not in ("yes", "no"):
            continue
        to_resolve.append((result, now, ticker))

    resolved_count = 0
    with _connect() as conn:
        if to_resolve:
            cur = conn.executemany(
                "UPDATE rejected_candidates SET resolved = 1, result = ?, resolved_at = ? "
                "WHERE ticker = ? AND resolved = 0",
                to_resolve,
            )
            resolved_count = max(cur.rowcount, 0)
            conn.executemany(
                "UPDATE rejection_events SET resolved = 1, result = ?, resolved_at = ? "
                "WHERE ticker = ? AND resolved = 0",
                to_resolve,
            )
    return resolved_count


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
    I/O-bound again. That last clause used to read "so the tick_executor
    offload at this function's own call site is now doing real work" -
    corrected by issue #410, which took the reasoning one step further:
    once the remaining cost is genuinely I/O-bound, a thread offload is the
    WRONG tool for it too, and services/analytics/routes.py now calls
    population_gate_summary_async() below instead. Re-measured 2026-09-04
    against 28.7M rows: 18.2-22.5s of SQL, 0.000s of Python.

    Flushes capture_writer's rejection_events buffer first (P3 Task 16,
    2026-08-27) - record_rejection() no longer writes this table
    synchronously, so without this a caller could read an undercount for
    up to ~1s after the most recent rejection. Cheap: bounded by
    _FLUSH_BATCH, a no-op when nothing is buffered."""
    capture_writer.flush_now("rejection_events")
    with _connect() as conn:
        rows = conn.execute(_POPULATION_GATE_SQL).fetchall()
    return _summarize_population_rows(rows, min_samples)


async def population_gate_summary_async(min_samples: int = 30) -> list[dict]:
    """Async sibling of population_gate_summary() above, for callers that
    are already on the event loop (services/analytics/routes.py's GET
    /api/candidate-log/summary). Same query, same output, same contract -
    only the transport differs. Issue #410, implementing
    docs/superpowers/specs/2026-09-04-issue-410-pool-vs-aiosqlite-design.md.

    Why aiosqlite rather than the dedicated pool that design rejected: this
    function is ENTIRELY SQL-bound, so there is nothing here for a worker
    thread to usefully own. Re-measured at implementation time against the
    live 28.7M-row table (design Sec 6 asks for exactly this
    re-confirmation, and the number it was written against had already
    moved): the GROUP BY costs 18.2-22.5s warm and the Python that shapes
    its output costs 0.000s, because the query returns 11 grouped rows, not
    28.7M. Sending it through tick_executor bought nothing except a
    15-22s occupancy of one of that pool's 2 workers - workers shared with
    candidate_ledger.claim()/record_decision() on the live per-signal
    decision path.

    The sync version above is NOT deprecated by this one and must not be
    deleted: services/research/research.py's run_and_store() reaches it
    from a plain synchronous function (itself already offloaded wholesale
    via asyncio.to_thread), where awaiting anything is not available.

    capture_writer.flush_now() goes through asyncio.to_thread rather than
    running inline: it is a blocking SQLite WRITE (bounded by _FLUSH_BATCH,
    usually trivial, but a write on a contended file is exactly the thing
    that is never safe to do on the event loop). The sync version pays it
    on whatever thread already owns it."""
    await asyncio.to_thread(capture_writer.flush_now, "rejection_events")
    conn = await _aio_db.connection_for(DB_PATH, schema_init=_ensure_schema_aio)
    rows = await conn.execute_fetchall(_POPULATION_GATE_SQL)
    return _summarize_population_rows(rows, min_samples)


def _summarize_population_rows(rows, min_samples: int) -> list[dict]:
    """The pure-Python half of population_gate_summary(), shared verbatim by
    the sync and async paths so the two can never drift into reporting
    different numbers from the same rows.

    Takes plain sqlite3 tuples OR aiosqlite.Row objects - both are
    sequences, so the unpacking below is identical for either. Costs
    0.000s in practice (measured, implementation-time): the SQL already
    reduced 28.7M rows to 11 groups before anything reaches here.

    rejected_count/resolved_count arrive as SUM(sample_weight) (issue
    #532), a float even when every row is unsampled (weight 1.0) - rounded
    back to int here since both are still logically population COUNTS
    (a "count" that silently became a float is exactly the kind of
    quiet dimension-shift CLAUDE.md's dimensional-analysis rule flags).
    sided_total/sided_wins/unit_cost_total/unit_cost_n are untouched: they
    were never weighted (see _POPULATION_GATE_SQL's own comment) and stay
    whatever type SQLite's COUNT/SUM already gives them."""
    out = []
    for strategy, gate_name, rejected_count, resolved_count, sided_total, sided_wins, \
            unit_cost_total, unit_cost_n in rows:
        g = {
            "strategy": strategy, "gate_name": gate_name,
            "rejected_count": round(rejected_count), "resolved_count": round(resolved_count),
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


_PRUNE_BATCH = 50_000


def prune_gate(gate_name: str, retention_hours: float, now: float | None = None,
                *, batch_size: int = _PRUNE_BATCH) -> dict:
    """One-off/manual backlog purge for a single gate's rejection_events
    rows (issue #532) - NOT wired into any periodic sweep. #532's fix
    (record_rejection() sampling min_contracts at write time) caps FUTURE
    growth; it does nothing about the ~29.5M rows already written before
    that fix shipped. This function is how that backlog gets cleared,
    once, under explicit human go-ahead - same shape as services/
    market_history.py's prune() (mirrored deliberately: retention_hours/
    now/batch_size signature, cutoff = now - retention_hours*3600,
    DELETE ... WHERE id IN (SELECT id ... LIMIT ?) so a multi-million-row
    backlog drains in bounded batches rather than one long-held write
    transaction), with one addition market_history.prune() doesn't need:
    a gate_name filter, since only ONE gate's backlog is being purged
    here, not the whole table - see this module's own "SAMPLING" docstring
    section for why a blanket table-wide purge was rejected (it would
    delete the ~1% of volume carrying the actual counterfactual signal
    for the OTHER 10 gates, which were never the growth problem and have
    no oversampling to correct).

    Never wired into _maybe_prune_capture_stores (main.py) the way
    market_history.prune()/fault_log.prune() are - giving rejection_events
    an ongoing retention policy across every gate is a separate, broader
    decision (this table still has none, deliberately, for the ~1% of
    rows that carry real signal) that #532 never asked for and this
    function does not decide.

    LIMIT without ORDER BY (same reasoning as market_history.prune()):
    rows are appended in roughly chronological rowid order via
    capture_writer's batched flush, and the target gate is ~99% of the
    table's volume, so the oldest cutoff-violating rows cluster at the
    start of any scan - this drains a large backlog in bounded per-call
    work rather than needing an index or a full sort.

    Flushes rejection_events' capture_writer buffer first, same reasoning
    as clear_range/clear_all - a row still sitting in the buffer isn't in
    the table yet for this DELETE to find."""
    now = now if now is not None else time.time()
    cutoff = now - retention_hours * 3600
    try:
        capture_writer.flush_now("rejection_events")
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM rejection_events WHERE id IN "
                "(SELECT id FROM rejection_events WHERE gate_name = ? AND rejected_at < ? LIMIT ?)",
                (gate_name, cutoff, batch_size),
            )
            return {"rejection_events_deleted": cur.rowcount, "cutoff": cutoff}
    except Exception as exc:
        fault_log.record("candidate_log", "prune_gate", exc)
        return {"rejection_events_deleted": 0, "error": str(exc)}


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
