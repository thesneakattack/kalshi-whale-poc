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
