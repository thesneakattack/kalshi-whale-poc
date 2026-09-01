# Isolating Write-Critical Work From `tick_executor`'s Shared Pool — Design (Revision 3)

Revision 3. Revision 2 was written against the required fix list in
`2026-09-01-whale-scoring-connection-reuse-design-review.md` (NO-GO on revision 1);
every factual citation in revision 1 held up under independent adversarial
re-verification, and the causal analysis gap that revision found (section 4's core
mechanism, section 1a's `score_recovered_trade` scope) is addressed and carried
forward unchanged into this revision.

**Revision 3 broadens scope on direct instruction, after a second, independently
confirmed root cause landed the same day.** A peer session (`autotrade-dc`) found and
verified live — the app genuinely hung on `/api/quality/summary` (confirmed
independently in this revision too, `curl` timeout, `HTTP_STATUS:000`) — that
`services/diagnostics/diagnostics.py`'s `run_offline()`, invoked by that endpoint on
every dashboard poll (every 5s), was pinning **both** of `tick_executor`'s 2 workers
continuously for 5h10m+ (confirmed via `/proc`: `futex_do_wait`/GIL contention,
~82% CPU each), starving the *actual* trading-critical writes
(`_flush_trade_capture_async`, `_resolve_and_record_settlements_async`,
`_build_series_track_record_async`) that share that same pool — directly explaining
the ongoing `capture_writer` "database is locked" faults independent of the whale-
scoring mechanism this spec originally targeted. Both are now understood as two
instances of one architectural gap, not two coincidentally-related bugs: **nothing
non-critical should share `tick_executor`'s pool with the trading-critical writes it
exists to protect** — this spec's original whale-scoring fix already isolates one
non-critical consumer (scoring reads) onto its own pool; this revision adds the
second (diagnostics) under the same principle, in the same spec, per direct
instruction to revise rather than run two disconnected efforts.

An immediate stopgap was applied before this revision was written: the `fastapi`
container was restarted (clears stuck threads, does not fix the underlying cost) —
confirmed the app recovered from a full hang to "slow but responding"
(`quality/summary`: 19.2s, still far too slow) — proving the restart bought time
without touching the actual cause, consistent with every other stopgap applied
today.

## 1. Problem, grounded in source and live telemetry

`docs/next-action.md`'s top item (2026-09-01): `capture_writer`/`candidate_log`'s
write path was believed unable to sustain full legitimate trade volume, proven live
when a peer session's PR #397 dropped `KXBTC15M`'s effective `min_contracts` threshold
3000→173 (a ~17x cut on this app's highest-frequency series). Result: tick duration
6.63s→241.63s peak, `kalshi_websocket` repeated `consumer_stalled_forced_reconnect`,
the ingest queue reached 17,451/20,000 capacity, signal generation stopped for ~6
minutes. Reverted as an emergency stopgap; three named candidate mechanisms were left
for follow-up investigation (capture_writer's flush cadence, candidate_log's per-print
work, `kalshi_trade_tape.py`'s prescan/resolve pipeline cost).

This spec root-causes the mechanism precisely, not by picking among those three but
by tracing the actual call graph and correlating it against live telemetry:

- `services/whalewatchers/kalshi_trade_tape.py`'s `_process_trades_sync()` — called
  once per incoming WS trade message via `asyncio.to_thread` (Python's default
  executor, not `services.tick_executor`'s pool) — runs four scoring reads for every
  trade that passes the `min_contracts` gate: `signal_log.recent_sides_for_ticker()`,
  `signal_log.cluster_factor()`, `_trend_factor()` (→ `market_history.momentum()`),
  `_analyst_factor()` (→ `market_analyst_agent.per_market.analyst_lean()`).
- Each of these opens a **fresh SQLite connection** per call, confirmed directly:
  `signal_log.py:250` (`with _connect() as conn:`), `signal_log.py:280` (same),
  `market_history.py:57`'s `_connect(db_path)`, `market_analyst_agent/_db.py:16`'s
  `_connect()`. Every one of the three `_connect()` implementations does
  `sqlite3.connect(DB_PATH)` (no `timeout=` — Python's 5.0s default busy timeout) plus
  `PRAGMA journal_mode=WAL` and a `CREATE TABLE IF NOT EXISTS`, on every single call.
- `candidate_log.record_rejection()` — the majority-case path for the many trades
  that get *rejected* rather than scored — was checked and ruled out: already fixed
  to be fully async/batched via `capture_writer` (P3 Task 17, 2026-08-27), zero
  synchronous DB I/O. Not part of this problem.
- Live telemetry (`GET /api/observability/summary`, read during this design):
  `whale_pipeline.stage.provider.window_avg_ms` — the stage timing exactly
  `whale_provider.fetch_signals()`, which wraps `_process_trades_sync()` — spikes to
  ~924ms average in one measured window, versus ~1ms for `capture` and ~0.2ms for
  `config`. `handler_total` correspondingly reaches a 20.3s max. This is independent,
  correlated confirmation of the same location the source points to.

Dropping BTC15M's threshold didn't just admit more data — it multiplied how many
trades hit this four-fresh-connections-across-three-files code path, on the app's
single highest-frequency series, from worker threads that can run concurrently
against the same SQLite files.

## 1a. A second, more severe instance of the same call path — found in revision 2

`services/candidate_retry.py`'s `run_pending()` (invoked from `main.py`'s own
`_candidate_retry_loop`) calls `provider.score_recovered_trade(trade, market, cfg,
now)` for a candidate whose market lookup failed on first try. This reaches
`_process_trades_sync` — the same four scoring functions — but **synchronously, on
the event-loop thread**, not via `asyncio.to_thread` at all. A slow call here blocks
the entire event loop (every async task, not just one worker-thread call) for its
duration, which is strictly worse than the WS-message path revision 1 analyzed. This
was missed in revision 1's root-cause narrative and is brought into scope here (see
section 4) — the fix targets the shared underlying functions, so covering this path
costs nothing extra and closes a worse bug than the one originally targeted.

**Also found, pre-existing and explicitly out of scope:** `_process_trades_sync`'s
own docstring claims `self._seen_trade_ids`/`self._seen_order` are "only ever
touched from within one in-flight `fetch_signals()` call at a time" — false once
`score_recovered_trade`'s event-loop-thread path is considered alongside a
worker-thread call running concurrently. Real, but a pre-existing thread-safety gap
unrelated to connection lifecycle; noted here as a pointer for a future item, not
addressed by this spec.

## 1b. Second root cause, independently verified — `run_offline()` saturating `tick_executor`

`services/diagnostics/diagnostics.py:764-765` (`run_offline`) loops over every
watched series (8, from `config/settings.yaml`'s `series_watcher.series`) and calls
`series_watcher.check_series_funnel()` once per series — not a single aggregate
call. `check_series_funnel()` isn't itself a single cost: it calls `reconcile()`
(`series_watcher.py:655`, its own real query cost against `signal_log.db`/
`paper_broker.db`, not otherwise analyzed here) unconditionally per series, and only
calls `funnel()` (`series_watcher.py:519-652` — the function spans to 652, not 567;
corrected in this revision) when both `signal_accuracy_pct` and
`realised_win_rate_pct` are non-None. `funnel()` runs two `raw_trades` aggregate
queries per call (`:543-547`, `:548-552`): both filter on `series`/`observed_at`
(covered by `idx_raw_trades_series (series, observed_at)`) *and* on
`excluded`/`resolved_side` (not covered by that index — every row in the
time-windowed range must still be examined to evaluate those conditions).
`raw_trades` has grown to ~38.4M rows (27.9GB) as of this revision, up from a
documented 30,787,297-row/22.3GB baseline (2026-08-30).

**Not the whole story — a sibling, already-documented cost lives in the same call,
missed in the first draft of this revision and caught on adversarial review.**
`run_offline()` also runs `check_confidence_input_coverage()` before the per-series
loop, which calls `signal_log.resolved_signals_with_factors()` with no `since_ts` —
a full, unscoped fetch against `signal_log.db`'s 103k+ rows. This is not a new
finding: `docs/open-decisions.md` already records it (2026-09-01,
whale-confidence-scoring-remediation Task 9), independently measured at ~1.0-1.1s,
already flagged there as "roughly a 20% duty cycle on [tick_executor] for this one
diagnostic alone," still open, awaiting a design call on whether to bound the fetch
or accept the cost. Section 4a's isolation fix resolves that entry's specific
"eats into tick_executor capacity" framing as a side effect — the whole
`run_offline()` call leaves that pool, this one included — even though the
underlying per-call cost of `resolved_signals_with_factors()` itself is unchanged
(query-bounding stays this spec's own non-goal). `docs/open-decisions.md`'s entry
should be updated to note this once section 4a ships, not left stale.

`services/quality/routes.py:81` wraps the entire `run_offline()` call — both the
`raw_trades`-scanning loop and the sibling cost above — in one
`await tick_executor.run(lambda: diagnostics.run_offline(cfg))` call — the **same**
2-worker pool `main.py` routes `_flush_trade_capture_async`/
`_resolve_and_record_settlements_async`/`_build_series_track_record_async`, and
`decision_bridge.py`'s `candidate_ledger.claim()`/`record_decision()` (which gate
every whale signal), through. `GET /api/quality/summary` is polled by the dashboard
every 5s regardless of either cost — so polls began queuing faster than they could
drain, both `tick_executor` workers ended up permanently occupied, and the
trading-critical work sharing that pool was starved. Confirmed live via `/proc`:
both worker threads in `futex_do_wait` (GIL contention) at ~82% CPU each,
continuously, for 5h10m+ at time of discovery — matching `capture_writer` "database
is locked" faults recurring live. Independently re-confirmed *twice* now (once in
this revision, once again during this revision's own adversarial review, both
after an emergency container restart): the fault recurs again within ~11 minutes of
a freshly-started process, and `GET /api/quality/summary` still hangs.

**What this mechanism confidently explains, and what it doesn't.** `tick_executor`
pool-sharing is well-supported as the cause of the recurring `capture_writer` lock
faults specifically — verified same pool object, verified faults recur quickly even
post-restart. It does **not** fully explain the broader "the bare event loop
stalls, other endpoints hang too" symptom: `GET /api/health/pipeline` (confirmed via
source to use `asyncio.to_thread`, never `tick_executor`) was independently
reproduced hanging as well, both in this revision's own check and again in its
adversarial review. `tick_executor` sharing can't be the cause of a hang on an
endpoint that never touches `tick_executor`. The more likely explanation is the
*other* half of this same spec: until the whale-scoring connection-reuse fix ships
(sections 1/1a/4), `_process_trades_sync` still runs on that same shared default
`asyncio.to_thread` executor `/api/health/pipeline`'s own code also uses — real
default-executor contention from the still-open half of this spec, not something
section 4a touches. **Both fixes in this spec are needed together for the full
picture** — this is the reason to ship them as one spec, not a coincidence.

**2026-08-27's own fix moved `run_offline()` off the event loop and onto
`tick_executor` specifically to avoid blocking it — a real, correct fix for the bug
it targeted, that became this one as the underlying table (and the sibling
unscoped fetch above) grew.** The lesson generalizes: routing something expensive
onto *a* thread pool isn't sufficient on its own — routing it onto the *same* pool
as trading-critical work, with no isolation, means its own cost growth over time can
silently start starving something else entirely unrelated to it. This is exactly
the failure mode section 4a's design closes — for the mechanism it actually covers.

## 2. Non-goals

- `capture_writer`'s flush cadence/batch size and `services.tick_executor`'s own
  2-worker pool sizing are **not** touched here. Increasing `tick_executor`'s worker
  count was considered and rejected for both mechanisms in this spec: SQLite
  serializes writers at the file level regardless of Python thread count (WAL allows
  concurrent readers alongside one writer, never concurrent writers to the same
  file), so more threads contending for the same lock plausibly makes
  lock-acquisition retry/backoff worse, not better — and per this repo's own HARD
  RULE, thread-count is not a parameter to guess-adjust without first identifying
  the measured mechanism, which this spec does instead (two, in fact: this is about
  isolating existing work onto separate small pools, not growing any one pool).
- Optimizing `funnel()`'s own query shape (e.g., composite indexes covering
  `excluded`/`resolved_side` so the aggregate queries stop examining every row in a
  series' time window) is a real, plausible complementary improvement, but is **not**
  in this spec — it would reduce `run_offline()`'s absolute cost, but wouldn't by
  itself fix the architectural problem (anything sharing `tick_executor`'s pool with
  critical writes remains a risk regardless of its own cost), and adding an index to
  a heavily-written table has its own write-amplification cost this spec hasn't
  measured. Worth a follow-up item, not bundled here.
- Reducing `/api/quality/summary`'s 5s poll interval, or moving it off polling
  entirely, is **not** in this spec — it would reduce trigger *frequency* but not fix
  the shared-pool starvation a single slow call already causes, and polling-interval
  changes are their own scoped decision elsewhere in this codebase's history, not
  something to fold in here.
- Re-raising `KXBTC15M`/`KXBTCD`/`KXETH15M`'s `min_contracts_by_series` back toward
  their computed P90 values is explicitly **out of scope** for this spec — that's
  the next-action item's own stated follow-up, gated on this fix actually landing
  and being validated, not bundled into it.
- `services/tick_executor.py`'s existing `connection_for()` utility is **not reused
  as-is** — see section 4 for why, and what this spec builds instead.

## 3. Why the obvious fix (reuse `tick_executor.connection_for()`) doesn't apply

`connection_for()` (`services/tick_executor.py:84-99`) is a thread-local SQLite
connection cache — genuinely correct, tested infrastructure, already built for this
exact *kind* of problem. But its own docstring states the precondition for safe use:
a module must be either "(a) restructured so its tick-executor-routed callers use a
dedicated, schema-initialized, single-writer-thread connection separate from that
module's other callers, or (b) proven safe by real measurement of lock-contention
frequency under load." Checked directly against all three files this fix touches —
none is single-caller:

- `signal_log.db` is also written from the event loop directly
  (`services/whale_stream/decision_bridge.py`'s `log_signal()` call, synchronous,
  not on a worker thread).
- `market_history.db` is written from `whale_stream_handlers.py` itself (a
  *different* function, `_process_stream_ticker`, via `record_snapshot_from_ticker`),
  plus `settlement_resolver.py`, `catalog_scan.py`, `main.py`.
- `market_analyst.db` is written from `market_analyst_orchestrator.py`.

None qualifies for (a). This spec's fix is therefore built to satisfy (b) instead —
a real, measured validation gate, not a code-review-only judgment call (see section 6).

A second, independent problem with reusing `connection_for()` directly: its cached
connections use a **50ms busy timeout** (`tick_executor.py:96`,
`PRAGMA busy_timeout = 50`) — **100x shorter** than the 5.0s Python default every one
of these three modules' own `_connect()` already uses (confirmed: none of
`signal_log.py:48`, `market_history.py`'s `_connect()`, or `market_analyst_agent/_db.py:18`
passes an explicit `timeout=`). Reusing `connection_for()` as-is would give the cached
*reader* 100x less patience under contention than the *writer* it's contending with —
a plausible new "database is locked" source on the reader side specifically, separate
from whether total write volume changes at all. This spec's cache uses the modules'
own existing 5.0s convention, not `tick_executor`'s tuning (which was chosen for a
different context and should stay scoped to it).

## 4. Design

**Two changes, not one: where this work runs, and how its connections are cached.**
Revision 1 only changed connection lifecycle and left the work running on Python's
shared default executor (via `asyncio.to_thread`). That's what let issue #145/#150
(a timed-out handler's OS thread isn't actually freed — confirmed live,
`handler_timeouts_total: 45` for the `trade` class, a 20.3s max handler duration
against the 10s timeout) turn this fix's own cache into a compounding leak: each
stuck thread would hold 3 open connections forever. Revision 2 fixes this by moving
the work onto a small, dedicated, explicitly-bounded pool instead — not
`tick_executor`'s shared 2-worker pool (already shared by
`candidate_ledger.claim()`/`record_decision()`, which gate whale-signal detection,
and shouldn't gain a new competitor), a **third, separate** pool sized only for this
workload.

**New: `services/whalewatchers/_scoring_pool.py`.**

```python
"""Dedicated worker pool + thread-local connection cache for
kalshi_trade_tape.py's per-trade scoring work (both the WS-message path,
_process_stream_trade -> fetch_signals, and the candidate-retry path,
score_recovered_trade - see docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md section 1a/4 for why both are in scope).

Deliberately its own pool, not services.tick_executor's shared one (that pool
is also used by candidate_ledger.claim()/record_decision(), which gate every
whale signal - this workload shouldn't compete with it) and not Python's
default asyncio.to_thread executor (shared process-wide with unrelated work
like backup.py/research.py, and unbounded up to 20 threads on this
container - too large to keep issue #145/#150's known thread-leak blast
radius small and observable). 4 workers: this call path normally needs ~1
concurrently (the WS consumer drains one queue item at a time), so this is
headroom for legitimate brief overlap plus the candidate-retry path, not a
guess at a load-bearing capacity number - if #145/#150's leak keeps
happening, all 4 workers eventually get stuck and further scoring work
queues (a visible backlog/latency symptom) rather than spawning unbounded
new OS threads and connections silently, which is the actual property this
design needs: bounded and observable, not eliminated (#145/#150 itself is a
separate, already-tracked bug this spec does not fix).

Connections are cached thread-local, tuned to the 5.0s busy-timeout
convention services/signal_log.py, services/market_history.py, and
services/market_analyst_agent/_db.py's own _connect() functions already use
- not services.tick_executor.connection_for()'s 50ms, which was chosen for
a different (write-capable) context. A parameterized connection_for() was
considered (add busy_timeout_ms/schema_init args, reuse across both pools)
and rejected for this revision: this pool's threads are a genuinely
different, deliberately-separate set from tick_executor's, so a second
small self-contained cache scoped to its own pool is at least as clear as
threading a parameter through a shared one, at the same code cost."""
import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whale-scoring")
_local = threading.local()


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)


def cached_read_connection(db_path: Path, schema_init: Callable[[sqlite3.Connection], None]) -> sqlite3.Connection:
    """schema_init runs once, only on this thread's first connect to this
    db_path - the exact "first use per thread runs DDL, reuse skips it"
    behavior revision 1's prose claimed but its code didn't implement."""
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(db_path)
    if conn is None:
        conn = sqlite3.connect(db_path)  # Python's 5.0s default busy timeout, matching this cache's three target modules' own _connect()
        conn.execute("PRAGMA journal_mode=WAL")
        schema_init(conn)
        cache[db_path] = conn
    return conn
```

**Modify `signal_log.py`, `market_history.py`, `services/market_analyst_agent/_db.py`:**
each gains one new read-only helper (e.g. `signal_log._scoring_read_connection()`)
that calls `_scoring_pool.cached_read_connection(DB_PATH, schema_init=<the module's
own existing schema-creation logic, factored out of its plain _connect() into a
small reusable function both paths call>)` — used *only* by
`recent_sides_for_ticker`/`cluster_factor`/`momentum`/`analyst_lean`. Every other
function in these three modules keeps its existing plain `_connect()` unchanged.

**Modify `kalshi_trade_tape.py`'s `fetch_signals()`:** replace
`asyncio.to_thread(self._process_trades_timed, ...)` with
`await _scoring_pool.run(lambda: self._process_trades_timed(...))`.

**Modify `kalshi_trade_tape.py`'s `score_recovered_trade()`:** this function is
currently synchronous; per section 1a, its call into `_process_trades_sync` now
needs to go through the same pool too, making it `async def`. Verified its one call
site (`candidate_retry.py:167`, `for signal in provider.score_recovered_trade(trade,
market, cfg, now):`) — `run_pending()` is already `async def` (`candidate_retry.py:103`),
so this becomes `for signal in await provider.score_recovered_trade(...)`: a
one-line change at a single call site, not a ripple through multiple callers.

**No change to `_process_trades_sync`'s own logic, gates, or scoring math** — this is
purely a connection-lifecycle and execution-context change. Every query still
executes fresh against live data at call time; nothing is cached, batched, or
deferred.

**WAL checkpoint-starvation:** verified, not assumed — none of the four scoring
functions holds a transaction open across calls; each executes one `SELECT`, fully
drains it via `.fetchall()`, and returns within the same call. No long-running
reader snapshot is ever held that could block a writer's checkpoint.

## 4a. Design — diagnostics gets its own isolated pool

Same principle as section 4, applied to the second mechanism (section 1b): move
`run_offline()`'s work off `tick_executor`'s shared pool onto its own dedicated one,
so its cost — whatever it is, today or after any future query optimization — can
never again starve the trading-critical writes that pool exists to protect.

**New: `services/diagnostics/_diagnostics_pool.py`** — same shape as
`services/whalewatchers/_scoring_pool.py` (Task 1 of the implementation plan),
deliberately a separate module and a separate `ThreadPoolExecutor`, not a shared
utility between the two: they isolate two unrelated workloads from `tick_executor`
for two unrelated reasons (whale-scoring's per-trade connection overhead vs.
diagnostics' per-series-loop cost against a large table), and a future change to one
pool's sizing/behavior shouldn't need to reason about whether it affects the other.

```python
"""Dedicated worker pool for services/diagnostics/diagnostics.py's
run_offline() - see docs/superpowers/specs/2026-09-01-whale-scoring-
connection-reuse-design.md section 1b/4a for why this needs its own pool,
not services.tick_executor's shared one: run_offline()'s per-series
raw_trades aggregate queries (services/series_watcher.py's funnel()) grew
expensive enough, as raw_trades grew past 38M rows, to permanently occupy
both of tick_executor's 2 workers - starving the trading-critical writes
that pool exists to protect (confirmed live, 2026-09-01: both workers in
futex_do_wait for 5h10m+, capture_writer lock faults recurring).

2 workers, not tick_executor's 2 (shared with trading-critical work,
the exact problem being fixed) or the whale-scoring pool's 4 (a
different, higher-frequency workload shape). Corrected during this
revision's own adversarial review: an earlier draft assumed 1 worker
on the reasoning that only the dashboard's own 5s poll calls this
endpoint - false. Real, structural other callers exist: this repo's
own .claude/hooks/guard_workflow.py routes sessions to this exact
endpoint, CLAUDE.md's own "Start investigations here" names it step 1
(printed every session banner), tools/quality_coordination.py also
calls it, and this repo routinely runs multiple parallel Claude
sessions that each independently check it - with individual calls
already running 15-20s+, overlap between the dashboard's own poll and
a session's manual check is plausible, not an edge case. 2 gives
headroom for that realistic pattern without reintroducing
tick_executor's own problem (unbounded, unisolated sharing) - if 2
still isn't enough, that becomes a measurable, attributable backlog on
THIS pool specifically (see below), not a guess to get right on the
first try."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="diagnostics")


async def run(fn: Callable[[], T]) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn)
```

**Modify `services/quality/routes.py:81`:** replace
`await tick_executor.run(lambda: diagnostics.run_offline(cfg))` with
`await _diagnostics_pool.run(lambda: diagnostics.run_offline(cfg))`.

**No connection caching added here** — unlike the whale-scoring fix, this spec
doesn't change `series_watcher.py`'s own connection handling or query shape (see
section 2's non-goals: query optimization is a real, separate follow-up, not bundled
in). This section is isolation only: same expensive call, same cost, just no longer
capable of starving `tick_executor`'s critical-path work regardless of how expensive
it is or becomes. `run_offline()` becoming slower over time (as `raw_trades` keeps
growing, or as `resolved_signals_with_factors()`'s own unscoped fetch grows with
`signal_log.db`) will now show up as `/api/quality/summary` itself getting
slower — a direct, attributable, visible symptom on the one endpoint that actually
causes the cost — rather than an indirect, confusing "trading writes are failing"
symptom on an unrelated part of the system.

**Stated explicitly, not left implicit: `/api/quality/summary` will very likely
still be slow after this fix ships**, possibly still look "hung" from the
dashboard's perspective on a bad day — this is isolation, not a performance fix for
`run_offline()` itself. The real, load-bearing improvement is that its cost can no
longer take `capture_writer`'s writes down with it. Whether the endpoint's own
residual slowness is acceptable, or worth `docs/open-decisions.md`'s already-open
query-bounding question (Task 9) being picked up as a follow-up, is a separate call
this spec doesn't make.

**Interaction with the overlapping-call failure mode:** if polls keep arriving faster
than `run_offline()` completes, they'll now queue behind this pool's single worker —
visible as growing latency on `/api/quality/summary` specifically, not a silent
thread/GIL leak elsewhere. This is the same "bounded and observable, not eliminated"
property section 4's whale-scoring pool design already established — consistent
architecture across both fixes in this spec, not two different philosophies.

## 5. Resilience

**Corrected in revision 2** — the file-relocation scenario revision 1's mitigation
targeted isn't the realistic risk. On Linux, deleting/moving a file out from under an
open `sqlite3` connection does not raise `OperationalError`; the open connection
keeps working against the now-unlinked inode while a fresh connection would silently
open an empty new file — a retry-on-exception wouldn't even fire for this case. It's
also not practically reachable in this app: `data/*.db` files are live, permanent,
never deleted while the process runs (CLAUDE.md's own standing invariant) — so this
isn't a real operational risk here, and the spec says so plainly rather than
implying a mitigation covers it.

What the cache does need to handle: `sqlite3.ProgrammingError` from genuine API
misuse (e.g., a connection closed elsewhere by mistake) — catch it at each of the
four call sites, evict the stale cache entry, retry once with a fresh
`cached_read_connection()` call. Cheap, and matches the actual failure mode this
cache could realistically hit, rather than one it can't.

## 6. Testing strategy — the required measured validation gate

Per `connection_for()`'s own documented bar (satisfying its option (b), since none of
the three files qualifies for (a)): **this must be validated against real, measured
lock-contention frequency under load before being trusted, not shipped on code
review alone.**

- Unit: `cached_read_connection()` returns the same connection object on repeated
  calls with the same `db_path` from the same thread, and a *different* connection
  object when called from a different thread (spin up 2 real threads, assert
  `id(conn)` differs — proves thread-locality, not just asserts the docstring).
  The retry-on-broken-connection fallback (section 5) gets its own test: close the
  cached connection out from under it, confirm the next call transparently recovers.
- Integration: extend the existing test fixtures for `recent_sides_for_ticker`/
  `cluster_factor`/`momentum`/`analyst_lean` to run under the new cached path and
  confirm identical results to the current fresh-connection path for the same
  inputs — this is a pure connection-lifecycle change, so behavior must be bit-for-bit
  unchanged.
- **Required before merge, not optional:** a real, scripted load test against a
  paper-mode soak, replaying a burst profile comparable to the actual incident.
  `data/observability.db` stores windowed aggregates (`count`/`min`/`max`/`avg`),
  not a raw per-message trace, so this is a representative synthetic profile derived
  from those aggregates (peak sustained trades/sec over the incident's measured
  window), not a literal message-by-message replay — corrected from revision 1's
  overstated "replay the real historical rate" framing. Measure
  `capture_writer_health`/lock-fault occurrence rate and `last_tick_duration_sec`
  before and after this change, under otherwise-identical conditions, plus worker
  count in `_scoring_pool`'s executor (should stay at or near 4, not silently grow -
  the pool itself has a fixed size, but confirm nothing bypasses it). A clean run
  alone (no faults, tick duration recovers) is the pass criterion for *this* fix
  specifically; it does not by itself prove the original `min_contracts_by_series`
  thresholds are now safe to re-raise, and it does not fix issues #145/#150
  themselves (this design bounds their blast radius, it doesn't resolve the
  underlying cancellation gap) — both stay separate, explicitly out-of-scope items.

**Diagnostics pool validation (section 4a), added in revision 3:**

- Unit: `_diagnostics_pool.run()` executes on a thread named `diagnostics-*`, not
  the event loop and not a `tick_executor`/`whale-scoring` thread.
- **Required before merge, not optional, same bar as the whale-scoring fix:** confirm
  live that `GET /api/quality/summary` no longer competes with trading-critical
  writes for `tick_executor` capacity — capture `tick_executor`'s own worker
  business (a simple probe: submit a no-op and time how long it takes to run) while a
  `run_offline()`-triggering poll is in flight, before and after this change. Before:
  the probe should show contention/delay while `run_offline()` is running (this is
  the bug, so it should be reproducible). After: the probe should return promptly
  regardless of `run_offline()`'s own state. This is the direct, mechanistic proof
  the isolation actually isolates, not just an indirect inference from fewer faults.
- Confirm `/api/quality/summary`'s own response time is unaffected (or, if
  `run_offline()` is still slow against the current table size, that the *symptom*
  is now confined to that one endpoint's latency, not visible as `capture_writer`
  lock faults or other-endpoint hangs).

## 7. Rollback

Additive: two new modules (`_scoring_pool.py`, `_diagnostics_pool.py`), a handful of
small new helper functions in existing modules, one call-site swap in
`quality/routes.py`. No change to any existing function's signature or behavior for
any *other* caller, no schema change, no config field. Revertible with a normal
`git revert`, and the two fixes are independently revertible from each other (touch
disjoint files).

## 8. Spec self-review (revision 3)

- Placeholder scan: none — every file, line number, busy-timeout value, thread count,
  and row/size figure above was read from current source or live telemetry during
  this design (section 1b's numbers independently re-confirmed in this revision, not
  taken on the peer session's word alone: re-ran the `curl` timeout myself).
- Internal consistency (carried from revision 2): section 4's dedicated pool directly
  resolves both item 1 (leak boundedness) and item 2 (thread-count stability) from
  the original review; section 1a's `score_recovered_trade` scope addition is carried
  through consistently into section 4's implementation and section 6's test plan.
- Internal consistency (new in revision 3): section 4a's diagnostics pool follows the
  same architectural principle section 4 establishes (isolate onto a dedicated,
  deliberately-small pool; make overload a visible backlog, not a silent leak) —
  stated explicitly in section 4a rather than left as an implicit parallel, and the
  two pools are kept genuinely separate (own modules, own executors) rather than
  merged into one shared "everything non-critical" pool, since they isolate two
  unrelated workloads for two unrelated reasons.
- Scope check: this spec now covers two independently-revertible fixes under one
  architectural principle, per direct instruction to revise together rather than run
  disconnected efforts — each remains a cohesive unit on its own (section 7 notes
  they touch disjoint files), so this isn't scope creep into an unrelated third
  thing, but it is now two fixes, and the implementation plan (next) will need two
  correspondingly separable groups of tasks.
- Ambiguity check: section 4a's worker-count sizing is justified with reasoning tied
  to realistic known callers, not an unexplained number.

**Post-adversarial-review update (this section written before that review; findings
below are from it, addressed above, not re-asserted here as new self-review):** the
new section 1b/4a material got its own fresh, independent adversarial review
(separate consolidation doc, `2026-09-01-diagnostics-pool-addition-review.md`,
NO-GO). Real findings, all fixed in this file: two citation inaccuracies
(`funnel()`'s actual line span; `check_series_funnel()`'s conditional, not
unconditional, call into `funnel()`), a missed cross-reference to an already-open,
same-day `docs/open-decisions.md` entry describing a sibling cost inside the same
`run_offline()` call, an important reframing (this fix confidently explains
`capture_writer` faults but only *partially* explains the broader event-loop-stall
symptom — the rest needs the whale-scoring half of this same spec), and the 1-worker
diagnostics-pool sizing changed to 2 once real evidence of legitimate concurrent
callers surfaced. Fix-list recheck: re-read every edited section above against the
review's five items after making the edits — all five addressed, no new gap found
in this pass.
