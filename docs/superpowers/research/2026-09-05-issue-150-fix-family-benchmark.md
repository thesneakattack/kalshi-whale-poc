# Issue #150 fix-family benchmark: thread-pool leak / SQLite hang tradeoff

Investigation/benchmark doc, same shape and rigor as #601/PR #603's
candidate_log benchmark. **Docs-only - no application code changes.**
Triggered by issue #605 (a real, currently-recurring event-loop-stall
episode, 9.3-9.6s magnitude, observed 2026-09-05) as motivating context for
revisiting issue #150's own stated re-evaluation trigger
(`handler_timeouts_total` climbing "with any regularity"), which issue
#150's own comment thread already confirmed fired once (30 timeouts,
2026-08-30). This doc does not claim to have root-caused #605 itself -
that link is correlation only (same order of magnitude, same 10s
mechanism), never asserted as proven causation anywhere below.

## Headline finding, load-bearing for everything else in this doc

**Fix Family 1 (a dedicated, smaller `ThreadPoolExecutor` for the
trade-scoring pipeline) is already shipped**, independently of issue #150
ever being actioned:

- `services/whalewatchers/_scoring_pool.py` (added 2026-09-01, commit
  `3ff41d4`, "feat: add dedicated worker pool + connection cache for
  whale-scoring reads") gives `fetch_signals`'s WS-trade path its own
  `ThreadPoolExecutor(max_workers=4)`, reached via
  `loop.run_in_executor(_executor, fn)` - explicitly *not* the shared
  default `asyncio.to_thread` pool. The module's own docstring: "not
  Python's default asyncio.to_thread executor (shared process-wide with
  unrelated work, unbounded up to 20 threads on this container)."
- `services/whalewatchers/_candidate_retry_pool.py` (split out 2026-09-04,
  commit `c6f3295` - "feat: dedicated 1-worker pool for candidate-retry
  scoring (#563)"; `score_recovered_trade` wired onto it two minutes later
  by commit `9b55c1b`, same day) gives `score_recovered_trade`'s
  candidate-retry path a *separate* `ThreadPoolExecutor(max_workers=1)`.

Both predate this investigation and postdate issue #150's filing
(2026-08-28T12:00:45Z, confirmed via `gh issue view 150 --json createdAt`).
Issue #150's own body and `services/kalshi/websocket.py`'s code comments
still describe the pre-fix shape (`asyncio.to_thread(self._process_trades_timed,
...)`) verbatim - that description is now stale relative to the actual
call graph. Confirmed by direct source read of
`services/whalewatchers/kalshi_trade_tape.py`'s `fetch_signals()`:

```python
signals, started, finished = await _scoring_pool.run(
    lambda: self._process_trades_timed(...),
)
```

Neither `_scoring_pool.py` nor `_candidate_retry_pool.py` mentions issue
#150 anywhere in their own text - they were built for a different,
unrelated reason (write-path capacity / candidate-retry isolation) and
happen to also satisfy Family 1's proposal as a side effect. This has not
been previously connected to #150 in the repo (checked: neither file, nor
issue #150's own comment thread, cross-references the other).

## Mechanism investigation - what `_process_trades_sync` actually touches

Read in full: `services/kalshi/websocket.py`'s `_process_item` (wraps
`_handle_message` in `asyncio.wait_for(..., timeout=self._handler_timeout_sec)`,
`_HANDLER_TIMEOUT_SEC = 10.0`) and `services/whalewatchers/kalshi_trade_tape.py`'s
`fetch_signals` / `_process_trades_timed` / `_process_trades_sync`.

Per real WS trade message, `fetch_signals` is called with
`market_context["trade_tape"] = [trade]` - exactly **one** trade
(confirmed at `services/whale_stream/whale_stream_handlers.py:243`), not
the whole cached tape. `_process_trades_sync`'s per-trade loop, for one
qualifying (not-yet-seen, whale-sized, resolvable-market) trade, makes at
most:

| Call | Blocking? | Connection shape |
|---|---|---|
| `candidate_log.record_rejection` (rejection path only) | **No** - fully non-blocking since 2026-08-27 (P3 Task 17): both writes route through `capture_writer.submit()`, zero synchronous SQLite I/O | n/a |
| `signal_log.recent_sides_for_ticker` | Yes, read | `_scoring_pool.cached_read_connection()` - thread-local cache, schema-init only on first touch per thread |
| `signal_log.cluster_factor` | Yes, read | same cached connection |
| `market_history.momentum` (via `_trend_factor`) | Yes, read | same cached-connection pattern (`market_history.py`'s own `_scoring_read_connection`) |
| `market_analyst_agent.analyst_lean` (via `_analyst_factor`) | Yes, read | same cached-connection pattern (`per_market.py`'s own `_scoring_read_connection`) |
| `series_evaluator.record_trades_observed_bulk` | **Never actually called** (see "Separate finding" below) | n/a |

`self._seen_lock` (a `threading.Lock`) is held only across in-memory
`set`/`dict` operations (lines 645-664) - released well before any of the
five SQL calls above run. Ruled out as a cross-thread serialization
mechanism (verified by direct source read, not assumed).

## Separate finding (not one of the two fix families - reported, not fixed here)

While tracing every write in this call path for Family 2, found that
`series_evaluator.record_trades_observed_bulk()` - the function
`_process_trades_sync`'s own docstring and inline comments (lines
606-619) say is called "once at the end of this loop" to persist
`trades_observed_by_series` - **is never actually called anywhere in
application code**. The dict is built during the per-trade loop
(`kalshi_trade_tape.py:620`, incremented at `:668`) and then silently
discarded: the function returns at line 880 with no reference to it after
line 668, and a repo-wide `grep -rn "record_trades_observed_bulk("`
finds real invocations only inside `tests/test_series_evaluator.py` -
never from `kalshi_trade_tape.py`, `main.py`, or anywhere else in
`services/`. `git log -S` on this exact call across the whole history of
`kalshi_trade_tape.py` returns nothing - this was never wired up, not a
later regression; the introducing commit (`a84d35c`, 2026-08-11) added the
docstring's promise and the dict-building loop but not the call itself.

Confirmed live (read-only query against `data/series_evaluator.db`, no
write, no live app interaction): all 38 rows in `series_status` show
`trades_observed = 0`, and the newest `first_seen_at` is ~594 hours
(~24.75 days) old - the table was seeded once and has not moved since,
despite the app continuously processing real trades that whole time.
`main.py` only calls `series_evaluator.evaluate_pending()` (the read/
decision side) - nothing calls `record_trade_observed`/
`record_trades_observed_bulk` (the write side) from any live code path.

This is a real, live, data-plane-completeness bug (CLAUDE.md: "a dropped
message, skipped candidate, or DB hole is a defect") - `series_evaluator`'s
whole population-size gate has been evaluating against permanently-zero
trade-observation counts for at least as long as the table's oldest row.
It is **not** part of either of issue #150's two fix families and is not
fixed in this docs-only investigation; it is filed as its own GitHub issue,
#621, so it does not get lost as a footnote.
It does, however, directly affect the Family 2 analysis below: it means
`_process_trades_sync` currently performs **zero** synchronous writes in
practice, not one, which only strengthens the conclusion that ordinary
SQLite lock contention cannot be the mechanism behind a stall in this
specific function's own call graph today.

## Family 2 (root-cause the hang): busy_timeout is present everywhere - no gap to fix

Checked every `_connect()`-style helper reachable from this path:
`services/db.py` (`candidate_log.py`, `series_evaluator.py`) sets an
**explicit** `PRAGMA busy_timeout = 5000`. `signal_log.py`,
`market_history.py`, `market_analyst_agent/_db.py`, and
`_scoring_pool.py`'s own `cached_read_connection` all do a bare
`sqlite3.connect(db_path)` with no `timeout=` override, relying on
Python's documented `sqlite3.connect()` default (`timeout=5.0`) -
`_scoring_pool.py`'s own docstring says this explicitly ("Connections
cached here use Python's 5.0s default busy timeout").

**Issue #150's literal Family-2 proposal ("a busy_timeout gap") does not
exist in current source.** Verified empirically, not just read from
docstrings (`bench/family2_busy_timeout_mechanism.py`, real
`sqlite3`/`threading` primitives against disposable tmp-file DBs, never a
live or copied `data/*.db`):

- Section 1: a writer holding a lock for 3.0s, contended by a bare
  `sqlite3.connect()` reader with **no** explicit timeout - the contender
  succeeds at 3.16s, not raises. Confirms the implicit default actually
  behaves as a real busy-wait, not a silent pass-through.
- Section 1b/2: same setup with the holder never releasing - both the
  implicit default and an explicit `PRAGMA busy_timeout = 5000` raise
  `OperationalError` at **5.006-5.007s**, matching to the millisecond.
  Confirms the implicit default and the explicit PRAGMA are the exact same
  mechanism, not merely similar.
- Section 3: under WAL (set on every DB in this path), a plain `SELECT` on
  a separate connection returns in **0.00024s** while a writer holds an
  open, uncommitted transaction for 4.0s. Confirms `_process_trades_sync`'s
  four read calls are structurally shielded from writer contention in the
  ordinary case - not merely "bounded," genuinely near-zero-latency.
- Section 4: two *sequential* short-lived writers (3.0s then 3.0s, neither
  individually near the 5s ceiling) still make a contending write's own
  busy_timeout - measured from **its own** first attempt, not refreshed
  per new holder - expire at 5.006s. A parallel case with holds summing to
  only 4.0s (2.0s + 2.0s) succeeds at 4.05s. This is a real, reproducible
  mechanism by which chained-but-individually-short writer contention
  *can* exceed a nominal 5s ceiling - but it requires **multiple
  overlapping writers to the same file**, and (see "Separate finding"
  below) `_process_trades_sync` currently performs **zero** writes at all
  in practice, so there is no writer here for this chaining mechanism to
  even apply to today. It is documented above as a general, verified
  mechanism in case a future fix reconnects `record_trades_observed_bulk`
  (or any other write) to this hot path - at that point Option B's
  up-to-4-concurrent trade dispatch would be a real, realistic source of
  exactly this kind of contention against `series_status.db`.

**Stronger, live-telemetry-based finding, not just synthetic**: issue
#150's own comment thread (2026-08-30) recorded `handler_timeouts_total:
30` alongside `handler_exceptions_total: 0` for the exact same observation
window. Every mechanism a `busy_timeout` (implicit or explicit) can
produce **always eventually raises a catchable `sqlite3.OperationalError`**
- confirmed above, never hangs past ~5s without raising. An
`OperationalError` inside `_handle_message` propagates up through
`_process_item`'s `except Exception` branch and increments
`handler_exceptions_total`, not `handler_timeouts_total`. Since
`handler_exceptions_total` was **zero** while 30 real timeouts occurred,
**ordinary SQLite lock contention (in the busy_timeout sense) is ruled out
as the mechanism behind every one of those 30 observed real timeouts** -
not just unproven, actively contradicted by the app's own recorded
counters. This is a stronger claim than "no gap was found": the evidence
says the mechanism these 30 events actually hit is not SQLite lock
contention at all.

**What remains unexplained.** No other concrete, verifiable-from-source
mechanism inside `_process_trades_sync`'s SQLite calls was found that
could produce a >10s stall with no exception ever raised. WAL-file sizes
for `signal_log.db`/`market_history.db`/`candidate_log.db` were checked
live (read-only `stat`, no content read, no live DB touched otherwise):
3.93-4.46 MiB each (`candidate_log.db-wal` 4,124,152 bytes,
`signal_log.db-wal` 4,663,872 bytes, `market_history.db-wal` 4,676,232
bytes), consistent with SQLite's default ~1000-page
auto-checkpoint threshold - no evidence of runaway WAL bloat *at this
snapshot*, though this is a single point-in-time reading, not a standing
guarantee. Candidates that would explain a true, exception-free multi-
second-to-indefinite stall - a genuine OS-level disk I/O stall (this
environment has documented WSL2/bind-mount I/O quirks in unrelated
incidents), or GIL/scheduling starvation from an unrelated CPU-bound
thread - are plausible but **not verifiable from static analysis or a
disposable-fixture benchmark**; confirming either would need a live stack
capture (`sys._current_frames()` / `py-spy`) taken *during* a real
recurrence, which is out of scope for benchmark work and is exactly what
issue #527 already notes as a limitation of this app's own watchdog
sampler. Per this task's own instruction: stated plainly here rather than
forcing a Family-2 fix design onto an unconfirmed mechanism.

## Family 1 (dedicated pool): verified isolation benefit, negligible overhead

`bench/family1_pool_isolation.py`, real `asyncio` / `ThreadPoolExecutor`
objects (never a hand-rolled timing simulation), modeling
`_process_item`'s exact `asyncio.wait_for(run_in_executor(...), timeout=N)`
shape:

- **Starvation, shared-pool-shaped (20 workers, matching the live
  container's real `min(32, os.cpu_count()+4)` = 20 per issue #150's own
  2026-08-30 measurement)**: 5/20 slots stuck -> an unrelated quick task
  completes in 0.004s (spare capacity absorbs it). **20/20 slots stuck ->
  the same unrelated quick task never completes within a 3s wait**
  (real, measured starvation once the shared pool is genuinely
  exhausted).
- **Dedicated-pool-shaped (4 workers, matching the real shipped
  `_scoring_pool` size)**: 4/4 slots stuck, with unrelated work submitted
  to a *separate* pool object (standing in for the shared default or any
  other app subsystem) -> completes in 0.0004s, completely unaffected.
  This is the isolation property the shipped design rests on, now backed
  by a real measurement rather than architectural intuition. **Caveat**:
  this isolation is specifically about worker-*slot* leakage (issue #150's
  own confirmed mechanism - a genuinely idle, blocked OS thread that
  releases the GIL while waiting). It models a leaked-but-idle thread, not
  a CPU-bound thread that never yields - the "what remains unexplained"
  paragraph above already leaves GIL/scheduling starvation as an
  unruled-out candidate mechanism for real #605-shaped stalls, and *that*
  failure mode is not something two separate `ThreadPoolExecutor` objects
  in the same interpreter would isolate from each other, since they still
  share one GIL. This benchmark does not claim otherwise; it verifies the
  specific mechanism issue #150 documents, not every conceivable stall
  mechanism.
- **Exhaustion budget, pure arithmetic on real, previously-recorded
  numbers**: the one real observed burst (30 timeouts in one window) would
  fully exhaust *either* pool size (30 > 20, 30 > 4) if those 30 truly
  landed concurrently - but issue #150's own follow-up comment already
  showed the app kept processing continuously through that window, which
  only makes sense if timed-out threads eventually complete and free their
  slot (transient occupancy, not a permanent leak - true on either pool
  size). The dedicated pool's real, non-eliminated tradeoff is a *smaller
  shock absorber* (4 vs 20 slots before every worker is simultaneously
  stuck) - exactly what `_scoring_pool.py`'s own docstring already
  concedes ("if #145/#150 keeps happening, all 4 workers eventually get
  stuck ... a visible backlog/latency symptom" rather than silent,
  cross-subsystem starvation).
- **Overhead**: thread-startup cost is trivial either way (dedicated-4:
  0.86ms; shared-20: 4.68ms, one-time). Per-submission dispatch overhead
  at the one concretely documented real rate in this repo (27.3/sec,
  `services/whalewatchers/kalshi_trade_tape.py`'s own comment, line 64:
  "1,640 prints/min across the WATCHED series alone") is statistically
  indistinguishable between
  pool sizes (mean 0.33ms dedicated-4 vs 0.34ms shared-20). Re-measured at
  an explicitly-labeled *assumed* stress rate (200/sec, well above any
  documented real figure - exchange-wide volume is described only as
  "many times" 27.3/sec, no exact number found in this repo) - still
  indistinguishable (0.26ms vs 0.30ms mean). **A dedicated 4-worker pool
  costs no measurable per-message overhead at any volume this repo has
  documented or plausibly anticipates.**

## Correctness / failure behavior

No application code changes are proposed by this doc (Family 1 is already
shipped; Family 2 has no fix to propose since its named gap does not
exist), so there is no new code path to run an old-vs-new correctness
diff against, unlike #601/#603's shape. What was verified instead:

- `_scoring_pool.cached_read_connection`'s eviction path
  (`sqlite3.ProgrammingError` -> reopen) and its "schema_init only on
  first touch" contract were confirmed by direct source read
  (`services/whalewatchers/_scoring_pool.py:58-75`), not exercised by a
  new test here - this is existing, already-tested shipped code
  (`tests/test_whalewatchers_candidate_retry_pool.py` covers the sibling
  pool's sizing contract).
- Failure behavior of the shared-vs-dedicated pool split under fault
  injection (a hung task) is exactly what Section 1 above measures: full
  starvation on a saturated shared pool vs. zero impact on unrelated work
  when isolated - the deterministic fault-injection rigor #603 used for
  Family 3's isolation claim, applied here to Family 1's isolation claim.

## Complexity

Family 1: **zero additional complexity** - already shipped, no new code to
write, review, or maintain. Family 2: **no fix to design** - its proposed
mechanism (a busy_timeout gap) does not exist, so there is nothing to
implement; inventing a fix for an unconfirmed mechanism would itself
violate the data-plane HARD RULE's "identify the measured bottleneck and
its mechanism first."

## Recommendation

**No application code change from this investigation.** Family 1 is
already fully shipped and its isolation claim is now benchmark-verified
(previously it was architectural intuition, backed only by the shipped
code's own docstring reasoning - not by a runnable, falsifiable
measurement). Family 2's literal proposal is falsified by both a targeted
synthetic benchmark and independently by the app's own historical
telemetry (`handler_exceptions_total: 0` alongside `handler_timeouts_total:
30`), so there is no root-cause fix to design or implement.

Concrete, small follow-ups worth doing separately (each its own later,
reviewed task - not bundled into this docs-only investigation per
"nothing advances on one pass"):

0. **Filed as issue #621** (done, during this investigation): the
   "Separate finding" above (`series_evaluator.record_trades_observed_bulk()`
   never called from any live code path; `trades_observed` has sat at 0
   for every row for ~25 days) - a real, live data-plane-completeness bug,
   unrelated to either fix family, that should not be fixed inside this
   docs-only investigation but must not be lost as a footnote either.
1. **Update issue #150 itself** with a comment noting Family 1 shipped
   independently 2026-09-01/03 (`_scoring_pool.py`/`_candidate_retry_pool.py`),
   linking this doc, and correcting its still-open "revisit trigger fired,
   not yet fixed" status - the trigger fired and the fix it named already
   exists, just was never connected to this issue number.
2. **Update `services/kalshi/websocket.py`'s stale code comments**
   (lines ~188, ~782) that still describe `asyncio.to_thread(self.
   _process_trades_timed, ...)` verbatim - the real call graph has been
   `_scoring_pool.run(...)` since 2026-09-01. Same correction is due in
   `services/kalshi/CHEATSHEET.md` (lines ~256, ~286, confirmed via
   grep to carry the identical stale quote).
3. If `#605`-shaped stalls recur, the next actionable step is a **live
   stack capture during the stall itself** (`sys._current_frames()` /
   `py-spy dump`), not another static-analysis pass - this doc has already
   exhausted what source reading and disposable-fixture benchmarking can
   establish about the mechanism.
4. The remaining, accepted risk this doc's Section "Family 1" numbers
   quantify (a dedicated 4-worker pool's smaller shock absorber vs the
   shared 20-worker default) is already the shipped design's own
   documented tradeoff, not a new finding requiring action - it is closer
   to `_scoring_pool.py`'s own stated goal ("bounded and observable...not
   eliminating #145/#150 itself") than to an open gap.

## Artifacts

- `bench/family2_busy_timeout_mechanism.py` / `bench/out/family2_busy_timeout_mechanism.json`
- `bench/family1_pool_isolation.py` / `bench/out/family1_pool_isolation.json`

Both scripts operate exclusively on disposable `tempfile`-created SQLite
files or in-process `ThreadPoolExecutor`/`asyncio` objects - no live
`data/*.db` file was opened, copied, or modified by this investigation.
