# Adversarial Review — Persistence Layer Implementation Plan

## Status

Independent adversarial review per CLAUDE.md's "nothing advances on one pass" HARD RULE,
applied to `docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-persistence-layer-implementation.md (moved there 2026-09-06, planning-lanes migration)` (commit
`137d57d`, worktree `worktree-agent-ae61311892d35be18`). Fresh Agent call, no memory of the
session that wrote the plan or its self-review. Per the HARD RULE's own definition of this
stage: every load-bearing claim below was re-derived from primary sources (live source reads,
a live Python 3.13.15 check inside `ddev-kalshi-whale-poc-fastapi`, `grep`/arithmetic
recomputation) — never taken from the plan's own tables, prose, or self-review.

## Method

For each of the plan's ten tasks: read the exact current source at the file:line ranges the
task cites, confirmed the "before" transcription is byte-accurate, confirmed the "after" diff
preserves every existing DDL/index/column/call-site contract, and independently re-ran every
piece of arithmetic (module counts, call-site counts, busy-timeout numbers, tick cadence) the
plan asserts rather than trusting its stated conclusion. Specifically checked, against live
source and a live Python probe inside the actual container:

- `services/fault_log.py`'s `_write()`/`ON CONFLICT` clause (the plan's central self-flagged
  correction to the design).
- `services/candidate_log.py`, `services/series_watcher.py`,
  `services/observability/observability.py`'s current `_connect()` bodies, line-for-line
  against Tasks 3/5/6's "before" quotations and "after" diffs.
- `services/capture_writer.py`'s `_flush_store`/`flush_now`/`_run`, including the
  shutdown-fast-path branch (`_stop_event.is_set()`), against Task 2's diff.
- `services/whale_stream/decision_bridge.py`, `services/candidate_ledger.py`,
  `services/tick_executor.py`'s thread-pool sizing, and every caller of
  `candidate_log.resolve_from_market_results()` repo-wide (not just the ones the plan names).
- `main.py`'s `_tick_interval_sec()`/`_settlement_resolver_loop()`/`trading_loop()` wiring.
- The exact existing test-file import/alias conventions in `tests/test_capture_writer.py`,
  `tests/test_candidate_log.py`, `tests/test_series_watcher.py`, `tests/test_observability.py`
  the plan's new tests are meant to slot into.
- `sqlite3.OperationalError`'s actual `sqlite_errorcode` attribute behavior when raised
  manually in Python vs. raised by the C extension, live, inside the real container
  (Python 3.13.15, matching the design's own cited version).
- Recomputed the 30→22 module-count arithmetic, the 18-net-new-test count, the "six
  call sites"/"four functions" count, and the "28 call sites" `flush_now()` figure by direct
  `grep`, not by re-deriving from the plan's own summary.

---

## Findings

### F1 — CONFIRMED: the `context`-vs-`operation` correction to the design's falsifier is exactly right

Read `services/fault_log.py:125-144` directly. `_write()`'s `INSERT ... ON CONFLICT
(component, operation, exc_type, message) DO UPDATE SET count = count + 1, last_seen =
excluded.last_seen` — `context` is not part of the `UNIQUE` constraint and is never touched by
`DO UPDATE SET`. The plan's claim that putting caller identity in `context` (the design's own
suggested implementation) "would not work" — the field stays frozen at whatever the first
occurrence recorded — is verified true from source, not from the design's or the plan's own
prose. Folding caller identity into `operation` instead (`flush_retained_on_lock_daemon` /
`flush_retained_on_lock_flush_now`) is a correct fix for the stated problem, since `operation`
**is** part of the unique key.

### F2 — CONFIRMED: Tasks 3/5/6 preserve every DDL/index/column-add call; no silent regression

Read all three "before" functions end-to-end and diffed them against each task's proposed
"after":

- `services/candidate_log.py:76-145` — exactly two `CREATE TABLE`, two `CREATE INDEX`
  (`idx_rejection_events_gate`, `idx_rejection_events_unresolved`), two
  `_add_column_if_missing(..., "unit_cost", "REAL")` calls. Task 3's rewrite runs all four
  non-table-DDL statements on the yielded connection and its own test
  (`test_connect_still_creates_both_tables_indexes_and_unit_cost_columns`) asserts on all of
  them by name. Matches.
- `services/series_watcher.py:151-207` — two `CREATE TABLE`, four `CREATE INDEX`
  (`idx_raw_trades_series`, `idx_raw_trades_ticker`, `idx_book_ticker`, `idx_book_series`), zero
  `add_column_if_missing` calls. Task 5's rewrite and its test match exactly.
- `services/observability/observability.py:41-59` — one `CREATE TABLE`
  (`metric_samples`), one `CREATE INDEX` (`idx_metric_samples_metric_time`), zero
  `add_column_if_missing` calls. Task 6's rewrite and test match exactly.

All three tasks' cited line ranges, call-site line numbers (`candidate_log.py`: 239, 287, 387,
439, 461, 481; `series_watcher.py`: 458, 484; `observability.py`: 66, 80, 89, 104, 124, 130)
were independently re-derived via `grep -n "_connect("` and matched the plan's citations
exactly, including the `import contextlib`/import-order details (`db` alphabetically before
`fault_log` in `observability.py`'s existing multi-line import — confirmed).

### F3 — CONFIRMED: trading-critical blast-radius claim holds

`grep -n "candidate_log" services/whale_stream/decision_bridge.py` → zero hits, confirmed.
`decision_bridge.py:66,129` call `candidate_ledger.claim(...)`/`candidate_ledger.record_decision(...)`
via `tick_executor.run(...)`, exactly as cited. `services/candidate_ledger.py` and
`services/tick_executor.py` are not touched by any task's diff (checked every task's "Files"
section). The plan's claim that `candidate_log.py` is off the order-placement path is correct
— it is a rejection-observability/analytics store, never consulted by the decision path.

### F4 — CONFIRMED: Task 7's "22 modules" recount is exact

Recomputed independently: design's 30-module `_connect()` list minus Tier 0's 5
(`market_history.py`, `title_cache.py`, `market_catalog/market_catalog.py`, `signal_log.py`,
`fault_log.py`) minus this plan's 3 direct migrations (`candidate_log.py`,
`series_watcher.py`, `observability/observability.py`) = 22, and the resulting set matches
Task 7's named list element-for-element (verified via a scripted set-difference, zero
mismatches either direction).

### F5 — CONFIRMED: the tick-cadence figure (30s) and `PRAGMA integrity_check(N)` syntax

`main.py:648-665`'s `_tick_interval_sec()` returns `kalshi_cfg["safety_net_interval_sec"]`
when `_streaming_trade_tape_enabled()` is true; `config/settings.yaml:44` sets
`safety_net_interval_sec: 30`. `PRAGMA integrity_check(5)` (Task 9's bounded form) is valid
SQLite syntax, confirmed by executing it against a scratch file inside the container.

### F6 — FALSIFIED (MUST-FIX): Task 2's falsifier test does not exercise the code path it is named for

Task 2's `test_flush_retained_on_lock_records_caller_in_operation_name` simulates a lock error
by monkeypatching `sqlite3.connect` to raise `sqlite3.OperationalError("database is locked")`
directly in Python. Verified live, inside `ddev-kalshi-whale-poc-fastapi` (Python 3.13.15):

```
>>> exc = sqlite3.OperationalError('database is locked')
>>> hasattr(exc, 'sqlite_errorcode')
False
```

`services/capture_writer.py:312-319`'s `_is_lock_error(exc)` gates exclusively on
`getattr(exc, "sqlite_errorcode", None)` — an attribute the real `sqlite3` C extension sets
when it raises the error itself, but which a manually-constructed `OperationalError` never has.
`_is_lock_error()` therefore returns `False` for Task 2's mock, meaning `_flush_store()` takes
the **generic non-lock exception branch** (`fault_log.record("capture_writer", "flush", exc,
context=store)`), not the `flush_retained_on_lock_{caller}` branch the test's assertion
expects. The test would fail — both before Task 2's Step 3 fix (its stated "expected: fails"
outcome, but for the wrong reason: `recorded == ["flush", "flush"]`, not a missing-parameter
`TypeError`) and, more seriously, **after** the fix lands too, since the exception it raises
never reaches the branch the fix touches at all.

This repo already has the correct pattern for this exact simulation, in the same file the new
test is being added to: `tests/test_capture_writer.py:277-290`'s `_hold_write_lock()`/
`_release()` helpers open a real second `sqlite3` connection with `BEGIN IMMEDIATE`, so a
genuine `SQLITE_BUSY` comes from the C extension itself (with `sqlite_errorcode` correctly
populated) — exactly the mechanism four other tests in this same file already use to test
`_is_lock_error`-gated behavior (`test_lock_collision_retains_the_batch_instead_of_dropping_it`
and its three neighbors). Task 2's Step 1 instructs the implementer to check this file's
existing pattern before writing the test ("to match its monkeypatch/lock-simulation pattern")
but the actual code that follows does not use it.

**Impact:** low blast radius (test-only, additive, no production code path affected), but
this is exactly the kind of gap TDD is supposed to prevent — a "Step 1: write the failing
test" that fails for a different reason than intended can mislead an implementer into
believing the fix works once the test happens to go green after some unrelated tweak. Must be
rewritten using the `_hold_write_lock`/`_release` pattern (or an equivalent that actually sets
`sqlite_errorcode`) before this task is implemented.

### F7 — FALSIFIED (MUST-FIX): Task 2 and Task 6's new tests reference aliases that do not exist in their target files

- `tests/test_capture_writer.py` has **no module-level `capture_writer` or `cw` import** —
  confirmed by reading the file's full import block (`import sqlite3`, `import time`, nothing
  else) and by grepping for `cw` (zero hits outside comments). Every one of the file's ~25
  existing tests does its own local `from services import capture_writer` inside the function
  body. Task 2's three new tests reference a bare `cw` throughout (`cw._STORE_PATHS`,
  `cw._buffers`, `cw.fault_log`, `cw.sqlite3`, `cw._flush_store`,
  `cw._CALLER_BUSY_TIMEOUT_MS`) with no local import added — as literally written, this raises
  `NameError: name 'cw' is not defined` at collection/run time, independent of finding F6.
- `tests/test_observability.py` imports the module under its full name —
  `from services.observability import observability` (line 33) — with no `obs` alias anywhere
  in the file. Task 6's two new tests reference a bare `obs` throughout
  (`obs.DB_PATH`, `obs.db.sqlite3`, `obs._connect`) — same `NameError` failure mode.

By contrast, Task 3's and Task 5's new tests correctly use `cl`/`sw`, which **do** already
exist as module-level aliases in `tests/test_candidate_log.py`
(`from services import candidate_log as cl`) and `tests/test_series_watcher.py`
(`from services import series_watcher as sw`) respectively — verified by direct grep. So this
is not a systemic flaw in the plan's approach, but a concrete, reproducible defect specific to
the two tasks (2 and 6) whose target test files happen to use a different existing convention
than the plan's boilerplate assumed. Both are one-line fixes (add the missing aliased import,
or rewrite the new tests to use each file's own existing full-name/local-import convention)
but as currently drafted, four of the plan's 18 net-new tests (the 3 in Task 2, the 2 in Task
6 — 5, not 4, but F6 already invalidates one of Task 2's three independent of this) would not
run.

### F8 — GAP (SHOULD-FIX, non-blocking): the "two independent writers" model undercounts a real, tighter-cadence, thread-concurrent third caller

Traced every caller of `candidate_log.resolve_from_market_results()` repo-wide (the design's
§4.2 and the plan's Task 4 both frame this as "called once per ~30-second tick," singular).
There are in fact **two independent call sites**, not one:

1. `main.py:383`, inside `_resolve_and_record_settlements()`, reached via
   `_resolve_and_record_settlements_async()` → `tick_executor.run(...)` from `trading_loop()`
   (`main.py:1306`'s own supervised task) — the ~30-second-tick path both documents describe.
2. `services/settlement_resolver.py:179`, inside `_resolve_one_sync()` (its own docstring:
   "Runs on the tick executor's worker thread"), reached via `run_pending()` →
   `_settlement_resolver_loop()` — a **separately supervised** asyncio task
   (`main.py:1331`), polling every `_SCHEDULER_TRIGGER_INTERVAL_SEC` = **5.0 seconds**
   whenever `settlement_resolver.snapshot()["pending"] > 0` (`main.py:706-731`).

Both paths dispatch through the **same** `tick_executor._executor`, a
`ThreadPoolExecutor(max_workers=2)` (`services/tick_executor.py:80`) — so these two calls to
the identical function can genuinely execute on two different OS threads at the same time, not
merely at staggered wall-clock moments. `settlement_resolver.py`'s own module docstring states
settlements "cascade at boundary times (hourly/15-minute series settle together)" — i.e., this
5-second-cadence caller is likely to **burst**, correlated with real settlement activity,
rather than fire at a steady rate. This is a materially different collision shape than the
design's §4.2 characterization ("an occasional near-simultaneous overlap between two
independently-scheduled timers with a short window... not a saturated resource").

This does not undermine the chosen fix direction — widening the busy-timeout budget (Task 4)
helps regardless of which caller is contending, and 1000ms remains generous against either a
5s or 30s cadence. But two things the plan states as settled should be corrected:

- The "why this number" justification in Task 4 ("`resolve_from_market_results()` runs once
  per ~30-second tick... not a request-latency-sensitive path") is factually incomplete — it
  can also run every 5 seconds under real settlement load, on a separate thread.
- Task 2's falsifier taxonomy (`caller="daemon"` vs. `caller="flush_now"`) **cannot
  distinguish** a main-tick-driven collision from a settlement-resolver-driven one — both
  collapse into `caller="flush_now"` since both reach `capture_writer.flush_now()` identically.
  If Task 10's re-measurement (Step 4) still shows a non-trivial `flush_retained_on_lock_flush_now`
  rate after Tasks 3-4 land, the plan's own diagnostic apparatus will not be able to tell
  whether that's the (already-widened) main-tick caller or the settlement-resolver caller
  without further instrumentation — worth naming as a known limitation now rather than
  discovering it after Task 10.

### F9 — OVERSTATED (SHOULD-FIX, cosmetic): Task 4's blast-radius text miscounts by one

Task 4 states: "candidate_log.py has **six** call sites of capture_writer.flush_now()
(lines 236-237, 286, 386, 437-438, 458-459, 478-479...) Only the two inside
resolve_from_market_results() ... are touched by this task. **The other four functions**
(gate_summary, population_gate_summary, clear_all, count_range, clear_range)..." Five function
names are listed, and `grep -n "^def "` against `services/candidate_log.py` confirms these are
five distinct functions (`gate_summary:270`, `population_gate_summary:330`, `clear_all:424`,
`count_range:444`, `clear_range:467`). Six call-site locations minus the one touched
(236-237) leaves five, not four. The actual code change is unaffected (Task 4's diff correctly
touches only lines 236-237), but the prose miscounts the very blast-radius enumeration whose
entire purpose is to make the "untouched" set precise and checkable.

### F10 — OVERSTATED (SHOULD-FIX, cosmetic): the "28 call sites total" figure for `flush_now(` does not match a direct recount

Task 2's Interfaces section cites "28 call sites total, zero pass a second positional
argument" for `grep -rn "flush_now(" services/ main.py tests/`. A direct recount (excluding
the `def flush_now` definition line and pure comment/docstring mentions) finds **34** real
call sites across `services/candidate_log.py` (10), `tests/test_candidate_log.py` (2),
`tests/test_capture_writer.py` (19), `tests/test_main_tick_executor_wiring.py` (1),
`tests/test_series_watcher.py` (1), and `tests/test_whale_candidate_lifecycle.py` (1). The
substantive safety claim — zero call sites pass a second positional argument — was
independently re-verified and **is** true (a targeted grep for a comma before the closing
paren on every one of those lines returns nothing), so Task 2's additive-signature-change
safety conclusion still holds regardless of the exact count. Only the cited number is wrong;
worth a quick re-grep-and-correct before merge per the dimensional-analysis HARD RULE's
"any numeric derivation gets checked" standard.

### F11 — GAP (SHOULD-FIX, judgment call): Task 7's dedicated-PR exclusion list may be one or two modules short

Task 7 names `risk_manager.py`, `paper_broker.py`, and `candidate_ledger.py` as the only three
of the 22 tracked modules requiring their own dedicated, never-bundled PR. Read the module
docstrings of four other candidates in the 22-module list to check for omissions:
`settlement_edge.py` (a read-only settlement-vs-market-price analytics module — no order
involvement, reasonable to leave in the general bucket), `shadow_mode.py` (explicitly "only
ever logs an intended trade — never calls kalshi_account_client.create_order," reasonable to
leave in the general bucket), `series_evaluator.py` (governs whether a series is
re-admitted/removed from the automatic watchlist that whale-signal generation draws
candidates from — a step upstream of, but feeding into, live signal generation), and
`trade_category.py` (writes once per position **open**, i.e., participates in the live
position-opening sequence, even though it only records metadata rather than gating the
decision). The latter two sit measurably closer to the live trading path than the other ~17
modules in the general opportunistic bucket, though clearly less central than the three
already named (they don't gate or execute a trade the way `candidate_ledger.claim()` or
`risk_manager`'s kill switch do). Not a defect in what Task 7 does today (it ships zero
migration code, only a tracking issue), but worth a note in the tracking issue's own body so a
future picker-upper applies the same one-dedicated-PR caution to these two, rather than
silently bundling them as "routine."

### F12 — GAP (folds into F8, SHOULD-FIX): the plan's own caller enumeration for `candidate_log.py` is incomplete

The Global Constraints section states `candidate_log.py`'s callers are "`services/
strategy_engine.py`/`services/whale_stream/*_handlers.py`'s rejection recording path... and
`main.py`'s tick loop calling `resolve_from_market_results()`." A full repo-wide grep for
`candidate_log\.` shows the rejection-recording callers are actually
`services/strategy_engine.py`, `services/kalshi/websocket.py`, and
`services/whalewatchers/kalshi_trade_tape.py` (not literally under `whale_stream/`, and
`services/whale_stream/whale_stream_handlers.py` is a distinct fourth file also in this set),
and — per F8 — `services/settlement_resolver.py` also calls `resolve_from_market_results()`
directly, independent of `main.py`'s tick loop. The underlying safety conclusion ("neither is
the order-placement path") holds for all of these once checked individually, so this is a
citation-completeness issue rather than a wrong conclusion, but the plan's own text should
name the actual files rather than a glob that doesn't match any real path in this repo.

### F13 — CONFIRMED: Task 1's tests genuinely verify `.close()`, not merely the context-manager shape

`test_connect_closes_on_normal_exit` and `test_connect_closes_even_on_exception` both
monkeypatch `sqlite3.connect` to wrap the real connection's `.close` method and record whether
it was actually invoked, then assert `closed == [True]` — this is a real behavioral check, not
a check that `db.connect()` merely returns something usable in a `with` block. Matches the
design's own §1.4 API shape verbatim (the transcription in Task 1 Step 2 is byte-identical to
the design document's §1.4 code block, confirmed by direct comparison). The DDL-idempotency
and WAL/busy-timeout-pragma tests are similarly real assertions against live `sqlite3` state,
not mocks.

### F14 — CONFIRMED: net-new test count and TDD ordering

Task 1 (6) + Task 2 (3) + Task 3 (3) + Task 4 (2) + Task 5 (2) + Task 6 (2) = 18, matching
Task 10 Step 1's claim exactly. Every one of Tasks 1–6 follows the same four-step shape (write
failing test → read exact current source → implement → confirm pass), satisfying
`superpowers:test-driven-development`'s test-first ordering structurally. The falsifier
(Task 2) is correctly sequenced before the fix it measures (Tasks 3–4), which is the right
call independent of F6/F7's defects in Task 2's specific test code — the *ordering* logic is
sound even though two of its three tests need rewriting.

### F15 — CONFIRMED: item 28 (small-file consolidation) is not silently reintroduced

No task references merging any `data/*.db` files; Task 7's tracking issue is about migrating
`_connect()` bodies to a shared *module*, not merging files, and the plan's own prose (§
"Design decisions this plan implements," item 5) states the decline explicitly. Confirmed by
reading every task's "Files" section — none touches more than one existing `.db` file's own
schema, and none proposes a new shared file.

---

## Verdict: **GO-AFTER-FIXES**

The plan correctly translates all seven of the design's top-level decisions into disjoint,
appropriately-scoped tasks. The three code migrations (Tasks 3, 5, 6) are faithful,
complete, and correctly extend the design's own oversimplified illustrative example without
dropping any index, column, or DDL statement — independently re-verified line-for-line
against current source, not trusted from the plan's own tables. The trading-critical blast
radius claims (decision_bridge.py, candidate_ledger.py, tick_executor.py untouched;
candidate_log.py off the order-placement path) are all independently confirmed accurate. The
22-module tracking list arithmetic is exact. Task ordering and TDD structure are sound.

The defects found are real but narrow and mechanical: two of Task 2's three new tests
(F6, and the `cw` alias half of F7) and both of Task 6's new tests (the `obs` alias half of
F7) will not run as literally written, and the mechanism narrative underlying Task 4's chosen
number is incomplete in a way that also weakens Task 2's falsifier's diagnostic power (F8).
None of these require redesigning any task — they are fixes to the plan document's own code
snippets and prose, not new design work — but per this repo's TDD/never-guess standards they
must be corrected before implementation starts, not discovered mid-implementation.

### Must-fix (3)

1. **F6** — Rewrite Task 2's `test_flush_retained_on_lock_records_caller_in_operation_name` to
   simulate a real lock error via the file's own established `_hold_write_lock()`/`_release()`
   pattern (or another mechanism that actually sets `sqlite_errorcode`), not a manually-raised
   `sqlite3.OperationalError`, which `_is_lock_error()` does not recognize as a lock error.
2. **F7a** — Add a module-level `from services import capture_writer as cw` (or rewrite the
   three new tests to use `tests/test_capture_writer.py`'s existing per-test local-import
   convention) before Task 2's new tests reference `cw`.
3. **F7b** — Add a module-level alias (`from services.observability import observability as
   obs`, or rewrite the two new tests to use the file's existing full `observability.` name)
   before Task 6's new tests reference `obs`.

### Should-fix (5)

1. **F8/F12** — Correct Task 4's "runs once per ~30-second tick" framing to acknowledge
   `services/settlement_resolver.py`'s independent, up-to-5-second-cadence,
   `tick_executor`-thread-concurrent call to the same `resolve_from_market_results()`
   function, and note in Task 2/10 that the `daemon`/`flush_now` taxonomy cannot distinguish
   this caller from the main-tick caller — a real limitation on Task 10 Step 4's diagnostic
   value if the occurrence rate doesn't drop as expected.
2. **F9** — Fix Task 4's "the other four functions" to "five" (`gate_summary`,
   `population_gate_summary`, `clear_all`, `count_range`, `clear_range`).
3. **F10** — Re-grep and correct Task 2's "28 call sites total" figure (a direct recount finds
   34); the "zero pass a second positional argument" conclusion is independently confirmed
   true and needs no change.
4. **F11** — Add `series_evaluator.py` and `trade_category.py` to Task 7's tracking-issue body
   as modules that, while not as central as `risk_manager.py`/`paper_broker.py`/
   `candidate_ledger.py`, sit closer to the live trading path than the rest of the 22 and
   warrant the same one-dedicated-PR caution when picked up.
5. **F12** — Correct the Global Constraints section's `candidate_log.py` caller citation from
   the non-matching glob `services/whale_stream/*_handlers.py` to the actual files
   (`services/kalshi/websocket.py`, `services/whalewatchers/kalshi_trade_tape.py`,
   `services/whale_stream/whale_stream_handlers.py`, `services/strategy_engine.py`), and add
   `services/settlement_resolver.py` as a caller of `resolve_from_market_results()`.

None of the must-fix or should-fix items touch this plan's actual design decisions (which fix
direction to take, which module migrates first, hand-rolled vs. SQLAlchemy, etc.) — all are
corrections to the plan document's own code snippets or prose accuracy. Once the three
must-fix items are applied and re-checked against real test collection (not just re-read),
this plan is ready for consolidation.
