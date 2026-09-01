# PR #414 Adversarial Review — Event-Loop-Blocking Elimination (Fix 1)

Independent adversarial pass per CLAUDE.md's "nothing advances on one pass" HARD
RULE. Fresh context, no memory of how this PR was produced. Every claim below was
re-derived from primary sources this session — the actual committed source at
`fix/event-loop-blocking-elimination` (HEAD `c4402ba`), actual test runs against
that checkout, and direct reads of `services/kalshi/websocket.py` — never taken
from the PR body's own summary or the design/plan docs' own tables.

## Verdict: **NO-GO as submitted**

The four claimed fixes (`index_feed.record_cfbenchmarks`/`record_pyth`,
`settlement_edge.record_observation`, `game_state.record`,
`series_watcher.record_book`) are correct, complete for what they target, and
well-tested — confirmed independently below. But the PR's own headline claim of
completeness ("app-wide grep... confirmed complete, not a partial list") does not
hold for the underlying bug class: two more inline, synchronous, unbounded
`flush()` calls remain live in the exact file this PR already touches
(`services/index_feed/ingestion.py`), reachable from the same async event-loop
context, undisclosed anywhere in the PR, design spec, or plan. This is precisely
the class of gap the adversarial-review cycle exists to catch before merge, not
after. The required fix is small and mechanical — same pattern this PR already
established, applied to two more call sites — so this is not a request to
redesign anything, just to finish the sweep the PR already claims to have done.

---

## Confirmed findings (personally verified — not suspected, not assumed)

### Finding 1 (primary, blocking): the "4 files, confirmed complete" claim is false for the underlying bug class

`services/index_feed/ingestion.py` — the same file Task 1 of this PR modified —
contains two more functions that call `flush()` inline and synchronously, with
no `await` point, from async event-loop context:

- `last_tick_before()` (line 212): `flush()` called unconditionally, before a
  `SELECT MAX(observed_at)` read, "so a tick still sitting in `_tick_buffer`
  isn't missed" (the function's own docstring — a genuine correctness
  requirement, not a bug in intent).
- `record_cfbenchmarks_backfill()` (line 287): `flush()` called unconditionally
  after buffering backfilled rows, "persist immediately rather than waiting for
  the live stream's `_FLUSH_BATCH` threshold."

Both are plain `def` functions (confirmed: neither is `async def`). Both are
called with no `await` from `services/index_feed/backfill.py`'s
`async def backfill_index()` (line 177: `ingestion.last_tick_before(...)`, line
193: `ingestion.record_cfbenchmarks_backfill(...)`), which is itself called from
`async def check_and_backfill()`, which is called from `main.py`'s
`async def _index_feed_backfill_loop()` (main.py:1262). That loop is wired at
`main.py:1372` via `task_supervisor.supervise(lambda:
_index_feed_backfill_loop(index_stream), ..., restart=True)` —
`task_supervisor.supervise` is confirmed (read in full) to be a thin wrapper
around `asyncio.create_task`, i.e. this loop runs directly on the main asyncio
event loop, not in a thread or a separate process.

This is the exact bug shape the PR's own design spec's Context section names as
the general principle it is fixing ("the asyncio event loop must never be
blocked by synchronous SQLite I/O... via a raw unawaited synchronous call with
zero offload at all") — but the audit method used to find "the fourth instance"
(`grep -rl "should_flush\s*=" services/`) only matches the buffer-full-gated
shape (`if should_flush: flush()`), not an unconditional direct call. I
re-ran that exact grep myself — it returns exactly the 4 files the PR claims,
confirming the grep is self-consistent, and separately grepped for the literal
`flush()`-alone-on-its-own-line pattern app-wide
(`grep -rn "^\s*flush()\s*$" services/`), which found exactly these two
additional hits, both in this one file, nowhere else. Neither is mentioned in
the PR body, the design spec's "Files touched" list, or the implementation
plan's Task 1 Step 1 check (which only verified no other caller of
`record_cfbenchmarks`/`record_pyth` exists — a different, narrower question
than "does this file have other synchronous `flush()` calls").

Severity, not just theoretical:
- Frequency is low ("rare, only on reconnect" per the function's own docstring)
  but real — every WS reconnect triggers this path, and reconnects happen in
  production.
- `record_cfbenchmarks_backfill`'s `flush()` can write far more rows in one call
  than the routine 200-row `_FLUSH_BATCH` threshold this PR's fix bounds
  elsewhere — an hour-long reconnect gap backfills roughly an hour's worth of
  1Hz index ticks in one `executemany`, plausibly a *longer* blocking write than
  anything the buffer-full path produces.
- Unlike the four call sites this PR does fix, this path has **no** timeout
  backstop at all: it's a periodic loop task, not routed through
  `_process_item`'s `asyncio.wait_for(..., timeout=_HANDLER_TIMEOUT_SEC)` (see
  Confirmed-correct section below), so a slow write here cannot even be
  interrupted after 10s the way a WS message handler's would be.
- No existing tracking: `gh issue list --search "backfill flush event loop"` /
  `"last_tick_before"` / `"record_cfbenchmarks_backfill"` against this repo
  returned nothing. This is not a previously-known, deliberately-scoped-out gap
  (unlike `record_trade`/read-only functions, which the design spec explicitly
  and correctly carves out) — it's an undisclosed miss.

Suggested fix (mechanical, same design already established by this PR): these
two call sites need `flush()`'s completion *before* they proceed (a real
ordering requirement, unlike the fire-and-forget buffer-full case elsewhere in
this PR), so the correct shape is `await tick_executor.run(flush)` at the
now-`async` call site in `backfill_index()` — not `asyncio.create_task`
fire-and-forget — with `last_tick_before`/`record_cfbenchmarks_backfill`
themselves either becoming `async def` or having their `flush()` calls hoisted
into their already-`async` caller.

### Finding 2 (minor, documentation accuracy): two docstrings' "confirmed live" language overstates the evidence

`services/index_feed/ingestion.py`'s `record_cfbenchmarks` docstring: "An inline
synchronous `flush()` call here was a real, **confirmed live**
event-loop-blocking bug." `services/settlement_edge.py`'s `record_observation`
docstring: "Real live bug this fixed... (**confirmed live**: a 13-minute
app-wide stall, unrelated in-memory-only endpoints hung too)."

What is actually confirmed live, per the design spec's own root-cause section,
is: (a) a 13-minute app-wide, endpoint-agnostic stall occurred once, this
session (real, observed), and (b) the endpoint-agnostic symptom (an in-memory-
only endpoint hanging too) is evidence the event loop itself was blocked
somewhere, not any one endpoint's own I/O. What is verified independently via
source reading (and independently confirmed by me this pass) is that these
specific functions had a synchronous `flush()` call with zero await points that
mechanistically *could* produce exactly that symptom. What is **not** shown
anywhere in the PR, spec, or plan is a stack trace or profiler sample captured
during the actual 13-minute stall that pinpoints `record_cfbenchmarks` or
`record_observation` specifically, as opposed to `game_state.record`,
`series_watcher.record_book`, or (per Finding 1) the backfill path — all
candidates for the same single observed incident. `series_watcher.py`'s own
docstring for `record_book` correctly hedges this ("**plausibly** the
highest-frequency... not a minor addendum"); `game_state.py`'s docstring for
`record` makes no "confirmed live" claim at all. The two "confirmed live"
docstrings are inconsistent with that more careful framing elsewhere in the
same PR. This does not undermine the fix's correctness — the mechanism is real
and independently verified via source reading regardless of which function(s)
caused the one observed incident — but "confirmed" is stronger than the
evidence chain supports for these two sites specifically. Non-blocking; a
docstring wording fix, not a functional change.

### Finding 3 (informational, not required): fire-and-forget `create_task` calls hold no stored reference

None of the six new `asyncio.create_task(tick_executor.run(<module>.flush))`
call sites store the returned `Task` object. The asyncio stdlib documents this
as a real pitfall ("save a reference to the result... to avoid a task
disappearing mid-execution... the event loop only keeps weak references").
Two things make this low-risk here, not a required fix:
- It's consistent with existing precedent already in this codebase —
  `services/whale_stream/decision_bridge.py`'s `_broadcast_signal_decision`
  calls (lines 143/160/174/191) use the identical unreferenced
  `asyncio.create_task(...)` pattern today, unrelated to this PR.
- It's moot for exception-swallowing specifically: I read all four `flush()`
  function bodies (`index_feed/ingestion.py:291`, `settlement_edge.py:177`,
  `game_state.py:303`, `series_watcher.py:370`) and every one wraps its DB
  write in `try/except Exception`, logs to `fault_log.record(...)`, and never
  re-raises. There is no exception for a GC'd or unretrieved task to lose.
  `services/task_supervisor.py` exists specifically to catch exactly this
  failure mode for *other* call sites in this codebase (main.py's long-running
  loops) — this PR's new call sites don't use it, but given `flush()` never
  raises, wrapping them in `task_supervisor.supervise()` would add no real
  protection. Not recommending a change here.

---

## Confirmed correct, no issue (independently re-verified this pass)

- **Root-cause mechanism (Claim 1).** Read `services/kalshi/websocket.py`
  directly: `_HANDLER_TIMEOUT_SEC = 10.0` (line 187); `_process_item` (line
  1175) wraps `await self._handle_message(...)` in
  `asyncio.wait_for(..., timeout=self._handler_timeout_sec)` (lines 1192-1197);
  `_handle_message` (line 730) `await`s `on_index(msg_type, ...)` for
  `cfbenchmarks_value`/`pyth_value` and `on_ticker(...)` for `ticker` messages —
  the exact handlers this PR's fix sits behind. Confirmed: prior to this PR,
  the buffer-full-triggered `flush()` calls inside these four functions had
  zero internal `await` points, so `wait_for`'s cancellation (which can only
  interrupt at an `await` point) genuinely could not have stopped them
  mid-write. The PR's claim about *why* this bug shape defeats the 10s timeout
  is accurate.
- **Signature change + scheduling (Claim 2).** All four functions now return
  `tuple[bool, bool]`, no longer call `flush()` internally (verified by reading
  every function body, not just the diff), and every production caller
  correctly unpacks `_, should_flush = ...` and only schedules
  `asyncio.create_task(tick_executor.run(<module>.flush))` when `should_flush`
  is true.
- **Caller-site completeness (Claim 6, re-derived independently).** Re-grepped
  for every production (non-test, non-definition) call site of all four
  functions myself: `record_cfbenchmarks`/`record_pyth` — exactly
  `index_stream_handlers.py:59,64`; `record_observation` — exactly
  `index_stream_handlers.py:161`; `game_state.record` — exactly
  `event_metadata.py:215` and `live_status.py:297`; `record_book` — exactly
  `whale_stream_handlers.py:287`. No leftover caller anywhere still assumes the
  old plain-`bool` return. Every pre-existing test asserting the old return
  type was found and updated (`test_kalshi_contracts.py`, `test_game_state.py`,
  `test_series_watcher.py`, `test_settlement_edge.py`, `test_index_feed.py`).
  Also separately grepped the whole test suite for any other
  `if <module>.record...(...)`-style truthy check that a `bool`→`tuple` change
  would silently break (a tuple is always truthy, so `if gs.record(...):` would
  stop reflecting "was this accepted" the moment the signature changed) — the
  PR's own commit message calls out fixing exactly one such latent bug in
  `game_state`'s concurrent stress test; my independent grep found no other
  instance left broken.
- **`record_trade`/read-only functions untouched (Claim 4).** Confirmed via
  diff — zero changes to `record_trade` or to any read-only function
  (`funnel`, `reconcile`, `_signals_for_series`, etc.) in `series_watcher.py`.
- **`capture_writer.py` unchanged (Claim 5).** Confirmed — not present in the
  PR's file list at all.
- **`index_stream_handlers.py` coherence.** Read the full current file: one
  import line added once (`tick_executor` appended to the existing `from
  services import ...` line), both `_process_stream_index` and
  `_record_settlement_observations` correctly updated, no leftover reference to
  the old synchronous-call pattern anywhere in the file.
- **Test quality — not vacuous.** Read the four caller-site
  "schedules_flush_via_tick_executor_when_told" tests in full
  (`test_index_stream_handlers.py` x2, `test_market_watch_event_metadata.py`,
  `test_market_watch_live_status.py`, `test_whale_stream_stage_timing.py`).
  Each drives the real caller function (`_process_stream_index`,
  `_record_settlement_observations`, `_fetch_event_live_data`,
  `_fetch_live_status`, `_process_stream_ticker`) with only the immediate
  `record_*`/`flush` functions monkeypatched — not the caller itself — spies on
  the real `asyncio.create_task`/`tick_executor.run` (still executing them,
  not stubbing them into no-ops), and asserts the *exact real function object*
  (e.g. `index_feed.flush`, not just "some callable") is what gets scheduled.
  This would fail if the real integration were broken, not pass vacuously.
- **No pile-up / race risk from concurrent `create_task` scheduling.** Read
  every `flush()` body: all four share the identical
  swap-buffer-under-lock-then-`if not rows: return`-cheap-no-op shape, so
  multiple `create_task` calls landing before the first one runs cannot
  duplicate or lose rows — only the first does real I/O, the rest return
  immediately. Read `services/tick_executor.py`'s `run()` in full: a plain
  `await loop.run_in_executor(_executor, fn)` — wrapping it in `create_task`
  does not alter its execution semantics.
- **Test suite — personally run, not taken from the PR body.** Scoped run
  (the 10 files named in this review's brief): `195 passed, 0 failed` in
  16.27s. Full suite (`pytest tests/ -q -m 'not slow'`):
  **`3035 passed, 16 skipped, 2 deselected, 0 failed`** in 269.77s, exit code
  0, zero `FAILED`/`ERROR` lines in the raw log. This is a **better** result
  than the PR body's own stated test-plan claim ("3003 passed, 6 failed... 42
  skipped") — I observed zero failures, not the six pre-existing ones the PR
  body describes. This is most likely because whatever "no git binary in this
  dev container" gap the PR body cites has since been resolved in this
  environment; it is not evidence of a regression, but the PR body's specific
  numbers are now stale and should not be cited as current fact.
  `python -c 'import main'` → `IMPORT_OK`, confirming Claim 6's "import main
  clean" note.

---

## What I could not fully verify vs. what I verified directly

Directly verified this pass, against real source/tests, not taken on faith:
the `wait_for`/`_HANDLER_TIMEOUT_SEC` mechanism; every modified function's
before/after body; every production caller site (re-derived via independent
grep, not the diff alone); all four `flush()` bodies' exception handling;
`tick_executor.run()`'s implementation; the full and scoped test suites (run
myself); `import main`; the two additional inline-`flush()` sites in
`services/index_feed/ingestion.py` and their async call chain through
`backfill.py`/`main.py`.

Taken on the design spec's word, not independently re-derived: the precise
historical timeline of commits `d87fd5b`/`9b4bd80` "earlier today" and the
claim that those specifically established `tick_executor` as the accepted
scheduled-flush mechanism (plausible and consistent with what `git log` shows
at the top of this branch, but I did not diff those commits myself). This does
not affect the verdict either way.

## Process note (not a PR defect)

Mid-review, this session's Bash tool was transiently blocked by an apparent
stale worktree-isolation binding (harness-level, not `.claude/hooks/`) that
also prevented a delegated subagent from running commands; it self-resolved
without intervention. Separately, `ddev` was found stopped mid-review (most
likely a side effect of shared-checkout activity from another concurrent
session per CLAUDE.md's "parallel sessions share one primary checkout") and
was restarted from the primary root before the full test suite could run.
Neither affected the findings above — both were resolved before the test
results and source reads this report relies on were taken.

---

## Re-review of fix commit 734eca0

Independent second pass, fresh context, per CLAUDE.md's "nothing advances on
one pass" HARD RULE. Scope: verify that commit `734eca0` (on top of the
reviewed `c4402ba`) actually addresses Finding 1 above, and independently
re-run the app-wide sweep for a third undiscovered instance of the same bug
shape. Did not re-review the four original call sites (`record_cfbenchmarks`/
`record_pyth`, `settlement_edge.record_observation`, `game_state.record`,
`series_watcher.record_book`) — already approved above and untouched by this
commit (confirmed: `git diff c4402ba..734eca0` touches only
`services/index_feed/ingestion.py`, `services/index_feed/backfill.py`,
`services/settlement_edge.py`'s docstring, and two test files).

### Verdict: **Finding 1 is ADDRESSED**

### What was verified directly against `git diff c4402ba..734eca0` and current source

- **`last_tick_before` is now `async def`** (`services/index_feed/
  ingestion.py:207`), and its body is exactly `return await
  tick_executor.run(lambda: _last_tick_before_sync(index_id, before_ts))`. A
  new private `_last_tick_before_sync` (line 229) contains the original
  flush-then-`SELECT MAX(observed_at)` body verbatim (byte-for-byte identical
  to the pre-fix `last_tick_before` body per the diff — only the function
  name and docstring changed, no logic touched). This preserves the read-
  after-flush ordering guarantee: `tick_executor.run()`'s real signature,
  read in full from `services/tick_executor.py`, is `async def run(fn:
  Callable[[], T]) -> T` — a plain `await loop.run_in_executor(_executor,
  fn)`. Because `flush()` and the `SELECT` both run inside the one `fn`
  passed to `run()`, they execute back-to-back on the same worker thread as
  a single atomic unit; nothing else can interleave a write between them.
  This is a materially different (and correct) shape from the fire-and-
  forget `asyncio.create_task(tick_executor.run(flush))` pattern the other
  four sites use — those sites don't need the caller to see the flush's
  effect immediately, this one does, and the diff does not conflate the two.

- **`record_cfbenchmarks_backfill` no longer calls `flush()`.** Confirmed via
  diff: the trailing `if stored: flush()` block was replaced with `return
  stored, bool(stored)`. Signature is now `-> tuple[int, bool]`. Re-grepped
  `record_cfbenchmarks_backfill` across `services/` and `tests/` myself
  (not trusting the commit message): its only production call site anywhere
  in the repo is `services/index_feed/backfill.py:202`, which correctly
  unpacks `stored, should_flush = ingestion.record_cfbenchmarks_backfill(...)`
  and only schedules a flush when `should_flush` is true. `tests/
  test_index_feed.py` was updated at all three of its call sites
  (`test_record_cfbenchmarks_backfill_stores_rows_distinguishable_by_source`,
  `..._never_regresses_latest_backward`, `..._never_raises_on_garbage`) to
  the new `(stored, should_flush)` tuple contract — no leftover assertion
  against the old plain-`int` return anywhere.

- **`backfill_index()` now `await`s the async `last_tick_before`** (`start_ts
  = await ingestion.last_tick_before(index_id, reconnect_at)`, was a plain
  sync call before) **and schedules the backfill flush fire-and-forget**:
  `if should_flush: asyncio.create_task(tick_executor.run(ingestion.flush))`
  — not awaited directly, consistent with the reasoning the other four sites
  already established (the caller shouldn't block on the flush finishing).
  This is the correct split: the ordering-sensitive read (`last_tick_before`)
  is awaited synchronously because a stale read is a real correctness bug;
  the ordering-insensitive persistence flush (`record_cfbenchmarks_backfill`'s)
  is fire-and-forget because nothing downstream in this same call depends on
  it having landed yet.

- **Two new tests in `tests/test_index_feed_backfill.py`, read in full, are
  not vacuous.** `test_backfill_index_schedules_flush_via_tick_executor_
  when_points_are_stored` spies on the real `asyncio.create_task` and the
  real `tick_executor.run` (wrapping and still calling through to the real
  implementations, not stubbing them into no-ops) and drives the actual
  `backfill.backfill_index()` coroutine end to end with a fake
  `fetch_history` returning one point. It correctly accounts for the two-
  call structure the diff produces: `tick_executor_calls` has length 2
  (`last_tick_before`'s internal flush-then-read call first, then
  `backfill_index`'s own scheduled flush second), asserts
  `tick_executor_calls[1] is ingestion.flush` (the *second* call, exactly as
  the task brief anticipated), and asserts `len(scheduled) == 1` — i.e. only
  the backfill flush went through `create_task`, not the internally-awaited
  `last_tick_before` call. This would fail if the fix's call graph were
  wired any other way (e.g. if `last_tick_before` were also wrapped in
  `create_task`, or if the backfill flush were awaited directly instead of
  scheduled) — it is a real integration assertion, not a vacuous one. The
  companion `test_backfill_index_schedules_no_flush_when_nothing_was_stored`
  confirms `should_flush=False` (empty `fetch_history` result) produces zero
  `create_task` calls, correctly distinguishing "nothing stored" from "stored
  but not yet flushed." Both tests pass (see run below).

- **`tick_executor.run`'s signature matches every new call site's usage** —
  read the current file in full: `async def run(fn: Callable[[], T]) -> T`,
  a single no-arg callable in, awaited result out. `last_tick_before`'s
  `lambda: _last_tick_before_sync(index_id, before_ts)` and
  `backfill_index`'s bare `ingestion.flush` (already a zero-arg callable)
  both satisfy this correctly.

- **The two "confirmed live" docstring overclaims are corrected honestly.**
  `services/index_feed/ingestion.py`'s `record_cfbenchmarks` docstring now
  reads "a real, plausible contributor to a confirmed live event-loop stall
  ... no stack trace pinpointed this exact call site during that stall
  (py-spy couldn't attach, ptrace blocked), so this is source-level
  inference from a reproduced symptom, not a directly observed cause."
  `services/settlement_edge.py`'s `record_observation` docstring was changed
  the same way, same wording pattern. Both match almost verbatim the
  language the original review's Finding 2 suggested ("plausible
  contributor... no stack trace pinpointed this exact call site... source-
  level inference, not a directly observed cause") and both now correctly
  distinguish "the 13-minute stall happened, and this mechanism could
  produce that symptom" (verified) from "this exact call site caused that
  exact stall" (not verified, not claimed anymore). Honest correction, no
  overstatement remaining in either docstring.

### Independent app-wide sweep for a third undiscovered instance

Re-ran the sweep independently rather than trusting the commit message's
"both fixed" claim: `grep -rn "^\s*flush()\s*$" services/` (the literal-call
pattern the original review used to find Findings 1's two sites) now returns
exactly **one** hit app-wide — `services/index_feed/ingestion.py:232`,
which is inside the new `_last_tick_before_sync` helper itself (the intended,
correctly-contained synchronous body that only ever runs on a
`tick_executor` worker thread, never directly on the event loop). No other
bare `flush()` call remains anywhere in `services/`.

Went broader than that one grep, per the brief: enumerated every `def
flush(...)`/`def flush_now(...)`-shaped function in `services/`
(`series_watcher.flush`, `game_state.flush`, `settlement_edge.flush`,
`index_feed/ingestion.flush`, `capture_writer.flush_now`/`_flush_store`) and
manually traced every production caller of each:

- `series_watcher.flush()`, `index_feed.flush()`, `settlement_edge.flush()`,
  `game_state.flush()` — every call site is `main.py`'s
  `_flush_trade_capture`/`_flush_secondary_capture_stores`, both plain sync
  functions invoked *only* through their `_async` wrappers
  (`_flush_trade_capture_async`/`_flush_secondary_capture_stores_async`),
  both of which route through `await tick_executor.run(...)` before being
  awaited from the main tick loop. Already-fixed, already-established
  pattern (commits `d87fd5b`/`9b4bd80` per this branch's own log) — no new
  finding here.
- `capture_writer.submit()`'s daemon-thread path — unchanged, out of scope
  per the brief's own exclusion.
- `capture_writer.flush_now()` — **this one is genuinely different from
  `submit()`**: its own docstring states it "runs on its CALLER's thread,"
  i.e. it is a real synchronous call, not backed by the daemon thread.
  Traced every caller: `candidate_log.py`'s `resolve_from_market_results`,
  `gate_summary`, `population_gate_summary`, `clear_all`, `count_range`,
  `clear_range` all call it inline. Two different reachability stories:
  - `resolve_from_market_results` — its only sync-context caller
    (`main.py`'s `_resolve_and_record_settlements`) is itself only ever
    invoked through `_resolve_and_record_settlements_async` →
    `await tick_executor.run(...)`, and its other caller
    (`services/settlement_resolver.py`'s `_resolve_one_sync`) is likewise
    only invoked via `await tick_executor.run(lambda: _resolve_one_sync(...))`
    inside `run_pending`. Both already correctly offloaded — no finding.
  - `gate_summary`/`count_range`/`clear_range`/`clear_all` — these **are**
    reachable synchronously with no `await`/offload from `async def` code:
    `main.py`'s `_maybe_run_auto_apply` (a plain `def`, called directly —
    `trigger(config_store.get())`, no await — from inside `async def
    _scheduler_loop`) calls `candidate_log.gate_summary()` inline, and
    `services/reset/routes.py`'s `async def reset_preview`/`reset_broker`
    admin routes call `candidate_log.count_range`/`clear_range`/`clear_all`
    directly with no executor offload either.

**This is real, but I am not treating it as a third instance of Finding 1**,
for reasons the original review's own severity framing supports: (a) it is
pre-existing and entirely outside this PR's diff — none of `candidate_log.py`,
`main.py`'s scheduler, or `reset/routes.py` are touched by `c4402ba` or
`734eca0`; (b) unlike the two sites Finding 1 flagged, `_maybe_run_auto_apply`
**discloses its own blocking profile in its own docstring** ("Still
inline-when-due here (same blocking profile as before, once every several
hours); offloading the due-time work itself via tick_executor is a
follow-up, not part of this pure relocation") — the defining problem with
Finding 1's two sites was that they were *undisclosed* anywhere, not merely
that blocking existed; (c) frequency is far lower than the WS-reconnect-
triggered backfill path Finding 1 fixed — `_maybe_run_auto_apply`'s own two
gates are `snapshot_interval_sec` (21600s/6h) and `auto_apply_cooldown_sec`
(86400s/1d), and the `reset/routes.py` paths are human-triggered, rare
Danger-Zone admin actions, not a reconnect-driven hot path; (d) it is a
different bug shape in one respect worth naming precisely — `flush_now()`
runs the write on the *caller's own thread* by design (per its own
docstring), not via a scheduled background daemon, which is exactly why it
blocks the event loop when its caller is itself on the event loop with no
offload; this is mechanistically the same defect class as Finding 1
(synchronous SQLite I/O with no `await` point on the event loop) but through
a distinct code path (`flush_now`, not `flush`) that neither this PR nor the
prior review scoped in. Disposition per CLAUDE.md's investigation-to-guard
requirement: **out-of-scope for this PR, worth a separate follow-up item**,
not a blocker on `734eca0`'s merge — flagging here rather than silently
dropping it, since it is a real, verified gap of the same mechanistic class
CLAUDE.md's data-plane HARD RULE cares about, just not the one this PR set
out to fix.

### Test results (personally run against this checkout)

Scoped run (the three files named in the task brief):
```
tests/test_index_feed.py tests/test_index_feed_backfill.py tests/test_settlement_edge.py
63 passed in 7.03s
```
Zero failures, zero errors, zero skips.

Full suite (`pytest tests/ -q -m 'not slow'`), run to completion myself:
```
3037 passed, 16 skipped, 2 deselected, 1 warning in 289.50s (0:04:49)
```
Exit code 0, zero `FAILED`/`ERROR` lines. Matches the commit message's own
"3037 passed, 0 failed" claim exactly — independently confirmed, not taken
on faith.

### What I verified directly vs. took on the commit message's word

Directly verified: the full `git diff c4402ba..734eca0`; `last_tick_before`'s
new body and its `_last_tick_before_sync` helper; `tick_executor.run`'s real
signature; every production call site of `record_cfbenchmarks_backfill` and
`last_tick_before` (independent grep, not the diff alone); both corrected
docstrings' actual current text; the two new tests' full bodies and what they
actually assert; the scoped test run (executed myself); an independent
app-wide re-sweep for `flush()`/`flush_now()` reachability beyond the two
sites Finding 1 named.

Not independently re-verified: nothing left outstanding — the full-suite run
completed (see above) and matches the commit message's own figure exactly.

**Addendum (orchestrating session, same day):** at the time this addendum was
first written, the re-reviewer's full-suite run had not yet completed. It has
since completed (see "Test results" above: `3037 passed, 16 skipped, 2
deselected, 1 warning in 289.50s`, zero failures) and agrees with the
orchestrating session's own independent full-suite run, taken right after
committing `734eca0` (before this re-review was dispatched): `3037 passed,
16 skipped, 2 deselected, 1 warning in 242.69s` - also zero failures. Two
independent full-suite runs, same pass/skip/deselect counts, zero failures
either time. `capture_writer.flush_now()`'s undisclosed-blocking gap
(candidate_log.py's gate_summary/count_range/clear_range/clear_all,
reachable from main.py's _maybe_run_auto_apply and services/reset/routes.py's
admin routes with no executor offload) filed as its own tracked follow-up
rather than folded into this PR - out of scope per this section's own
reasoning above.
