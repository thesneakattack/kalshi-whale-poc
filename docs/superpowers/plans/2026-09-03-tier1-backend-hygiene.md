# Tier 1 Backend Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Work the second-pass architecture audit's Tier 1 (days, no new
infrastructure) items 7-14 — stall attribution, dashboard de-polling, three
safety-adjacent DRY fixes, one more event-loop-blocking write, the
config-comment-wipe mechanism, unbounded-`limit` routes plus two
expensive-scan reads, `/api/state`'s oversized field and dead ETag, and
two GC/timeout hygiene items. Renumbered sequentially below as Task 1-8;
the audit's own "7-14" numbering is cited inline where it helps trace a
claim back to its source.

**Research basis:**
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`
§8 "Revised prioritized plan," Tier 1 (items 7-14), and the sections it
cites: §3.3 (dashboard polling), §4.2 (`/api/health/faults` `hours=`
mislabel — not this plan's scope, background only), §4.3 (unattributed
stalls), §4.4 (`config/settings.yaml` comment-wipe mechanism), §4.5
(`record_snapshot_from_ticker` on the loop), §6.3 (hand-rolled-vs-proven
re-score), §8.2/§8.3 (n/a — the second audit has no numbered §8.2/§8.3
subsections; the two DRY-fix citations actually needed,
`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-
considerations.md` §9.1 and its own Tier-1 items #3/#4, are read directly
below — see Task 3's and Task 6's own headers for why the citation moved).
Tier 0 (`docs/superpowers/plans/2026-09-03-tier0-live-incident-
remediation.md`, PR #441, **plan merged, its code not yet implemented** —
verified by `grep -n '_connect' services/market_history.py`, `services/
fault_log.py` etc. showing plain `def _connect() -> sqlite3.Connection:`,
no `@contextlib.contextmanager`, immediately before drafting this plan,
2026-09-03) is out of this plan's scope; where a task below touches one of
Tier 0's five target files, that is noted and the diff is written against
the CURRENT (un-fixed) source, not Tier 0's planned end state.

**Live re-verification done immediately before drafting this plan
(2026-09-03), not assumed from either audit's own numbers:**

- `services/market_history.py:86`, `services/title_cache.py:56`,
  `services/market_catalog/market_catalog.py:65`, `services/signal_log.py:150`,
  `services/fault_log.py:58` — all five still `def _connect(...) ->
  sqlite3.Connection:`, no `@contextlib.contextmanager`. Tier 0's plan is
  merged as a document; its code is not live. Task 4 below (Tier 1 item 10,
  `record_snapshot_from_ticker`) touches `market_history.py` but not its
  `_connect()` — confirmed no overlap with Tier 0's eventual diff there.
- `services/diagnostics/routes.py` has no `STORE_PROBE_TIMEOUT_SEC`,
  `_bounded`, or `open_fds` — Tier 0's Task 1/9 code is likewise not live.
  Nothing in this plan touches that file.
- The three `CLAUDE.md` rules the second audit's own §2 says PR #429 removed
  ("schema changes are additive only," "one SQLite file per concern,"
  "workflow/tooling and app code never overlap") are confirmed absent from
  the current `CLAUDE.md` (read in full before drafting this plan) — Task 3c
  and Task 6's DDL/pagination-sharing designs below are not fighting a rule
  that no longer exists, consistent with the second audit's own §6.3
  re-scoring.
- `config/settings.yaml`'s calibration-audit comment block (`docs/open-
  decisions.md`'s tracked line) is confirmed **already wiped** in this
  worktree's checked-out copy (`grep -n "min_contracts_by_series\|
  whale_confidence_weights" config/settings.yaml` shows no comment lines
  between them) — restoring it is explicitly a human decision recorded in
  `docs/open-decisions.md`, not this plan's Task 5, which fixes the
  mechanism only (see Task 5's own scope note).
- **New finding this plan's own research produced, not in either audit:**
  the exact comment-wipe mechanism in `services/config/config_store.py`'s
  `update()` was reproduced experimentally against a scratch copy of the
  real file's shape (never the live file), settling the first audit's own
  disclosed unverified claim ("the exact ruamel attachment point was not
  verified by experiment"). See Task 5's Step 1 for the full experiment and
  result — it also found that the naive "just deep-merge every level"
  fix the audit suggests would introduce a **new** correctness bug (orphaned
  stale keys in `whale_watcher_kalshi.min_contracts_by_series` when a user
  removes a series from that field), which shaped Task 5's actual design.
- **Corrected finding: `resolved_signals_with_factors()`/`population_gate_summary()`'s
  currently-unscoped callers must not be given a `since_ts` bound** the way
  the audit's citation (first audit's Tier-1 item #3, not the second audit's
  §9.2) suggests — not because the capability doesn't exist (it does:
  `resolved_signals_with_factors(since_ts: float | None = None)` already
  carries this parameter, added 2026-09-01 by a separate, earlier initiative
  for a different caller shape than this one), but because both functions'
  own docstrings/callers establish they compute a gate against a **minimum
  total sample count**, and every one of their currently-unscoped callers is
  that same gate (confirmed by reading all 5 call sites of
  `resolved_signals_with_factors` and both 2 call sites of
  `population_gate_summary` — `services/analytics/routes.py:104` and
  `services/research/research.py:221`, the second inside an
  evidence-gated, infrequent sweep, Task 6 below). Passing `since_ts` to the
  gate-computing callers specifically would silently produce a wrong
  "insufficient" determination — an accuracy regression, not a fix; every
  current caller including the gate route correctly leaves it unset. Task 6
  designs a route-level TTL cache instead, which `services/candidate_log.py`'s
  own `population_gate_summary()` docstring — documenting the 2026-08-26 fix
  that took this function from an 18.2s per-row Python loop to a 4.8s SQL
  `GROUP BY` for the same 6.2M rows — independently supports as still worth
  caching even post-fix: a 4.8s compute cost on every dashboard poll (even
  off the event loop) is real, ongoing cost. (The `services/analytics/
  routes.py:95-98` comment's "17-38s" figure is the pre-fix range that
  motivated that same 2026-08-26 commit, referenced there as a named
  historical ROADMAP entry, not a current live measurement — cited
  accurately as history, not as today's cost.)
- **New finding: `event_live_data` is already scoped, and still 87.3% of
  the payload.** `services/state_view.py:198-200`'s `_scoped_event_live_data`
  (added 2026-08-21, predating both audits) already filters `state
  ["event_live_data"]` down to only the event tickers in
  `_relevant_tickers()`'s currently-relevant set. A live probe of `GET
  /api/state` today (2026-09-03) shows `event_live_data` at 4,725,543 of
  5,413,552 total uncompressed body bytes (87.3%) across only 23 scoped
  events — roughly 205KB/event. The audit's "send only currently-rendered
  events" framing is already implemented; the remaining cost is per-event
  payload size, not event count. Task 7 designs around this corrected
  understanding.
- **New finding: `bump_generation()` fires on every processed trade message,
  confirmed from source, not inferred.** `services/whale_stream/
  whale_stream_handlers.py:200-240`'s `_process_stream_trade` calls
  `bump_generation()` unconditionally on all three of its exit paths
  (not-running, no-signals, signals-emitted) — every trade print this app
  processes bumps `state["generation"]`, which is the literal ETag value.
  This is the mechanism, not an inference from symptom.

---

## Architecture — eight independent fixes, no shared infrastructure

1. **Stack-capture stall attribution** (`services/loop_watchdog.py`) — on
   each stall tick, capture the main thread's stack via
   `sys._current_frames()`/`traceback.format_stack()` (microseconds, stall
   path only) and record it into `fault_log`'s existing traceback slot,
   off the event loop.
2. **De-poll the History-tab loaders and Terminal-tab `/api/quality/summary`**
   from the fixed 6s `/api/state` timer, coarsening their own cadence
   independently — a stated data-plane tradeoff (staleness of secondary
   analytics data, never the primary trading signal).
3. **Three safety-adjacent DRY fixes**: (a) `generate_recommendations()`'s
   3 call sites missing `declined_ids`; (b) `RiskManager.check_daily_loss`'s
   missing zero-bankroll guard; (c) one canonical DDL string per table for
   `raw_trades`/`rejection_events`/`rejected_candidates`, owned by
   `capture_writer.py` (the only one of the three modules with no import
   dependency on the other two) and imported by `series_watcher.py`/
   `candidate_log.py`.
4. **`record_snapshot_from_ticker` off the event loop** — the caller
   (`_process_stream_ticker`) schedules the write via `tick_executor.run()`
   + `asyncio.create_task()`, the exact idiom PR #414
   (`docs/superpowers/plans/2026-09-01-event-loop-blocking-fix1.md`) used
   for four sibling functions; the function itself is unchanged (still a
   valid direct synchronous call for its own 3 existing tests).
5. **`config_store.update()` stops destroying comments and does not
   introduce a new stale-key bug doing it** — a recursive, no-delete-by-
   default merge (extends today's one-level `dict.update()` to every
   level, preserving object identity so ruamel's attached comments
   survive), plus one explicit, narrow full-replace-with-deletion path for
   `whale_watcher_kalshi.min_contracts_by_series` (the one field the
   frontend always resends as a complete map and the field the wiped
   comment sits immediately after).
6. **One `paginate()` FastAPI dependency** for the 6 routes confirmed
   genuinely unbounded today, plus a route-level TTL cache (not a query
   bound) for the two expensive population-gate reads.
7. **`event_live_data` throttled to `_EVENT_LIVE_DATA_REPOLL_SEC` (the
   existing 60s constant that already bounds how often the underlying data
   can change) and `bump_generation()` coarsened to at most once per
   second** — both explicit data-plane tradeoffs, both verified to have
   zero trading-decision dependency (grep-confirmed: nothing outside
   `state_view.py`/`main.py`'s HTTP layer reads `state["event_live_data"]`;
   nothing outside diagnostics/UI-facing code depends on `generation`
   incrementing more than once a second).
8. **`alerting.py`'s 3 discarded task handles get a retained reference**
   (Python's own documented `asyncio.create_task` GC hazard: "save a
   reference... a task that isn't referenced elsewhere may get garbage
   collected at any time"); **`http_client.py`'s shared `AsyncClient`
   gets its currently-implicit httpx defaults made explicit** (pinned, not
   changed — no measured bottleneck justifies a different number, per the
   data-plane HARD RULE).

**Tech stack:** Python 3 stdlib (`sys`, `threading`, `traceback`,
`contextlib`/none-new, `time`) plus this repo's existing `tick_executor`/
`task_supervisor`/`ruamel.yaml`/FastAPI `Depends` (a first use of
`Depends` in this codebase — see Task 6's own note). No new dependencies.

## Global Constraints

- **No change to any trading, risk, sizing, calibration, strategy,
  settlement, or auth code**, except the one, narrowly-scoped, additive
  fix in Task 3b (`RiskManager.check_daily_loss`'s zero-bankroll guard) —
  itself explicitly named in this plan's scope by the audit and mirrored
  byte-for-byte from `ShadowTrader`'s already-shipped, already-tested
  equivalent, not new logic.
- **No write to any `data/*.db` file, and no edit to `config/settings.yaml`**
  anywhere in this plan or its execution — Task 5 fixes `config_store.py`'s
  merge *mechanism*; it does not restore the wiped comment (`docs/open-
  decisions.md`'s existing tracked line is where that human decision lives)
  and no task here touches the live file.
- **No new timeout, TTL, retry, or capacity number is asserted as correct
  without being labeled an estimate and reasoned about**, per the
  data-plane HARD RULE:
  - Task 1: no new interval — the stack capture runs synchronously inline
    on the stall path (audit's own "microseconds" claim, unmeasured by this
    plan; Task 1's own Step 5 is where that gets checked live).
  - Task 2: History-tab loaders throttled to 30s (5x the current 6s poll,
    reasoning in Task 2), `/api/quality/summary` to 20s.
  - Task 6: pagination ceiling of 200 (matches the majority of this app's
    8 already-clamped routes' own ceiling, not a new number); cache TTL of
    30s for the two expensive population-gate routes (reasoning in Task 6).
  - Task 7: `event_live_data` send-throttle reuses
    `_EVENT_LIVE_DATA_REPOLL_SEC` (60, an *existing* constant, not a new
    one); `bump_generation()` coarsened to 1.0s (reasoning in Task 7).
  - Task 8: `http_client.py`'s `AsyncClient` timeout/limits are pinned at
    httpx 0.27.2's own measured, verified defaults (`Timeout(timeout=5.0)`,
    `Limits(max_connections=100, max_keepalive_connections=20,
    keepalive_expiry=5.0)` — confirmed live via `docker exec
    ddev-kalshi-whale-poc-fastapi python3` against the actual installed
    version, not assumed from httpx's documentation), not a new,
    independently-chosen number — no measured bottleneck justifies
    picking something else.
- **Every polling/throttle-interval change is named as a data-plane
  tradeoff explicitly, per the HARD RULE's "never trade one property for
  another silently."** Tasks 2, 6, and 7 each state which property is
  traded (timeliness of a UI-facing signal) and why it doesn't touch
  anything trading-decision-facing (grep-verified per task, not assumed).
- **This repo has no `pytest-asyncio`.** Every new async-path test in this
  plan follows the SAME two established idioms this repo already uses,
  matched per file: `tests/test_pipeline_health_cost.py`/`tests/
  test_diagnostics_routes.py`'s `asyncio.run(module.async_func(...))`
  inside a plain `def test_...`, or `TestClient(main.app).get(...)` where
  the existing file in question already uses that convention — verified
  per-file below, never assumed uniform across the suite (Tier 0's own
  plan had to correct itself twice on exactly this point).
- **This repo has no JS test runner today.** `frontend/package.json`'s
  scripts are `build`/`watch`/`lint` only (`npm test` does not exist; the
  Preact/`node --test` harness `docs/superpowers/plans/2026-08-25-
  frontend-modularization.md` designs is explicitly "not started" per
  `CLAUDE.md`'s own current-status note). Task 2's frontend changes are
  therefore verified by `npm run lint` + `npm run build` (both exist today)
  plus a live/manual check (nginx access-log request-rate comparison, or
  `chrome-devtools` MCP network-panel inspection) — stated as a real
  limitation, not glossed over, and not an excuse to stand up a whole new
  test harness inside this narrow task.
- **Task 6 introduces this codebase's first use of FastAPI's `Depends()`.**
  `grep -rln "Depends(" services/*.py services/**/*.py` returns zero hits
  before this plan. Named explicitly because it is a real, if small, idiom
  shift, not a silent one — `Depends()` is standard FastAPI machinery
  (not a hand-rolled abstraction the 2026-08-30 "prefer proven" rule would
  flag), and it changes nothing about any other route in the app.

---

### Task 1: Stack-capture stall attribution in `services/loop_watchdog.py`

**Files:**
- Modify: `services/loop_watchdog.py` (imports; `start()`'s `_tick()`
  closure)
- Modify: `services/fault_log.py:110-121` (`record_fault` — add an
  optional `tb` parameter, additive, every existing call site unaffected)
- Test: `tests/test_loop_watchdog.py` (extend existing file — 26 lines
  total today, real fixture-free style, confirmed by direct read)
- Test: `tests/test_fault_log.py` (extend existing file — has an
  `@pytest.fixture(autouse=True) def _isolated(monkeypatch, tmp_path)`
  that redirects `DB_PATH`, confirmed by direct read; module imported as
  `fl`, matching Tier 0's own Task 6 note about this file's conventions)

**Interfaces:**
- `fault_log.record_fault(component, operation, message, context=None,
  severity="warn", now=None, tb: str | None = None)` — `tb` is new,
  keyword-only-by-position-convention (added last), defaults to `None`
  (current behavior for every one of this function's other 10 call sites,
  confirmed via `grep -rn "record_fault(" services/*.py services/**/*.py`
  — none pass more than 4 positional/keyword args, so appending `tb` last
  cannot collide). Threaded into the existing `_write(...)` call's `tb`
  argument (currently hardcoded `None`).
- `loop_watchdog._tick()` (private closure inside `start()`, unchanged
  external signature) gains one new behavior on the existing stall branch:
  capture + fire-and-forget record. `loop_watchdog.snapshot()`/
  `reset_window()` are unchanged.
- New module-private helper `_record_stall_fault_background(tb: str) ->
  None` and module-level `_pending_fault_writes: set[asyncio.Task]` —
  dispatches the `fault_log.record_fault` write via a retained
  `asyncio.create_task(asyncio.to_thread(...))` rather than an inline
  `await`, so a slow write can neither block the loop nor delay `_tick()`'s
  own next `asyncio.sleep()` (adversarial review Finding F13), while still
  avoiding the weak-reference GC hazard Python's own `asyncio.create_task`
  documentation warns about (the same "retain a reference, don't block on
  it" idiom Task 8a below applies to `alerting.py`, implemented locally
  here rather than shared since Task 8a hasn't landed when this task runs).

- [ ] **Step 1: Read the exact current stall branch and `record_fault`
      before writing anything**

`services/loop_watchdog.py` in full (45 lines) — the stall branch is:

```python
            if late > _STALL_THRESHOLD_SEC:
                _stall_count += 1
                _stall_max_ms = max(_stall_max_ms, late * 1000)
```

`services/fault_log.py:110-121`:

```python
def record_fault(component: str, operation: str, message: str,
                 context: str | None = None, severity: str = "warn",
                 now: float | None = None) -> bool:
    """Log something worth knowing that isn't an exception - a field that
    arrived unparseable, a projection refused for want of data, a market
    that couldn't be resolved. Same deduplication, same never-raises
    contract."""
    try:
        return _write(component, operation, severity, None,
                      str(message)[:_MAX_MESSAGE_CHARS], None, context, now)
    except Exception:
        return False
```

Confirm both still match before editing — if not, stop and re-derive from
current source.

- [ ] **Step 2: Write the failing test for `record_fault`'s new `tb` param**

Add to `tests/test_fault_log.py` (matching its existing `fl`-import,
autouse-`_isolated`-fixture convention — confirmed via `grep -n "^import
fault_log\|import fault_log as fl\|_isolated" tests/test_fault_log.py`
before writing):

```python
def test_record_fault_stores_an_explicit_traceback():
    """record_fault's tb param (added by Task 1 of docs/superpowers/plans/
    2026-09-03-tier1-backend-hygiene.md) stores a pre-formatted stack/
    traceback string into the same first_traceback slot record() populates
    from a real exception - for a captured stack (loop_watchdog's stall
    attribution), not a raised one."""
    fl.record_fault("test_component", "test_op", "something worth knowing",
                     tb="Traceback (most recent call last):\n  fake stack\n")
    row = fl.recent(component="test_component", limit=1)[0]
    assert row["first_traceback"] == "Traceback (most recent call last):\n  fake stack\n"


def test_record_fault_tb_defaults_to_none_for_every_existing_caller():
    """Every one of this function's other 10+ call sites omits tb - this
    pins that omitting it still behaves exactly as before (first_traceback
    stays NULL), so this additive param cannot be a silent behavior change
    for anything that doesn't pass it."""
    fl.record_fault("test_component2", "test_op2", "no traceback here")
    row = fl.recent(component="test_component2", limit=1)[0]
    assert row["first_traceback"] is None
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_fault_log.py -k test_record_fault_stores_an_explicit_traceback -q"`
Expected: **fails** — `TypeError: record_fault() got an unexpected keyword argument 'tb'`.

- [ ] **Step 3: Apply the `record_fault` fix**

Change:

```python
def record_fault(component: str, operation: str, message: str,
                 context: str | None = None, severity: str = "warn",
                 now: float | None = None) -> bool:
    """Log something worth knowing that isn't an exception - a field that
    arrived unparseable, a projection refused for want of data, a market
    that couldn't be resolved. Same deduplication, same never-raises
    contract."""
    try:
        return _write(component, operation, severity, None,
                      str(message)[:_MAX_MESSAGE_CHARS], None, context, now)
    except Exception:
        return False
```

to:

```python
def record_fault(component: str, operation: str, message: str,
                 context: str | None = None, severity: str = "warn",
                 now: float | None = None, tb: str | None = None) -> bool:
    """Log something worth knowing that isn't an exception - a field that
    arrived unparseable, a projection refused for want of data, a market
    that couldn't be resolved. Same deduplication, same never-raises
    contract.

    tb (2026-09-03, Task 1 of docs/superpowers/plans/2026-09-03-tier1-
    backend-hygiene.md): stores a pre-formatted traceback/stack string into
    the same first_traceback slot record() populates from a real
    exception - added for services/loop_watchdog.py's stall-attribution
    capture, a non-exception event (a captured stack, not a raised one)
    that still needs a first_traceback for attribution. Every existing
    call site omits it and behaves exactly as before (None -> unchanged
    null column, since _write's ON CONFLICT never updates first_traceback
    on a repeat anyway - see this module's own module docstring)."""
    try:
        return _write(component, operation, severity, None,
                      str(message)[:_MAX_MESSAGE_CHARS],
                      tb[:_MAX_TRACEBACK_CHARS] if tb else None,
                      context, now)
    except Exception:
        return False
```

- [ ] **Step 4: Run Step 2's tests, confirm they pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_fault_log.py -q"`
Expected: all pass, including both new ones.

- [ ] **Step 5: Write the failing test for `loop_watchdog`'s stall capture**

Add to `tests/test_loop_watchdog.py`, following its own existing
`test_watchdog_reports_a_real_stall`'s exact shape (a real forced stall via
`time.sleep()`, not a mocked timer — matching this file's established
convention):

```python
def test_stall_captures_a_stack_and_records_it_off_the_loop(monkeypatch):
    """The 2026-09-02 incident had zero attribution for what was blocking
    the loop - services/loop_watchdog.py's own docstring already tracks
    magnitude/count but nothing about *what*. This is the fix (§4.3 of
    docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md):
    capture the main thread's stack on the stall path itself and record it
    via fault_log's existing traceback slot, off the event loop so the
    diagnostic write can never become a new instance of the #210 blocking-
    sqlite-on-the-loop bug class this app has already fixed once elsewhere."""
    calls = []

    def _fake_record_fault(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        calls.append((args, kwargs, on_loop))
        return True

    monkeypatch.setattr(loop_watchdog.fault_log, "record_fault", _fake_record_fault)

    async def run():
        task = loop_watchdog.start(sample_interval_sec=0.01)
        await asyncio.sleep(0.05)
        time.sleep(0.2)  # blocks the loop - forces a real stall, same as the existing test above
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    loop_watchdog.reset_window()
    asyncio.run(run())

    assert calls, "expected at least one fault_log.record_fault call for the forced stall"
    args, kwargs, on_loop = calls[0]
    assert args[0] == "loop_watchdog"
    assert args[1] == "stall"
    assert not on_loop, "the fault_log write must run off the event loop (asyncio.to_thread)"
    assert kwargs.get("severity") == "warn"
    assert kwargs.get("tb"), "expected a non-empty captured stack string"
```

(`asyncio` and `time` are already imported at this file's top, per its
existing `test_watchdog_reports_a_real_stall` — no new imports needed for
the test itself; `loop_watchdog.fault_log` requires the Step 6
implementation below to import `fault_log` at module scope first.)

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_loop_watchdog.py -k stall_captures_a_stack -q"`
Expected: **fails** — `AttributeError: module 'services.loop_watchdog' has no attribute 'fault_log'` (not imported yet) or `calls` empty.

- [ ] **Step 6: Implement the capture + off-loop record**

Add to `services/loop_watchdog.py`'s imports (currently just `asyncio`,
`time`):

```python
import sys
import threading
import traceback

from services import fault_log
```

Add module-level helpers:

```python
def _capture_stall_traceback() -> str:
    """Cheap (microseconds - §4.3 of docs/superpowers/research/2026-09-02-
    architecture-audit-second-pass.md), runs only on the stall path, never
    on the hot 0.1s sample tick. sys._current_frames() is a snapshot of
    every live thread's current frame, safe to call from any thread; this
    app runs its event loop on the process's main thread, so
    threading.main_thread().ident is the right key."""
    frame = sys._current_frames().get(threading.main_thread().ident)
    if frame is None:
        return "<main thread frame unavailable>"
    return "".join(traceback.format_stack(frame))


_pending_fault_writes: set[asyncio.Task] = set()


def _record_stall_fault_background(tb: str) -> None:
    """Fire-and-forget the fault_log write so a slow SQLite write cannot
    delay _tick()'s own next asyncio.sleep() and inflate stall_count with
    a self-inflicted phantom stall (adversarial review of this plan,
    Finding F13) - but still retain a reference to the created Task
    (matching Task 8a's own 'retain a reference so it isn't GC'd, but
    don't block on it' idiom, applied here rather than imported from it
    since Task 8a hasn't landed yet when this task runs)."""
    task = asyncio.create_task(asyncio.to_thread(
        fault_log.record_fault, "loop_watchdog", "stall",
        "event loop stall detected (see first_traceback for the "
        "captured stack)", severity="warn", tb=tb,
    ))
    _pending_fault_writes.add(task)
    task.add_done_callback(_pending_fault_writes.discard)
```

Change the stall branch inside `start()`'s `_tick()` from:

```python
            if late > _STALL_THRESHOLD_SEC:
                _stall_count += 1
                _stall_max_ms = max(_stall_max_ms, late * 1000)
```

to:

```python
            if late > _STALL_THRESHOLD_SEC:
                _stall_count += 1
                _stall_max_ms = max(_stall_max_ms, late * 1000)
                # Stack capture (2026-09-03, Task 1 of docs/superpowers/
                # plans/2026-09-03-tier1-backend-hygiene.md): the 2026-09-02
                # incident had zero visibility into *what* was blocking the
                # loop. Capture is synchronous and cheap (sys._current_
                # frames/format_stack, no I/O); the fault_log WRITE is
                # fire-and-forget via _record_stall_fault_background() so
                # this diagnostic can never itself become a blocking-sqlite-
                # on-the-loop bug NOR delay this same _tick() coroutine's own
                # next asyncio.sleep(), which an inline `await
                # asyncio.to_thread(...)` would (adversarial review Finding
                # F13: a slow write delaying the watchdog's own next sample
                # could inflate stall_count with a self-inflicted phantom
                # stall - exactly the metric this task exists to make more
                # trustworthy).
                # Message is a fixed string (not late-value-dependent) so
                # fault_log's own dedup key (component, operation, exc_type,
                # message) collapses every stall into ONE row that
                # accumulates count - same tradeoff every other fault_log
                # row already makes (first_traceback is the FIRST capture,
                # not necessarily the most recent - a known limitation,
                # not silently glossed over: see this task's own Global
                # Constraints-adjacent note in the plan).
                tb = _capture_stall_traceback()
                _record_stall_fault_background(tb)
```

**Known, stated limitation (not silently glossed over):** `fault_log`'s
`_write()` keeps only the FIRST-ever `first_traceback` for a given
`(component, operation, exc_type, message)` key (`ON CONFLICT ... DO
UPDATE SET count = count + 1, last_seen = excluded.last_seen` — `context`/
`first_traceback` are not in that `SET` clause). Because this task's
message is a fixed string, every stall after the first bumps `count`
without refreshing the captured stack. If the underlying blocking cause
changes over the app's lifetime, the stored stack becomes a stale
attribution for a *different* cause, silently. This matches every other
`fault_log` row's existing behavior (not a new problem this task
introduces) and is the same tradeoff Tier 0's own plan and this file's
Global Constraints accept for a Tier-1-sized fix — a per-window-refreshing
version is a real, larger follow-up (bucketing by time or by captured
stack signature), out of this task's scope, and is recorded here rather
than assumed solved.

- [ ] **Step 7: Confirm the tests pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_loop_watchdog.py tests/test_fault_log.py -q"`
Expected: all pass, including the two new ones (Steps 2, 5).

- [ ] **Step 8: Confirm nothing else that imports `loop_watchdog` regressed**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/ -k loop_watchdog -q && python -m pytest tests/test_observability.py -q"`
Expected: no regressions — `services/observability/observability.py`'s
`_flatten_loop_watchdog`/`capture_from_runtime` only read
`loop_watchdog.snapshot()`, untouched by this task.

---

### Task 2: De-poll the History-tab loaders and Terminal-tab `/api/quality/summary`

**Files:**
- Modify: `frontend/src/js/main.js:72-83` (`refreshHistoryInsightsIfActive`)
- Modify: `frontend/src/js/polling-and-websocket.js:108` (the
  `loadSystemHealth(state)` call site inside `refresh()`'s `active ===
  'terminal'` branch)
- No test file — this repo has no JS test runner today (Global
  Constraints). Verified via `npm run lint`/`npm run build` plus a live
  check (Step 5).

**Data-plane tradeoff, named explicitly (HARD RULE):** this throttles the
*display* cadence of secondary analytics data (advisory recommendations,
calibration reports, regime segmentation, candidate-log summaries,
backtest sweeps, series-evaluator status, market-analyst output, and the
Quality Control Plane's own composite health summary) on the two tabs that
are not the primary trading surface. It does not touch `/api/state`'s own
6s poll (Terminal tab's live signal/decision feed, positions, prices),
which stays untouched — nothing here delays a trading-relevant signal.
Verified, not assumed: `grep -rn "refreshHistoryInsightsIfActive\|
loadSystemHealth" frontend/src/js/*.js` shows both are called only from
`polling-and-websocket.js`'s `refresh()`, never from any code path that
also updates `terminalSignalFeed`/`terminalDecisionFeed`/`latest_prices`
(those are set directly from `state.*` a few lines above, independent of
either throttled call).

**Chosen intervals, stated as estimates with reasoning (dimensional-
analysis HARD RULE):**
- History-tab loaders: 30,000ms (30s) — 5x the current 6s poll interval
  (`config/settings.yaml`'s `kalshi.poll_interval_sec: 6`, confirmed live).
  §3.3's own evidence: the measured 504-timeout storm (2,305/hour at 6s
  cadence, foreground) directly correlates with this exact 6s-cadence
  fan-out of 5+ routes; a 5x reduction in call volume is the most direct
  lever on that specific mechanism, without guessing at a query-cost fix
  this task doesn't attempt (that's the `tick_executor` pool contention
  problem named in §3.3's own "worst hour" note, separately scoped).
  30s is still fast enough that a human actively reading the History tab
  sees updates well within normal page-refresh expectations.
- `/api/quality/summary`: 20,000ms (20s) — smaller multiplier than the
  History loaders' 5x (only ~3.3x) because §3.3's own live evidence found
  this route is "almost never fetched by the browser" (35 rows in 13
  hours) and is "the endpoint CLAUDE.md tells every session to start
  from," i.e. it is the LEAST valuable of the three to de-poll and the
  route a session may specifically want fresher when the Terminal tab is
  open for exactly that reason — 20s is a smaller, more conservative cut
  than the History loaders get, consistent with §3.3's own corrected
  priority ordering, not the naive "de-poll everything the same amount"
  reading of the audit's Tier-1 #8 one-line summary.

- [ ] **Step 1: Read the current wiring fresh, confirm it still matches**

`frontend/src/js/main.js:68-83`:

```javascript
function refreshHistoryInsightsIfActive() {
  if (currentView !== 'history') return;
  loadAdvisory();
  loadDeclinedSuggestions();
  loadChangeHistory();
  loadCalibrationReport();
  loadCalibrationHistory();
  loadRegimeSegmentation();
  loadCandidateLogSummary();
  loadBacktestSweeps();
  loadSeriesEvaluator();
  loadMarketAnalyst();
}
```

`frontend/src/js/polling-and-websocket.js:97-108` (inside `refresh()`):

```javascript
    const active = VIEWS.find(v => $('view-' + v).classList.contains('active'));
    if (active === 'terminal') {
      $('markets-list-toggle').innerHTML = advToggleHTML('markets-list');
      if (isAdvanced('markets-list')) {
        renderScreenerTable('markets-list', 'markets-list', 'market-count', state.markets, state.latest_prices || {}, false, []);
      } else {
        renderMarkets(state.markets, state.latest_prices || {});
      }
      renderFunnel(state.stats);
      renderSignalDecisionFeed(state.signal_feed, state.decision_feed);
      renderHalted(state.risk);
      loadSystemHealth(state);
```

Confirm both still match before editing — if not, stop and re-derive from
current source.

- [ ] **Step 2: Throttle `refreshHistoryInsightsIfActive`**

Change:

```javascript
function refreshHistoryInsightsIfActive() {
  if (currentView !== 'history') return;
  loadAdvisory();
  loadDeclinedSuggestions();
  loadChangeHistory();
  loadCalibrationReport();
  loadCalibrationHistory();
  loadRegimeSegmentation();
  loadCandidateLogSummary();
  loadBacktestSweeps();
  loadSeriesEvaluator();
  loadMarketAnalyst();
}
```

to:

```javascript
// De-polled (2026-09-03, Task 2 of docs/superpowers/plans/2026-09-03-
// tier1-backend-hygiene.md, §3.3 of the architecture-audit-second-pass
// research): this used to fire all 9 loaders on EVERY /api/state poll
// (every 6s at this app's default kalshi.poll_interval_sec) while the
// History tab was open, measured live as the dominant cause of a
// 2,305-per-hour nginx 504 storm during one foreground hour. 30000ms is
// 5x the base poll interval - a stated data-plane tradeoff (staleness of
// secondary analytics data on a non-trading-surface tab), not a change to
// /api/state's own cadence or anything trading-decision-facing.
const HISTORY_INSIGHTS_REFRESH_MS = 30000;
let _lastHistoryInsightsRefreshAt = 0;

function refreshHistoryInsightsIfActive() {
  if (currentView !== 'history') return;
  const now = Date.now();
  if (now - _lastHistoryInsightsRefreshAt < HISTORY_INSIGHTS_REFRESH_MS) return;
  _lastHistoryInsightsRefreshAt = now;
  loadAdvisory();
  loadDeclinedSuggestions();
  loadChangeHistory();
  loadCalibrationReport();
  loadCalibrationHistory();
  loadRegimeSegmentation();
  loadCandidateLogSummary();
  loadBacktestSweeps();
  loadSeriesEvaluator();
  loadMarketAnalyst();
}
```

Tab-switch behavior is preserved: `_lastHistoryInsightsRefreshAt` starts at
0, so the first call after switching to the History tab (the next
`refresh()` tick, within one poll interval) always fires immediately, same
as today; only the REPEATED fetch-every-poll behavior while the tab stays
open is throttled.

- [ ] **Step 3: Throttle `loadSystemHealth`'s call site**

Change `frontend/src/js/polling-and-websocket.js`'s terminal branch from:

```javascript
      renderHalted(state.risk);
      loadSystemHealth(state);
```

to:

```javascript
      renderHalted(state.risk);
      // De-polled (2026-09-03, Task 2 of docs/superpowers/plans/2026-09-03-
      // tier1-backend-hygiene.md): §3.3 of the architecture-audit-second-
      // pass research found this route is the LEAST-fetched of the three
      // named for de-polling (35 browser rows in 13h - the Terminal tab is
      // rarely open) and the MOST important to keep responsive, since it's
      // the endpoint CLAUDE.md tells every session to start an
      // investigation from. Smaller throttle (20s, vs. the History tab
      // loaders' 30s) reflects that corrected priority, not a uniform
      // "de-poll everything the same" reading.
      const _now = Date.now();
      if (_now - _lastSystemHealthLoadAt >= SYSTEM_HEALTH_REFRESH_MS) {
        _lastSystemHealthLoadAt = _now;
        loadSystemHealth(state);
      }
```

Add near this file's other module-level `let`s (next to
`terminalSignalFeed`/etc.):

```javascript
const SYSTEM_HEALTH_REFRESH_MS = 20000;
let _lastSystemHealthLoadAt = 0;
```

Same tab-switch-preserving property as Step 2: the first poll after
switching to the Terminal tab always fires (starts at 0).

- [ ] **Step 4: Lint and build**

Run: `cd /app/.claude/worktrees/tier1-backend-hygiene/frontend && npm run lint && npm run build`
(via `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene/frontend && npm run lint && npm run build"`)
Expected: no lint errors, bundle builds successfully into
`static/js/dashboard.bundle.js`.

- [ ] **Step 5: Live verification (no JS test runner exists — Global
      Constraints)**

Via the `/run` skill or `chrome-devtools` MCP against
`https://kalshi-whale-poc.ddev.site:8443`: open the History tab, watch the
Network panel for 30+ seconds, confirm the 9 History-tab routes
(`/api/advisory/status`, `/api/suggestions/declined`, `/api/config/
change-history`, `/api/confidence-calibration/report`, `/api/confidence-
calibration/history`, `/api/regime/by-category`, `/api/candidate-log/
summary`, `/api/backtest/entry-threshold`, `/api/series-evaluator/status`,
`/api/market-analyst/*` — confirm the exact 9 route paths via `grep -n
"fetchJSON\|/api/" frontend/src/js/advisory-calibration.js` before
asserting this list, since this plan's own research did not exhaustively
trace every one) fire once on tab-open, then not again for ~30s, then
again. Separately open the Terminal tab and confirm `/api/quality/summary`
follows the same pattern at ~20s. Compare against `docker logs
ddev-kalshi-whale-poc-web --since 2m` (or the nginx access log) 504-count
rate before/after over a comparable foreground window, per §3.3's own
measurement method — record the before/after numbers in this task's
completion note rather than assuming the fix worked from code inspection
alone.

---

### Task 3: Three safety-adjacent DRY fixes

**Research basis for this task specifically:** the second-pass audit's §8
Tier-1 item 9 points at "(§9.1 of the FIRST audit — read
`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-
considerations.md`'s §9.1 directly for full detail)" — read directly
below, not re-derived from the second audit's own one-line summary.

#### Task 3a: `generate_recommendations()`'s 3 call sites missing `declined_ids`

**Files:**
- Modify: `main.py` (imports near line 32/57; `_maybe_run_auto_apply`,
  line 583)
- Modify: `services/analytics/market_analyst_orchestrator.py:95,277`
  (`_analyze_market_uncached`, `_build_full_spectrum_context` — no import
  change needed, `suggestion_decisions` is already imported at this
  file's line 18)
- Test: `tests/test_main_scheduler_loops.py` (extend existing file — has
  `_wire_advisory_auto_apply(monkeypatch, degraded)` +
  `test_advisory_auto_apply_writes_when_evidence_is_clean`/
  `test_advisory_auto_apply_refuses_to_write_when_evidence_is_degraded`,
  confirmed by direct read, lines 447-509)
- Test: `tests/test_market_analyst_orchestrator.py` (extend existing file —
  currently 3 tests, none touching `generate_recommendations`, confirmed
  by direct read)

**Interfaces:** `advisory_engine.generate_recommendations(...)`'s own
signature (`services/advisory/advisory_engine.py:909-914`) is unchanged —
`declined_ids: set[str] | None = None` already exists as a parameter; this
task only adds the missing keyword argument at 3 of its 6 call sites. The
other 3 (`services/advisory/routes.py:148,175`, `services/research/
research.py:173`) already pass it — confirmed via `grep -n
"declined_ids=" services/advisory/routes.py services/research/research.py
services/analytics/market_analyst_orchestrator.py main.py` immediately
before drafting this task.

- [ ] **Step 1: Confirm the exact current call sites**

`main.py:583-593` (inside `_maybe_run_auto_apply`):

```python
            adv_result = advisory_engine.generate_recommendations(
                adv_all_rows, cfg, adv_current_fp, adv_variants,
                adv_cfg["min_resolved_trades_per_variant"],
                gate_summaries=candidate_log.gate_summary(),
                # Staleness filter (2026-08-11, direct bug report) matters most
                # right here - unlike a manual click, auto-apply has no human
                # to notice it's repeatedly nudging the same field off the
                # exact same stale evidence every cooldown window.
                last_applied_by_path=config_performance.all_last_applied_by_path(),
                series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
                category_rows=regime_analytics.by_category(adv_all_rows),
            )
```

`services/analytics/market_analyst_orchestrator.py:95-101` (inside
`_analyze_market_uncached`):

```python
        recommendations = advisory_engine.generate_recommendations(
            all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
            gate_summaries=candidate_log.gate_summary(),
            last_applied_by_path=config_performance.all_last_applied_by_path(),
            series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
            category_rows=regime_analytics.by_category(all_rows),
        )
```

`services/analytics/market_analyst_orchestrator.py:277-283` (inside
`_build_full_spectrum_context`):

```python
    recommendations = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg.get("min_resolved_trades_per_variant", 30),
        gate_summaries=gate_summaries,
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=category_rows,
    )
```

Confirm all three still match before editing — if not, stop and re-derive
from current source (`main.py`'s line numbers especially may have shifted
since Tasks 1-2 above touch other files, not this one, but re-check
anyway).

- [ ] **Step 2: Write the failing test for `main.py`'s auto-apply path**

Add to `tests/test_main_scheduler_loops.py`, extending
`_wire_advisory_auto_apply` to capture the kwargs `generate_recommendations`
was actually called with (its current mock is `lambda *a, **k: {...}`,
which already accepts arbitrary kwargs — only the assertion is new):

```python
def test_maybe_run_auto_apply_passes_declined_ids(monkeypatch):
    """Task 3a of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md: this is the ONE unsupervised generate_recommendations()
    call site (no human in the loop between a suggestion and it being
    applied) - per advisory_engine.py's own declined_ids docstring
    (":926-929", "a suggestion a human already clicked 'no thanks' on
    doesn't come back with the exact same evidence behind it"), this is
    exactly the path that most needs to honor a decline, and previously
    didn't."""
    calls = []
    monkeypatch.setattr(main.advisory_engine, "generate_recommendations",
                         lambda *a, **k: calls.append(k) or {"recommendations": []})
    monkeypatch.setattr(main, "_series_evaluator_rows_for_advisory", lambda cfg: [])
    monkeypatch.setattr(main.regime_analytics, "by_category", lambda rows: [])
    monkeypatch.setattr(main.candidate_log, "gate_summary", lambda: {})
    monkeypatch.setattr(main.config_performance, "last_applied_at", lambda source: None)
    monkeypatch.setattr(main.config_performance, "fingerprint", lambda cfg: "fp")
    monkeypatch.setattr(main.config_performance, "all_last_applied_by_path", lambda: {})
    monkeypatch.setattr(main.config_performance, "all_variants", lambda: [])
    monkeypatch.setattr(main.trade_analytics, "build_trade_history", lambda rows: [])
    monkeypatch.setattr(main.evidence_provenance, "current_completeness_state",
                         lambda: {"degraded": False, "defects": [], "checked_at": 0.0})
    monkeypatch.setattr(main.suggestion_decisions, "declined_ids", lambda: {"decl-1", "decl-2"})

    main._maybe_run_auto_apply(_ADVISORY_CFG)

    assert len(calls) == 1
    assert calls[0].get("declined_ids") == {"decl-1", "decl-2"}
```

(`_ADVISORY_CFG` is this file's existing module-level fixture dict, line
478-487, unchanged and reused as-is.)

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_main_scheduler_loops.py -k passes_declined_ids -q"`
Expected: **fails** — either `AttributeError: module 'main' has no
attribute 'suggestion_decisions'` (not imported yet) or
`calls[0].get("declined_ids")` is `None`.

- [ ] **Step 3: Apply the `main.py` fix**

Add `from services.history import suggestion_decisions` to `main.py`'s
import block, next to its existing `from services.history import
regime_analytics` / `from services.history import trade_analytics` lines
(confirmed present at lines 32/57 — add alongside them, matching this
file's existing one-module-per-line style for `services.history`
submodules).

Change the call at line 583-593 from:

```python
            adv_result = advisory_engine.generate_recommendations(
                adv_all_rows, cfg, adv_current_fp, adv_variants,
                adv_cfg["min_resolved_trades_per_variant"],
                gate_summaries=candidate_log.gate_summary(),
                # Staleness filter (2026-08-11, direct bug report) matters most
                # right here - unlike a manual click, auto-apply has no human
                # to notice it's repeatedly nudging the same field off the
                # exact same stale evidence every cooldown window.
                last_applied_by_path=config_performance.all_last_applied_by_path(),
                series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
                category_rows=regime_analytics.by_category(adv_all_rows),
            )
```

to:

```python
            adv_result = advisory_engine.generate_recommendations(
                adv_all_rows, cfg, adv_current_fp, adv_variants,
                adv_cfg["min_resolved_trades_per_variant"],
                gate_summaries=candidate_log.gate_summary(),
                # Staleness filter (2026-08-11, direct bug report) matters most
                # right here - unlike a manual click, auto-apply has no human
                # to notice it's repeatedly nudging the same field off the
                # exact same stale evidence every cooldown window.
                last_applied_by_path=config_performance.all_last_applied_by_path(),
                series_evaluator_rows=_series_evaluator_rows_for_advisory(cfg),
                category_rows=regime_analytics.by_category(adv_all_rows),
                # 2026-09-03, Task 3a of docs/superpowers/plans/2026-09-03-
                # tier1-backend-hygiene.md: this is the one UNSUPERVISED
                # call site (no human between a suggestion and applying it)
                # - the one that most needs to honor a decline, and
                # previously didn't (advisory_engine.py's declined_ids
                # docstring, :926-929).
                declined_ids=suggestion_decisions.declined_ids(),
            )
```

- [ ] **Step 4: Run Step 2's test, confirm it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_main_scheduler_loops.py -q"`
Expected: all pass, including the new one.

- [ ] **Step 5: Write the failing tests for the orchestrator's 2 call sites**

Add to `tests/test_market_analyst_orchestrator.py`:

```python
def test_analyze_market_uncached_passes_declined_ids(monkeypatch):
    """Task 3a: the LLM market-analyst's own context snapshot includes
    advisory recommendations (services/ml_feed.py's 'work WITH, not
    replace' framing) - a declined suggestion showing up here can feed
    back into a fresh LLM-generated recommendation that re-proposes the
    same thing a human already said no to."""
    from services.analytics import market_analyst_orchestrator as mao

    calls = []
    monkeypatch.setattr(mao.advisory_engine, "generate_recommendations",
                         lambda *a, **k: calls.append(k) or {"recommendations": []})
    monkeypatch.setattr(mao.suggestion_decisions, "declined_ids", lambda: {"decl-x"})
    # Remaining collaborators mocked minimally - only generate_recommendations'
    # own kwargs are under test here; match this file's existing
    # test_returns_real_rows_when_enabled fixture style for the rest.
    ...  # fill in with this file's own existing mocking helpers for
         # _analyze_market_uncached's other collaborators (config_store,
         # broker.trade_log, config_performance.*, candidate_log.gate_summary,
         # regime_analytics.by_category) - confirm the exact set via
         # `grep -n "monkeypatch.setattr(mao\." tests/test_market_analyst_orchestrator.py`
         # and this file's existing tests before writing, since this task's
         # own research did not fully trace _analyze_market_uncached's
         # collaborator list

    asyncio.run(mao._analyze_market_uncached(fake_client, cfg, "TEST-TICKER"))

    assert len(calls) == 1
    assert calls[0].get("declined_ids") == {"decl-x"}


def test_build_full_spectrum_context_passes_declined_ids(monkeypatch):
    """Same fix, same reasoning, the full-spectrum (all-series) LLM
    context builder's own generate_recommendations call."""
    from services.analytics import market_analyst_orchestrator as mao

    calls = []
    monkeypatch.setattr(mao.advisory_engine, "generate_recommendations",
                         lambda *a, **k: calls.append(k) or {"recommendations": []})
    monkeypatch.setattr(mao.suggestion_decisions, "declined_ids", lambda: {"decl-y"})
    ...  # same note as above - trace _build_full_spectrum_context's real
         # collaborator list from current source before filling this in

    mao._build_full_spectrum_context(cfg)

    assert len(calls) == 1
    assert calls[0].get("declined_ids") == {"decl-y"}
```

**Disclosed placeholder, not silently left incomplete:** the `...` blocks
above are explicitly flagged rather than guessed — `_analyze_market_
uncached`'s and `_build_full_spectrum_context`'s full collaborator lists
(every other function/module they call besides `generate_recommendations`
itself) were not fully traced by this plan's own research pass, unlike
`main.py`'s `_maybe_run_auto_apply`, whose existing test fixture
(`_wire_advisory_auto_apply`) already enumerates its full collaborator set
directly. Whoever executes this task must read
`services/analytics/market_analyst_orchestrator.py`'s current source for
both functions (`_analyze_market_uncached:66-134`,
`_build_full_spectrum_context:261-318`) and this test file's own existing
`test_returns_real_rows_when_enabled` fixture pattern before filling
these in — same disclosed-scoping-decision shape Tier 0's own plan used
for its under-verified `title_cache.py`/`market_catalog.py` bodies, not a
silently-hidden gap.

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_market_analyst_orchestrator.py -k declined_ids -q"`
Expected: **fails** (once filled in) — `calls[0].get("declined_ids")` is
`None`.

- [ ] **Step 6: Apply both orchestrator fixes**

Change `services/analytics/market_analyst_orchestrator.py:95-101` from:

```python
        recommendations = advisory_engine.generate_recommendations(
            all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
            gate_summaries=candidate_log.gate_summary(),
            last_applied_by_path=config_performance.all_last_applied_by_path(),
            series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
            category_rows=regime_analytics.by_category(all_rows),
        )
```

to (add one line):

```python
        recommendations = advisory_engine.generate_recommendations(
            all_rows, cfg, current_fp, variants, adv_cfg["min_resolved_trades_per_variant"],
            gate_summaries=candidate_log.gate_summary(),
            last_applied_by_path=config_performance.all_last_applied_by_path(),
            series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
            category_rows=regime_analytics.by_category(all_rows),
            declined_ids=suggestion_decisions.declined_ids(),  # 2026-09-03, Task 3a
        )
```

And `:277-283` from:

```python
    recommendations = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg.get("min_resolved_trades_per_variant", 30),
        gate_summaries=gate_summaries,
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=category_rows,
    )
```

to:

```python
    recommendations = advisory_engine.generate_recommendations(
        all_rows, cfg, current_fp, variants, adv_cfg.get("min_resolved_trades_per_variant", 30),
        gate_summaries=gate_summaries,
        last_applied_by_path=config_performance.all_last_applied_by_path(),
        series_evaluator_rows=_series_evaluator_overview_with_crosscheck(cfg),
        category_rows=category_rows,
        declined_ids=suggestion_decisions.declined_ids(),  # 2026-09-03, Task 3a
    )
```

No import change needed — `suggestion_decisions` is already imported at
this file's line 18.

- [ ] **Step 7: Run both new tests plus the full file, confirm pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_market_analyst_orchestrator.py tests/test_main_scheduler_loops.py -q"`
Expected: all pass.

#### Task 3b: `RiskManager.check_daily_loss`'s missing zero-bankroll guard

**Files:**
- Modify: `services/risk_manager.py:152-166` (`check_daily_loss`)
- Test: `tests/test_risk_manager.py` (extend existing file — has a
  `_risk(tmp_path, monkeypatch, starting_bankroll=1000.0,
  max_daily_loss_pct=0.1, kill_switch_enabled=True)` fixture helper,
  confirmed by direct read)

**Interfaces:** `RiskManager.check_daily_loss(current_bankroll, now=None)
-> bool` — signature unchanged. `ShadowTrader.check_daily_loss` (`services/
shadow_mode.py:139-152`) already has this exact guard; this task mirrors
it byte-for-byte into the real kill switch's own equivalent, not new
logic — matching this plan's Global Constraints exception for this one
safety-code change.

- [ ] **Step 1: Confirm the exact current functions match before editing**

`services/risk_manager.py:152-166`:

```python
    def check_daily_loss(self, current_bankroll: float, now: float | None = None) -> bool:
        """Returns True if trading should continue; flips the kill switch if
        not. Runs the automatic daily rollover first (see module docstring)
        - a halt from a stale, day-old baseline gets cleared here the same
        way every other part of this state already self-manages, not left
        for a human to notice and clear by hand."""
        self._maybe_rollover_day(current_bankroll, now)
        if not self.kill_switch_enabled or self.halted:
            return not self.halted
        loss_pct = (self.day_start_bankroll - current_bankroll) / self.day_start_bankroll
        if loss_pct >= self.max_daily_loss_pct:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: -{loss_pct:.1%}"
            self._persist()
            return False
        return True
```

`services/shadow_mode.py:139-152` (the already-guarded sibling):

```python
    def check_daily_loss(
        self, current_bankroll: float, max_daily_loss_pct: float, kill_switch_enabled: bool, now: float | None = None,
    ) -> bool:
        self._maybe_rollover_day(current_bankroll, now)
        if not kill_switch_enabled or self.halted:
            return not self.halted
        if not self.day_start_bankroll:
            return True
        loss_pct = (self.day_start_bankroll - current_bankroll) / self.day_start_bankroll
        if loss_pct >= max_daily_loss_pct:
            self.halted = True
            self.halt_reason = f"Daily loss limit hit: -{loss_pct:.1%}"
            self._persist_risk()
            return False
        return True
```

Confirm both still match before editing — if not, stop and re-derive from
current source (this is real, hot-path-adjacent, safety-relevant code;
re-verify carefully, per the "never guess" HARD RULE, not just this
plan's own paraphrase).

- [ ] **Step 2: Write the failing test**

Add to `tests/test_risk_manager.py`, next to its existing
`test_check_daily_loss_within_limit_keeps_trading`/
`test_check_daily_loss_trips_kill_switch_past_threshold`:

```python
def test_check_daily_loss_zero_bankroll_baseline_does_not_crash(tmp_path, monkeypatch):
    """RiskManager's own missing guard (services/shadow_mode.py's
    ShadowTrader already has it: `if not self.day_start_bankroll: return
    True`) - the reachable path is reset_day(current_bankroll) setting the
    baseline from the live bankroll at each UTC date rollover, so a
    bankroll of exactly 0 at rollover would raise ZeroDivisionError in the
    REAL kill switch (the inert shadow copy was already protected - the
    safety asymmetry ran backwards). Task 3b of docs/superpowers/plans/
    2026-09-03-tier1-backend-hygiene.md."""
    risk = _risk(tmp_path, monkeypatch, starting_bankroll=0.0, max_daily_loss_pct=0.1)
    # Must not raise ZeroDivisionError, and must not halt on a baseline
    # that was never really a baseline (matches ShadowTrader's own
    # documented "return True" - trading continues, same forgiving default).
    assert risk.check_daily_loss(0.0) is True
    assert risk.halted is False
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_risk_manager.py -k zero_bankroll -q"`
Expected: **fails** — `ZeroDivisionError: float division by zero`, against
current `main`.

- [ ] **Step 3: Apply the fix**

Change:

```python
        self._maybe_rollover_day(current_bankroll, now)
        if not self.kill_switch_enabled or self.halted:
            return not self.halted
        loss_pct = (self.day_start_bankroll - current_bankroll) / self.day_start_bankroll
```

to:

```python
        self._maybe_rollover_day(current_bankroll, now)
        if not self.kill_switch_enabled or self.halted:
            return not self.halted
        # 2026-09-03, Task 3b of docs/superpowers/plans/2026-09-03-tier1-
        # backend-hygiene.md: mirrors ShadowTrader.check_daily_loss's own
        # already-shipped guard (services/shadow_mode.py:145-147) - without
        # it, a day_start_bankroll of exactly 0 (reachable via reset_day at
        # a UTC date rollover) raises ZeroDivisionError in the REAL kill
        # switch, while the inert shadow copy was already protected.
        if not self.day_start_bankroll:
            return True
        loss_pct = (self.day_start_bankroll - current_bankroll) / self.day_start_bankroll
```

- [ ] **Step 4: Run the new test plus the full file, confirm pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_risk_manager.py -q"`
Expected: all pass, including the new one and every pre-existing test
(this is a pure `if not X: return True` guard added *before* the existing
division, so no existing non-zero-bankroll test's behavior changes).

#### Task 3c: One canonical DDL string per table (`raw_trades`/`rejection_events`/`rejected_candidates`)

**Files:**
- Modify: `services/capture_writer.py:132-190` (`_STORE_DDL` — extract
  each value to a named module-level constant; no functional change to
  the SQL text itself, this module's own copies are already the correct,
  final-column-set shape)
- Modify: `services/series_watcher.py:159-180,219-240` (`_connect`,
  `_ensure_schema_aio` — both use `capture_writer.RAW_TRADES_DDL_SQL`
  instead of their own hand-typed, currently byte-identical copies;
  `services/series_watcher.py:67` already does `from services import
  capture_writer`, no new import)
- Modify: `services/candidate_log.py:85-107` (`_connect` — uses
  `capture_writer.REJECTED_CANDIDATES_DDL_SQL`/
  `capture_writer.REJECTION_EVENTS_DDL_SQL`; `services/candidate_log.py:62`
  already does `from services import capture_writer`, no new import; the
  `_add_column_if_missing(conn, "rejected_candidates"/"rejection_events",
  "unit_cost", "REAL")` calls at lines 143-144 are UNCHANGED — see
  Interfaces below for why)
- Test: `tests/test_capture_writer.py`, `tests/test_series_watcher.py`,
  `tests/test_candidate_log.py` (extend all three existing files)

**Import-direction constraint, verified before designing this task, not
assumed:** `grep -n "^from services\|^import" services/capture_writer.py
services/series_watcher.py services/candidate_log.py` confirms
`series_watcher.py:67` and `candidate_log.py:62` both `from services
import capture_writer`, and `capture_writer.py` imports nothing from
either of them (only `logging`, `time`, `pathlib.Path`, `services.
fault_log`). `capture_writer.py`'s own module docstring already states why
it doesn't reuse `series_watcher.py`'s DDL today: "Owns its own DDL per
store... even though 'raw_trades' already exists via services/
series_watcher.py's own _connect()" — the reason is this exact import
direction (importing series_watcher/candidate_log back into
capture_writer would be circular). This task's design therefore makes
`capture_writer.py` the DDL owner and has the other two import FROM it —
the only direction the existing import graph allows without restructuring
it, which this narrow task does not do.

**Interfaces:** `capture_writer._STORE_DDL` (currently a dict of inline
triple-quoted strings) is populated from three new module-level string
constants: `RAW_TRADES_DDL_SQL`, `REJECTION_EVENTS_DDL_SQL`,
`REJECTED_CANDIDATES_DDL_SQL` — same exact SQL text, now named and
importable. `capture_writer.py`'s own `rejection_events`/
`rejected_candidates` DDL already bakes `unit_cost` into the initial
`CREATE TABLE` (confirmed via direct read — its own comment says so:
"Matches services/candidate_log.py's real schema exactly (including
unit_cost, which that module adds via ALTER TABLE after its own initial
CREATE — baked directly into this DDL instead...)"), while
`candidate_log.py`'s own literal `CREATE TABLE` statements do NOT have
`unit_cost` (added via `_add_column_if_missing` afterward, confirmed by
direct read of `candidate_log.py:85-124` and its `:143-144` migration
calls). Switching `candidate_log.py`'s `_connect()` to use
`capture_writer`'s already-`unit_cost`-inclusive DDL is **safe and
behavior-preserving on the live file**: `CREATE TABLE IF NOT EXISTS` is a
no-op on a table that already exists (the live `candidate_log.db`), so
this change cannot retroactively alter that file's current schema; on a
FRESH database it now gets `unit_cost` immediately from `CREATE` instead
of via a follow-up `ALTER`, and the `_add_column_if_missing` calls stay in
place, becoming a harmless no-op for a table that already has the column
(covering any environment whose file predates it) — kept as defensive
insurance, not redundant dead code, since this task cannot verify every
possible deployment's file history.

- [ ] **Step 1: Confirm all three current DDL bodies match, before editing**

`services/capture_writer.py:132-190`, `services/series_watcher.py:159-180`
(sync `_connect`) and `:219-240` (async `_ensure_schema_aio`), and
`services/candidate_log.py:85-107` (`_connect`'s two `CREATE TABLE`
blocks) — read each fresh via `sed -n` and confirm they still match this
task's own transcriptions below before editing. This task's own research
already confirmed `series_watcher.py`'s two copies of `raw_trades` DDL are
byte-identical to each other and to `capture_writer.py`'s copy, and that
`candidate_log.py`'s two copies differ from `capture_writer.py`'s only by
the missing `unit_cost` column — re-confirm this hasn't drifted since.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_capture_writer.py` (check its existing fixture/import
style first via `grep -n "^def _\|^import\|^from" tests/
test_capture_writer.py`):

```python
def test_ddl_constants_are_exported_and_match_the_dict():
    """Task 3c of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md - the three DDL strings become named, importable module
    constants (not just dict values), so series_watcher.py/candidate_log.py
    can import them instead of hand-copying the SQL text."""
    from services import capture_writer as cw

    assert cw._STORE_DDL["raw_trades"] is cw.RAW_TRADES_DDL_SQL
    assert cw._STORE_DDL["rejection_events"] is cw.REJECTION_EVENTS_DDL_SQL
    assert cw._STORE_DDL["rejected_candidates"] is cw.REJECTED_CANDIDATES_DDL_SQL
```

Add to `tests/test_series_watcher.py`:

```python
def test_connect_uses_the_shared_raw_trades_ddl(tmp_path, monkeypatch):
    """No more hand-duplicated CREATE TABLE text - services/series_watcher.py's
    _connect() creates the same raw_trades table capture_writer.py's shared
    RAW_TRADES_DDL_SQL defines, confirmed by actually creating a fresh table
    via _connect() and reading its real column list back via PRAGMA
    table_info, not by comparing source strings."""
    import sqlite3
    from services import series_watcher as sw

    monkeypatch.setattr(sw, "DB_PATH", tmp_path / "series_watcher.db")
    with sw._connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(raw_trades)").fetchall()]
    assert "trade_id" in cols and "raw_json" in cols and len(cols) == 16
```

Add to `tests/test_candidate_log.py`:

```python
def test_connect_creates_rejected_candidates_with_unit_cost_from_ddl(tmp_path, monkeypatch):
    """Task 3c: candidate_log.py's _connect() now creates rejected_candidates
    (and rejection_events) from capture_writer's shared, unit_cost-inclusive
    DDL directly, rather than relying on _add_column_if_missing to backfill
    it after a bare CREATE - on a FRESH db the column exists from the start.
    _add_column_if_missing stays as a no-op safety net for pre-existing
    files (unchanged, not removed by this task)."""
    import sqlite3
    from services import candidate_log as cl

    monkeypatch.setattr(cl, "DB_PATH", tmp_path / "candidate_log.db")
    with cl._connect() as conn:
        rc_cols = [r[1] for r in conn.execute("PRAGMA table_info(rejected_candidates)").fetchall()]
        re_cols = [r[1] for r in conn.execute("PRAGMA table_info(rejection_events)").fetchall()]
    assert "unit_cost" in rc_cols
    assert "unit_cost" in re_cols
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_capture_writer.py -k ddl_constants -q tests/test_series_watcher.py -k shared_raw_trades_ddl -q tests/test_candidate_log.py -k unit_cost_from_ddl -q"`
Expected: `test_ddl_constants_are_exported_and_match_the_dict` **fails**
(`AttributeError`, constants don't exist yet); the other two currently
**pass already** (both tables already end up with the right columns via
today's separate mechanisms) — they exist as regression pins for the
refactor, not new-behavior tests; note this honestly rather than claiming
a red-then-green cycle that doesn't apply to them.

- [ ] **Step 3: Extract the three named constants in `capture_writer.py`**

Change:

```python
_STORE_DDL: dict[str, str] = {
    "raw_trades": """
        CREATE TABLE IF NOT EXISTS raw_trades (
            ...
        )
    """,
    ...
    "rejection_events": """
        CREATE TABLE IF NOT EXISTS rejection_events (
            ...
        )
    """,
    ...
    "rejected_candidates": """
        CREATE TABLE IF NOT EXISTS rejected_candidates (
            ...
        )
    """,
}
```

to (same exact SQL text in each block, now assigned to named constants
first, confirmed against Step 1's fresh read before this edit lands):

```python
RAW_TRADES_DDL_SQL = """
    CREATE TABLE IF NOT EXISTS raw_trades (
        trade_id TEXT PRIMARY KEY,
        ticker TEXT NOT NULL,
        series TEXT NOT NULL,
        observed_at REAL NOT NULL,
        exchange_ts REAL,
        taker_outcome_side TEXT,
        taker_book_side TEXT,
        taker_side_legacy TEXT,
        resolved_side TEXT,
        count_fp REAL,
        yes_price_dollars REAL,
        no_price_dollars REAL,
        notional_usd REAL,
        is_block_trade INTEGER,
        excluded INTEGER NOT NULL DEFAULT 0,
        raw_json TEXT NOT NULL
    )
"""
# Shared with services/series_watcher.py's _connect()/_ensure_schema_aio()
# (2026-09-03, Task 3c of docs/superpowers/plans/2026-09-03-tier1-backend-
# hygiene.md) - previously three independent hand-typed copies (this
# module plus series_watcher.py's own sync AND async schema-init
# functions), with a self-documented "keep the two DDL blocks in sync by
# hand" comment in series_watcher.py. This module owns the constant
# because it has no import dependency on series_watcher.py/candidate_log.py
# (they both already import IT) - the only direction that doesn't create
# a circular import.
REJECTION_EVENTS_DDL_SQL = """
    CREATE TABLE IF NOT EXISTS rejection_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        strategy TEXT NOT NULL,
        gate_name TEXT NOT NULL,
        observed_value REAL,
        threshold_value REAL,
        side TEXT,
        rejected_at REAL NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        resolved_at REAL,
        unit_cost REAL
    )
"""
# Shared with services/candidate_log.py's _connect() (same reasoning as
# RAW_TRADES_DDL_SQL above). Matches candidate_log.py's real schema
# exactly, unit_cost baked in from the start here (candidate_log.py's own
# _add_column_if_missing migration stays, as a no-op safety net for any
# pre-existing file created before this shared constant existed).
REJECTED_CANDIDATES_DDL_SQL = """
    CREATE TABLE IF NOT EXISTS rejected_candidates (
        ticker TEXT NOT NULL,
        strategy TEXT NOT NULL,
        gate_name TEXT NOT NULL,
        observed_value REAL,
        threshold_value REAL,
        side TEXT,
        rejected_at REAL NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        result TEXT,
        resolved_at REAL,
        unit_cost REAL,
        PRIMARY KEY (ticker, strategy, gate_name)
    )
"""
_STORE_DDL: dict[str, str] = {
    "raw_trades": RAW_TRADES_DDL_SQL,
    "rejection_events": REJECTION_EVENTS_DDL_SQL,
    "rejected_candidates": REJECTED_CANDIDATES_DDL_SQL,
}
```

- [ ] **Step 4: Point `series_watcher.py`'s two schema-init functions at
      the shared constant**

In `services/series_watcher.py`'s `_connect()`, change:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_trades (
            trade_id TEXT PRIMARY KEY,
            ...
        )
        """
    )
```

to:

```python
    conn.execute(capture_writer.RAW_TRADES_DDL_SQL)
```

and in `_ensure_schema_aio()`, change:

```python
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_trades (
            trade_id TEXT PRIMARY KEY,
            ...
        )
        """
    )
```

to:

```python
    await conn.execute(capture_writer.RAW_TRADES_DDL_SQL)
```

Update `_ensure_schema_aio()`'s own docstring (currently: "Duplicated
rather than shared with `_connect()` because one is sync... keep the two
DDL blocks in sync by hand if this table's schema ever changes") to say
this is now a single shared constant, not two hand-kept-in-sync copies —
the plain SQL string works identically for both `conn.execute(sql)` (sync)
and `await conn.execute(sql)` (aiosqlite), since only the caller's
`execute` differs, not the string itself.

- [ ] **Step 5: Point `candidate_log.py`'s `_connect()` at the shared
      constants**

Change:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rejected_candidates (
            ticker TEXT NOT NULL,
            ...
            PRIMARY KEY (ticker, strategy, gate_name)
        )
        """
    )
    ...
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rejection_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ...
        )
        """
    )
```

to:

```python
    conn.execute(capture_writer.REJECTED_CANDIDATES_DDL_SQL)
    ...
    conn.execute(capture_writer.REJECTION_EVENTS_DDL_SQL)
```

keeping every `CREATE INDEX IF NOT EXISTS` statement and the
`_add_column_if_missing(conn, "rejected_candidates"/"rejection_events",
"unit_cost", "REAL")` calls at lines 143-144 exactly as they are (both
unrelated to which DDL string built the table).

- [ ] **Step 6: Run all three tests files, confirm pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_capture_writer.py tests/test_series_watcher.py tests/test_candidate_log.py -q"`
Expected: all pass, including the three new ones.

- [ ] **Step 7: Broader regression check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/ -k 'raw_trades or rejection_events or rejected_candidates' -q"`
Expected: no regressions — this change alters only WHICH function's DDL
string executes, never the table shape, index list, or any query.

---

### Task 4: `record_snapshot_from_ticker` off the event loop

**Files:**
- Modify: `services/whale_stream/whale_stream_handlers.py:316-338`
  (`_process_stream_ticker`'s `matched_market is not None` block; add
  `from services import tick_executor` — confirm not already imported via
  `grep -n "^from services import tick_executor\|^import asyncio"
  services/whale_stream/whale_stream_handlers.py` first)
- Test: `tests/test_whale_stream_stage_timing.py` (extend existing file —
  already imports `whale_stream_handlers as wsh`, confirmed by Tier 0's
  precedent plan's own Task 4 citation, re-verify fresh before writing)
- **No change to `services/market_history.py`** — `record_snapshot_
  from_ticker`'s own signature/body stays exactly as-is (see Interfaces
  below for why), and its `_connect()` is one of Tier 0's five target
  functions, not yet fixed (confirmed by this plan's own Live
  re-verification section) — this task does not touch it or interact with
  Tier 0's eventual diff there.

**Interfaces — a deliberate deviation from PR #414's exact pattern, stated
explicitly:** PR #414 (`docs/superpowers/plans/2026-09-01-event-loop-
blocking-fix1.md`) converted 4 sibling functions
(`ingestion.record_cfbenchmarks`/`record_pyth`, `settlement_edge.
record_observation`, `game_state.record`, `series_watcher.record_book`)
from "write inline" to "accumulate in memory, return `(accepted,
should_flush)`, let the caller schedule the actual flush via
`tick_executor.run()`." `record_snapshot_from_ticker` does not fit that
shape: it has no accumulation buffer — every non-throttled call is its own
complete, immediate single-row write (confirmed by direct read of
`services/market_history.py:165-206`). Converting the FUNCTION itself to
match the four siblings' shape would mean rewriting its own throttle-check
contract and would break all 3 of its existing tests
(`test_record_snapshot_from_ticker_is_throttled_per_ticker`,
`test_record_snapshot_from_ticker_never_raises_and_logs_the_fault`,
`test_record_snapshot_from_ticker_writes_a_row`), which call it directly
and synchronously and check the DB immediately after. Instead, this task
moves ONLY the call site: `market_history.record_snapshot_from_ticker`
itself is untouched (still fully valid to call directly, synchronously,
exactly as its own tests already do); `_process_stream_ticker` schedules
it via `tick_executor.run()` + `asyncio.create_task()` (the same
fire-and-forget idiom PR #414 used for its 4 siblings' *flush* calls, just
applied to this function's own single write instead of a buffer flush).

**Known, small, accepted risk, stated explicitly (not glossed over):**
`record_snapshot_from_ticker`'s own throttle state
(`_last_ticker_snapshot`, a plain module-level dict, `get()` then
`[ticker] = now`, not atomic as a compound operation) currently only ever
runs on the single event-loop thread. After this task, it runs on
whichever of `tick_executor`'s 2 worker threads picks up the scheduled
call. Two calls for the SAME ticker landing on both workers at nearly the
same instant could both pass the throttle check before either updates it,
producing one slightly-more-frequent-than-intended snapshot write — a
minor timing/accuracy issue (an extra row inside one throttle window), not
a data-loss or correctness catastrophe (each write is independent, not
partially-applied). Not fixed by this task (would need a lock, itself a
new contention point on a 2-worker pool this file's own module docstring
already treats carefully); recorded here as a disclosed, accepted
tradeoff, not silently introduced.

- [ ] **Step 1: Confirm the exact current call site**

`services/whale_stream/whale_stream_handlers.py:316-338`:

```python
    if matched_market is not None:
        # Raises market_history's real time resolution using data already
        # in this message - see market_history.record_snapshot_from_ticker's
        # docstring and docs/next-session-pickup-2026-08-17.md's REST-vs-
        # websocket architecture finding (item #3, "smallest, lowest-risk").
        # volume_24h/close_time come from the cached REST market object
        # (matched_market), not the ticker message - the ws ticker channel
        # only carries all-time volume_fp (docs/kalshi/market-ticker.md),
        # and labeling that "volume_24h" would be exactly the kind of
        # mislabeled-value bug CLAUDE.md already documents twice.
        yes_bid_raw = ticker_msg.get("yes_bid_dollars")
        yes_ask_raw = ticker_msg.get("yes_ask_dollars")
        spread = None
        if yes_bid_raw is not None and yes_ask_raw is not None:
            try:
                spread = max(float(yes_ask_raw) - float(yes_bid_raw), 0.0)
            except (TypeError, ValueError):
                spread = None
        market_history.record_snapshot_from_ticker(
            ticker, state["latest_prices"][ticker], spread=spread,
            volume_24h=float(matched_market.get("volume_24h_fp") or 0.0),
            close_time=matched_market.get("close_time"), now=now,
        )
```

Confirm it still matches before editing — if not, stop and re-derive from
current source.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_whale_stream_stage_timing.py`, matching Tier 0's own
precedent test's exact shape for this same file/function
(`test_process_stream_ticker_schedules_flush_via_tick_executor_when_told`,
confirmed via `docs/superpowers/plans/2026-09-01-event-loop-blocking-
fix1.md`'s own Step 7):

```python
def test_process_stream_ticker_schedules_snapshot_write_via_tick_executor(monkeypatch):
    """Task 4 of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md
    (§4.5 of the architecture-audit-second-pass research): record_snapshot_
    from_ticker did a synchronous `with _connect(DB_PATH)` SQLite write per
    throttled ticker message, directly on the event loop, with no thread
    hop at all - the exact bug class PR #414 already fixed for 4 sibling
    functions. Unlike those, this function has no accumulation buffer, so
    the fix is at the call site (schedule the whole call via tick_executor,
    not modify record_snapshot_from_ticker's own signature)."""
    from services import market_history, tick_executor

    scheduled = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro):
        scheduled.append(coro)
        return real_create_task(coro)

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    tick_executor_calls = []

    async def fake_tick_executor_run(fn):
        tick_executor_calls.append(fn)
        return fn()

    monkeypatch.setattr(tick_executor, "run", fake_tick_executor_run)
    snapshot_calls = []
    monkeypatch.setattr(market_history, "record_snapshot_from_ticker",
                         lambda *a, **k: snapshot_calls.append((a, k)) or True)

    # Match this file's existing setup for reaching _process_stream_ticker
    # with a matched_market present - confirm the exact fixture/state shape
    # via `grep -n "_process_stream_ticker\|matched_market\|def _fresh_state"
    # tests/test_whale_stream_stage_timing.py` before writing, since this
    # task's own research confirmed the call site but not this file's full
    # existing state-setup helper.
    ...

    assert len(scheduled) == 1
    assert tick_executor_calls[0] is not None
    assert len(snapshot_calls) == 1
```

**Disclosed placeholder**, same shape as Task 3a's: this file's own
existing state/fixture setup for getting `_process_stream_ticker` to reach
the `matched_market is not None` branch (a market present in
`state["markets"]` matching the ticker) was not fully traced by this
plan's research pass. Confirm via direct read of this file's existing
tests reaching `_process_stream_ticker` before filling in the `...`.

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_whale_stream_stage_timing.py -k schedules_snapshot_write -q"`
Expected: **fails** (once filled in) — `scheduled` empty, `snapshot_calls`
has 1 entry (called inline, synchronously, not scheduled).

- [ ] **Step 3: Apply the fix**

Confirm `services/whale_stream/whale_stream_handlers.py` already imports
`asyncio` (it does — used elsewhere in this same function, line 289's
`asyncio.create_task(tick_executor.run(series_watcher.flush))`). Add `from
services import tick_executor` if not already present (check first — this
file already imports `series_watcher`, which itself imports
`tick_executor`, but this file needs its OWN import, not a transitive
one).

Change:

```python
    if matched_market is not None:
        # Raises market_history's real time resolution using data already
        # in this message - see market_history.record_snapshot_from_ticker's
        # docstring and docs/next-session-pickup-2026-08-17.md's REST-vs-
        # websocket architecture finding (item #3, "smallest, lowest-risk").
        # volume_24h/close_time come from the cached REST market object
        # (matched_market), not the ticker message - the ws ticker channel
        # only carries all-time volume_fp (docs/kalshi/market-ticker.md),
        # and labeling that "volume_24h" would be exactly the kind of
        # mislabeled-value bug CLAUDE.md already documents twice.
        yes_bid_raw = ticker_msg.get("yes_bid_dollars")
        yes_ask_raw = ticker_msg.get("yes_ask_dollars")
        spread = None
        if yes_bid_raw is not None and yes_ask_raw is not None:
            try:
                spread = max(float(yes_ask_raw) - float(yes_bid_raw), 0.0)
            except (TypeError, ValueError):
                spread = None
        market_history.record_snapshot_from_ticker(
            ticker, state["latest_prices"][ticker], spread=spread,
            volume_24h=float(matched_market.get("volume_24h_fp") or 0.0),
            close_time=matched_market.get("close_time"), now=now,
        )
```

to:

```python
    if matched_market is not None:
        # Raises market_history's real time resolution using data already
        # in this message - see market_history.record_snapshot_from_ticker's
        # docstring and docs/next-session-pickup-2026-08-17.md's REST-vs-
        # websocket architecture finding (item #3, "smallest, lowest-risk").
        # volume_24h/close_time come from the cached REST market object
        # (matched_market), not the ticker message - the ws ticker channel
        # only carries all-time volume_fp (docs/kalshi/market-ticker.md),
        # and labeling that "volume_24h" would be exactly the kind of
        # mislabeled-value bug CLAUDE.md already documents twice.
        yes_bid_raw = ticker_msg.get("yes_bid_dollars")
        yes_ask_raw = ticker_msg.get("yes_ask_dollars")
        spread = None
        if yes_bid_raw is not None and yes_ask_raw is not None:
            try:
                spread = max(float(yes_ask_raw) - float(yes_bid_raw), 0.0)
            except (TypeError, ValueError):
                spread = None
        # 2026-09-03, Task 4 of docs/superpowers/plans/2026-09-03-tier1-
        # backend-hygiene.md (§4.5 of the architecture-audit-second-pass
        # research): record_snapshot_from_ticker's own `with
        # _connect(DB_PATH)` SQLite write ran synchronously on the event
        # loop, per throttled ticker message - the same bug class PR #414
        # fixed for 4 sibling functions. Unlike those, this function has no
        # accumulation buffer to flush later, so the fix is here at the
        # call site: schedule the WHOLE call via tick_executor.run(),
        # capturing every argument as a plain value first (not inside the
        # lambda) so the deferred call on the tick-executor worker thread
        # never reads a possibly-mutated state["latest_prices"][ticker] or
        # matched_market by the time it actually runs.
        snapshot_price = state["latest_prices"][ticker]
        snapshot_volume_24h = float(matched_market.get("volume_24h_fp") or 0.0)
        snapshot_close_time = matched_market.get("close_time")
        asyncio.create_task(tick_executor.run(
            lambda: market_history.record_snapshot_from_ticker(
                ticker, snapshot_price, spread=spread,
                volume_24h=snapshot_volume_24h,
                close_time=snapshot_close_time, now=now,
            )
        ))
```

- [ ] **Step 4: Run the new test, confirm it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_whale_stream_stage_timing.py -k schedules_snapshot_write -q"`
Expected: passes.

- [ ] **Step 5: Confirm `market_history.py`'s own 3 existing tests are
      untouched and still pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_market_history.py -q"`
Expected: all pass, unchanged — `record_snapshot_from_ticker` itself was
not modified.

- [ ] **Step 6: Full-file regression check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_whale_stream_stage_timing.py tests/test_whale_stream_handlers.py -q"`
(confirm the second file's real name via `ls tests/ | grep -i
whale_stream` first — this plan's own research did not confirm its exact
filename.)
Expected: no regressions.

---

### Task 5: `config_store.update()` stops destroying comments (and doesn't introduce a new bug fixing it)

**Files:**
- Modify: `services/config/config_store.py:154-181` (`update`)
- Test: `tests/test_config_store.py` (extend existing file — already has
  `test_update_preserves_existing_comments`/
  `test_update_preserves_comments_on_an_untouched_section`, confirmed by
  direct read; this task's new test covers a DIFFERENT, currently-
  uncovered case, explained below)
- **No change to `config/settings.yaml`** and **no change to
  `frontend/src/js/config-panel.js`** — see Interfaces below for why the
  frontend's existing whole-section resend behavior does not need to
  change for this fix to be safe.

**Verified mechanism (falsified experimentally, not assumed from the
audit's own disclosed-unverified claim) — done 2026-09-03, against a
scratch copy of the real file's shape only, never the live file:**

A synthetic config matching `config/settings.yaml`'s real shape
(`whale_watcher_kalshi.min_contracts_by_series` immediately followed by a
comment block, then `whale_confidence_weights:`) was loaded via this
project's own `ruamel.yaml` settings (`YAML()`, `preserve_quotes = True`,
`indent(mapping=2, sequence=2, offset=0)` — matching `config_store.py`'s
own module-level `_yaml` object exactly). Applying today's `update()`
logic (`self._data[key].update(value)`, one level deep) with a patch
shaped exactly like `config-panel.js`'s real `whale_watcher_kalshi` patch
(`{"min_contracts": ..., "min_contracts_by_series": {...}}`) **reproduced
the comment loss**: the comment sitting immediately after
`min_contracts_by_series`'s last entry was gone from the re-dumped output,
even though `self._data["whale_watcher_kalshi"]` (the parent map) was
never replaced — only its `min_contracts_by_series` child VALUE was, via
`dict.update()`'s plain `dst[k] = v` assignment. **A recursive merge one
level deeper (merging `min_contracts_by_series`'s own keys into the
EXISTING ruamel map object in place, rather than replacing the whole
value) preserves the comment**, confirmed by the same experiment.

**But a second experiment found a real, new hazard in that "just deep-
merge everything" fix:** when a patch's leaf dict has a *different key
set* than what's currently on disk (e.g. a user removes one series from
the `min_contracts_by_series` text field and adds another), a naive
recursive merge with no deletion logic leaves the removed key orphaned
forever (contradicting the user's explicit edit), and a variant that DOES
delete stale keys, applied uniformly at every level, also deleted an
UNRELATED sibling field (`enabled`) at the OUTER level that simply wasn't
part of that specific patch — a much worse regression than the comment
loss this task fixes. This directly shaped the design below: recursive
merge with NO deletion is the safe default at every level (matches
today's own "never touch a key the patch doesn't mention" safety property,
just extended past one level), and exactly ONE known field
(`whale_watcher_kalshi.min_contracts_by_series` — the one field
`config-panel.js` always resends as a complete rebuilt map from a text
input, confirmed via `frontend/src/js/config-panel.js:194-201`'s
`Object.fromEntries(...)`) gets explicit delete-then-merge-in-place
semantics, scoped to that one dotted path only.

**Interfaces:** `ConfigStore.update(patch: dict)` keeps its exact current
signature and return value (`dict(self._data)`). Its internal merge logic
changes from one-level `dict.update()` to a recursive helper with one
small, explicit allowlist of full-replace-with-deletion paths.
`config-panel.js`'s Save logic is UNCHANGED — it may keep resending whole
sections; that behavior is what made the bug reachable but is not itself
what this task fixes (per the audit's own "deep-merge OR per-field PATCH"
framing, this task takes the deep-merge branch, the smaller, self-
contained, purely-server-side option, and states that as a deliberate
scope choice, not a silent omission of the per-field-PATCH alternative).

**Explicitly out of scope, named rather than silently skipped:**
restoring `config/settings.yaml`'s already-wiped calibration-audit
comment (a human decision per `docs/open-decisions.md`'s existing tracked
line — "Restore the comment only after the merge is fixed or it will be
wiped a fourth time"; this task fixes the merge, does not itself restore
anything) and moving config documentation out of the machine-written YAML
entirely (the audit's own Tier 2 "pydantic schema + values-only YAML"
recommendation, `§6.3`/`§9.2` — a materially larger redesign this Tier-1
task does not attempt). `strategy_overrides.by_category`/`.by_series`
(PR #389's own prior incident, a DIFFERENT wipe-to-`{}` mechanism, likely
a caller sending an empty patch object rather than this same merge-depth
bug) are not covered by this task's one-path allowlist — flagged as a
related-but-unverified case for a future session to check, not assumed
covered.

- [ ] **Step 1: Confirm the current `update()` matches, before editing**

`services/config/config_store.py:154-166`:

```python
    def update(self, patch: dict):
        """Shallow-merge a patch into the top-level config and persist it."""
        with self._lock:
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(self._data.get(key), dict):
                    self._data[key].update(value)
                else:
                    self._data[key] = value
```

Confirm it still matches before editing — if not, stop and re-derive from
current source. (Lines 167-181, the atomic-write logic, are untouched by
this task and are not reproduced here — re-read them fresh anyway before
editing, since this task inserts new code directly above that block.)

- [ ] **Step 2: Write the failing test for the actual bug**

Add to `tests/test_config_store.py`, matching its existing
`test_update_preserves_comments_on_an_untouched_section`'s `_write_text`/
`ConfigStore(path=path)` style — this is a NEW case the two existing
comment tests don't cover (both existing tests patch a plain top-level
scalar field; this one patches a NESTED dict field, reproducing the real
incident's actual shape):

```python
def test_update_preserves_a_comment_trailing_a_nested_dict_field(tmp_path):
    """The real incident (docs/open-decisions.md, 3 documented comment
    wipes; §4.4 of docs/superpowers/research/2026-09-02-architecture-audit-
    second-pass.md): a comment sitting immediately after a NESTED dict
    field's last entry (not a top-level scalar - the two existing comment
    tests above don't cover this shape) was destroyed because update()'s
    one-level dict.update() replaces that nested dict's VALUE wholesale
    with a brand-new plain dict object, even though the PARENT map object
    is never replaced. Reproduces the real config-panel.js patch shape for
    whale_watcher_kalshi.min_contracts_by_series exactly."""
    path = tmp_path / "settings.yaml"
    _write_text(path, (
        "whale_watcher_kalshi:\n"
        "  enabled: true\n"
        "  min_contracts: 5000\n"
        "  min_contracts_by_series:\n"
        "    KXBTC15M: 100\n"
        "    KXETH15M: 90\n"
        "# calibration-audit comment block\n"
        "# second line\n"
        "whale_confidence_weights:\n"
        "  depth_factor: 0.04\n"
    ))
    store = ConfigStore(path=path)

    store.update({
        "whale_watcher_kalshi": {
            "min_contracts": 5000.0,
            "min_contracts_by_series": {"KXBTC15M": 100.0, "KXETH15M": 90.0},
        },
    })

    on_disk = path.read_text()
    assert "# calibration-audit comment block" in on_disk
    # The values really did round-trip through the patch, not a no-op.
    assert store.get()["whale_watcher_kalshi"]["min_contracts_by_series"]["KXBTC15M"] == 100.0
    # enabled (untouched by this patch, and not part of the
    # min_contracts_by_series allowlist) must survive unchanged - the
    # regression this task's own second experiment found in a naive
    # "delete every stale key at every level" fix.
    assert store.get()["whale_watcher_kalshi"]["enabled"] is True


def test_update_replaces_min_contracts_by_series_keys_entirely_not_merges_them(tmp_path):
    """whale_watcher_kalshi.min_contracts_by_series is the one field
    config-panel.js always resends as a complete rebuilt map from a text
    input (Object.fromEntries over the whole comma-separated field) - a
    series removed from that field must actually disappear from the saved
    config, not linger as an orphaned stale key (which a naive "merge,
    never delete" fix at every level would produce)."""
    path = tmp_path / "settings.yaml"
    _write_text(path, (
        "whale_watcher_kalshi:\n"
        "  min_contracts_by_series:\n"
        "    KXBTC15M: 100\n"
        "    KXETH15M: 90\n"
    ))
    store = ConfigStore(path=path)

    store.update({
        "whale_watcher_kalshi": {
            "min_contracts_by_series": {"KXBTC15M": 200.0, "KXSOL15M": 50.0},
        },
    })

    result = store.get()["whale_watcher_kalshi"]["min_contracts_by_series"]
    assert result == {"KXBTC15M": 200.0, "KXSOL15M": 50.0}
    assert "KXETH15M" not in result
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_config_store.py -k 'trailing_a_nested_dict_field or replaces_min_contracts_by_series' -q"`
Expected: the first test **fails** (comment gone) against current `main`;
the second **passes already** today (today's one-level replace already
handles deletion correctly for this exact field, by accident of being
too shallow to merge into it at all) — note this honestly, it is a
regression pin for Step 3's fix, not new-behavior coverage.

- [ ] **Step 3: Implement the recursive merge + one explicit full-replace
      path**

Change:

```python
    def update(self, patch: dict):
        """Shallow-merge a patch into the top-level config and persist it."""
        with self._lock:
            for key, value in patch.items():
                if isinstance(value, dict) and isinstance(self._data.get(key), dict):
                    self._data[key].update(value)
                else:
                    self._data[key] = value
```

to:

```python
    # Full-replace-with-deletion paths (2026-09-03, Task 5 of docs/
    # superpowers/plans/2026-09-03-tier1-backend-hygiene.md): the general
    # merge below never deletes a key the incoming patch doesn't mention
    # (same safety property update() has always had, just extended past
    # one level - see this task's own experiment log in the plan for why a
    # uniform "delete stale keys everywhere" policy is unsafe). Exactly one
    # field needs the opposite: whale_watcher_kalshi.min_contracts_by_series
    # is always resent as a complete, freshly-rebuilt map by config-
    # panel.js's own Object.fromEntries(...) over a single text input - a
    # series a user removes from that field must actually disappear, not
    # linger as an orphaned stale key. Dotted-path strings, checked at each
    # recursion level by the (current key path) tuple joined with ".".
    _FULL_REPLACE_PATHS = {"whale_watcher_kalshi.min_contracts_by_series"}

    def _merge_in_place(self, dst: dict, patch: dict, _path: str = "") -> None:
        for key, value in patch.items():
            key_path = f"{_path}.{key}" if _path else key
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                existing = dst[key]
                if key_path in self._FULL_REPLACE_PATHS:
                    # Delete-then-merge-in-place: the incoming dict becomes
                    # the complete truth for this one key, but the
                    # EXISTING ruamel map object is mutated (del/setitem),
                    # never replaced wholesale - replacing the object
                    # reference is what destroys an attached comment;
                    # mutating it in place does not (verified experimentally,
                    # see this task's own header).
                    for stale_key in [k for k in list(existing.keys()) if k not in value]:
                        del existing[stale_key]
                self._merge_in_place(existing, value, key_path)
            else:
                dst[key] = value

    def update(self, patch: dict):
        """Recursive merge (2026-09-03, Task 5): every nested dict field is
        merged key-by-key into the EXISTING object rather than replaced
        wholesale, which is what let ruamel's attached comments survive a
        real, 3-times-repeated live incident (docs/open-decisions.md) -
        replacing a child map's object reference is what discards a
        comment ruamel attached to it, even when the PARENT object is never
        touched. Never deletes a key the patch doesn't mention, at any
        level, except the one explicit path in _FULL_REPLACE_PATHS above.
        Does not itself restore config/settings.yaml's already-wiped
        comment - see docs/open-decisions.md for that (human) decision."""
        with self._lock:
            self._merge_in_place(self._data, patch)
```

**Note, explicit per adversarial review Finding F2:** the "to:" block above
shows only the merge-loop being swapped for the new `_merge_in_place`
call. It is not a complete replacement of `update()`'s current body —
the atomic-write block that follows (`config_store.py:167-181`: `return
dict(self._data)` plus the persist-to-disk logic) is unchanged and stays
exactly where it is, immediately after the `with self._lock:` block
shown here. Confirmed the old snippet is a unique substring of the
current file, so a literal find/replace leaves that block correctly
nested under the new code with no manual re-insertion needed.

- [ ] **Step 4: Run both new tests, confirm the first now passes and the
      second still does**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_config_store.py -q"`
Expected: all pass, including both new ones and the two pre-existing
comment tests (`test_update_preserves_existing_comments`,
`test_update_preserves_comments_on_an_untouched_section`) — the recursive
merge is a strict extension of the one-level case those already cover,
never a narrower one.

- [ ] **Step 5: Broader regression check against every existing
      `config_store.update`/`config_store_module` caller**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/ -k config_store -q && python -m pytest tests/test_config_routes.py tests/test_config_bounds.py -q"`
(confirm the exact filenames for config-route tests via `ls tests/ | grep
-i config` first.)
Expected: no regressions — every existing caller's patches are either
scalar values (unaffected, `dst[key] = value` unchanged) or dicts whose
keys the patch already fully covers (unaffected by the no-deletion
default), except `min_contracts_by_series` itself, whose deletion
semantics are now CORRECT rather than accidentally-correct-by-shallowness.

---

### Task 6: One `paginate()` dependency + TTL cache for the two expensive population-gate reads

**Research correction, stated explicitly:** this plan's own task list
cites "§9.2 finding #3" for the "bound `resolved_signals_with_factors()`/
`population_gate_summary()`" half of this item — verified against both
audit documents and found to be the WRONG citation (the second audit's own
§8 Tier-1 item 12 itself points at "= #3, #4" of the FIRST audit's own
Tier-1 list, not that document's separate §9.2 DRY-findings table, which
has an unrelated finding #3). The correct citation, read directly:
`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-
considerations.md` lines 1410-1411 ("Bound `resolved_signals_with_factors()`
and `candidate_log`'s `population_gate_summary()` reads (§5.2) — the same
pattern PR #424 already used for a sibling function") and its own §5.2
(lines 638-657). **Verified via `gh pr view 424 --json title,body`
(the authoritative check, not `git log`'s branch-name-derived subject
line, which reads only "fix/run-offline-cooperative-yield"): PR #424's
real title is "fix: revert regressive elastic pool, keep+correct
query-bound fast-follow," and its body describes exactly the pattern
the audit gestured at — `check_confidence_input_coverage`'s
previously-unscoped query bound to a purpose-matched 24h window in
`services/diagnostics/diagnostics.py`, measured savings ~1.0s/~30% per
`run_offline()` call. This is a real, directly analogous precedent —
just applied to a different sibling function than
`resolved_signals_with_factors`/`population_gate_summary`, which this
citation does not (and, per Research correction #2 below, must not)
extend the same treatment to.**

**Research correction #2, more consequential:** naively adding a
`since_ts` bound to either function, as the audit's own one-line framing
suggests, is WRONG. Both functions already document (and every current
caller confirms) that they compute a gate against a MINIMUM TOTAL SAMPLE
COUNT (`resolved_signals_with_factors`'s own docstring: "the calibration
gate cares about total resolved count, not recency"; `population_gate_
summary`'s own docstring: "Reports 'insufficient' per gate rather than a
hypothetical_win_rate computed from too few samples"). Scoping either
query to a recent window would make an UNDER-SAMPLE gate decision look
like a SUFFICIENT one, or vice versa — a real accuracy regression, not a
narrow perf fix. Verified by reading every one of `resolved_signals_with_
factors`'s 5 call sites (`services/backtest/routes.py:21`, `services/
research/research.py:155`, `services/whale_calibration/routes.py:112,147`,
`main.py:490`) and `population_gate_summary`'s 2 call sites (`services/
analytics/routes.py:104` and `services/research/research.py:221` — the
second inside `research.py`'s evidence-gated, infrequent sweep, not a
tight-polling caller, so it's unaffected by this task's design either
way) — every single one is itself either the gate computation directly or
an input to it. This task therefore does NOT touch either function's own
signature or query; it caches the two ROUTES that are genuinely hit on a
tight polling cadence. `services/analytics/routes.py:95-98`'s own comment
("proven via a live py-spy stack trace to block the event loop for
17-38s on every call") documents the pre-fix range that motivated the
2026-08-26 commit converting this function from an 18.2s per-row Python
loop to a 4.8s SQL `GROUP BY` for the same 6.2M rows (`services/
candidate_log.py`'s own `population_gate_summary()` docstring) — the
current live cost is ~4.8s, not 17-38s, and this task caches that
still-real ~4.8s-per-poll cost, not the pre-fix figure.

#### Task 6a: `paginate()` FastAPI dependency for 6 confirmed-unbounded routes

**Files:**
- Create: `services/pagination.py`
- Modify: `services/alerting/routes.py:15` (`get_alert_history`)
- Modify: `services/analytics/routes.py:75` (`get_declined_suggestions`),
  `:193` (`get_market_analyst_analyses`)
- Modify: `services/diagnostics/routes.py:212` (`get_archive_epochs`),
  `:219` (`get_archive_compare`)
- Modify: `services/backup/routes.py:49` (`get_backup_history`)
- Modify: `services/market_catalog/routes.py:178` (`search_markets`)
- Test: `tests/test_pagination.py` (new file — no existing file to extend;
  matches this repo's own one-module-one-test-file convention)

**Verified-current unbounded-route census (fresh `grep -rn "limit: int"
services/*.py services/**/*.py main.py`, cross-checked against each
route's own call chain down to its `LIMIT ?` SQL bind or fetch-fan-out
cap, done immediately before drafting this task — not copied from either
audit's own count, which may be stale):** 16 total routes accept a
`limit` query parameter. 8 already clamp it (`get_signal_history`,
`get_trading_history`, `get_reset_history`, `get_advisory_applied_changes`,
`get_confidence_calibration_history`, `get_account_orders`,
`get_observability_history`, and `get_research_history` — the last one via
`research.recent()`'s own internal `limit = max(1, min(limit, 200))`, not
a route-level clamp; confirmed by direct read). 6 are genuinely unbounded
today, confirmed end-to-end to a raw `LIMIT ?`/fetch-cap with no clamp
anywhere in the call chain:

| Route | Underlying call | Confirmed unbounded at |
|---|---|---|
| `GET /api/alerts/history` | `alerting.recent(limit)` | `services/alerting/alerting.py:191-196`, `LIMIT ?` |
| `GET /api/suggestions/declined` | `suggestion_decisions.list_declined(limit)` | `services/history/suggestion_decisions.py:84-91`, `LIMIT ?` |
| `GET /api/archive/epochs` | `trade_archive.epochs(limit)` | `services/reset/trade_archive.py:270-289`, `LIMIT ?` |
| `GET /api/archive/compare` | `trade_archive.compare(limit)` | `services/reset/trade_archive.py:311+` |
| `GET /api/backup/history` | `backup.recent(limit, tier)` | `services/backup/backup.py:243-256`, `LIMIT ?` |
| `GET /api/market-analyst/analyses` | `market_analyst_agent.recent(limit, offset, ...)` | `services/market_analyst_agent/per_market.py:302-311`, `LIMIT ? OFFSET ?` |

`search_markets` (`services/market_catalog/routes.py:178`) is a 7th,
different-shaped case: `limit` is never clamped, but it bounds a fan-out
of Kalshi REST fetches, not a direct SQL `LIMIT ?` — included below since
the SAME dependency mechanically applies (it clamps a query param before
use, agnostic to what "use" means downstream).

**Interfaces:** `services/pagination.py` exports `paginate(max_limit:
int = 200)`, a dependency FACTORY (matches FastAPI's own documented
sub-dependency pattern) returning a callable with signature `(limit: int
= 50) -> int`, clamped to `[1, max_limit]`. Each of the 7 routes above
gets `limit: int = Depends(paginate(max_limit=200))` in place of its bare
`limit: int = 50` parameter — the query parameter's own name (`?limit=`)
is unchanged (FastAPI derives it from the dependency function's own
parameter name), so this is API-compatible for every existing caller.
200 matches the majority ceiling already used by this app's 8 existing
clamped routes (`get_signal_history`, `get_trading_history`, `get_
advisory_applied_changes` all use exactly 200) — not a new, independently
chosen number.

**Explicitly out of scope:** retrofitting the 8 already-clamped routes
onto this same shared dependency (they already work; touching 8 more
routes for a pure style/DRY win increases this task's diff/risk for no
functional gain — a reasonable future cleanup, not required here, and
named so it isn't silently assumed already done).

- [ ] **Step 1: Confirm all 7 current signatures match**

Re-run the census grep and the per-route body reads from this task's own
research immediately before editing — if any route's clamp status has
changed since this plan was drafted, stop and re-scope that route out of
(or into) this task's list rather than trusting this document blindly.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_pagination.py`:

```python
"""
services/pagination.py - the paginate() FastAPI dependency factory that
closes the "16 routes accept limit, only 8 clamp it" gap (Task 6a of
docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md).
"""
from services.pagination import paginate


def test_paginate_clamps_within_bounds():
    dep = paginate(max_limit=200)
    assert dep(limit=50) == 50


def test_paginate_clamps_above_max():
    dep = paginate(max_limit=200)
    assert dep(limit=999999) == 200


def test_paginate_clamps_below_one():
    dep = paginate(max_limit=200)
    assert dep(limit=-5) == 1
    assert dep(limit=0) == 1


def test_paginate_default_is_the_dependencys_own_default():
    dep = paginate(max_limit=200)
    assert dep() == 50


def test_paginate_respects_a_different_max_limit_per_route():
    dep = paginate(max_limit=25)
    assert dep(limit=100) == 25
```

Add ONE end-to-end route test per this task's own convention, matching
whichever TestClient pattern `tests/test_alerting_routes.py` (or wherever
`get_alert_history` is currently tested, confirm the exact filename via
`grep -rln "get_alert_history\|/api/alerts/history" tests/*.py` first)
already uses:

```python
def test_get_alert_history_route_clamps_an_oversized_limit():
    """One end-to-end proof the dependency is actually wired into a real
    route, not just unit-tested in isolation - matches whichever
    TestClient/asyncio.run convention this route's own existing test file
    uses (confirm via the grep above before writing this test)."""
    ...  # fill in against the confirmed existing convention
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_pagination.py -q"`
Expected: **fails** — `ModuleNotFoundError: No module named
'services.pagination'`.

- [ ] **Step 3: Create `services/pagination.py`**

```python
"""
One shared FastAPI dependency for clamping a route's `limit` query
parameter before it reaches any SQL LIMIT ?/OFFSET ? or REST fetch-fan-out
cap. Closes the gap the second architecture audit's own research (first
audit's §9.2 finding #5, re-verified fresh 2026-09-03) found: 16 routes in
this app accept `limit`, only 8 clamped it before this file existed.

This is the app's first use of FastAPI's Depends() mechanism (2026-09-03,
Task 6a of docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md) -
`grep -rln "Depends(" services/*.py services/**/*.py` returned zero hits
before this file. Standard FastAPI machinery, not a hand-rolled
abstraction the 2026-08-30 "prefer proven" rule would flag.
"""
from fastapi import Query


def paginate(max_limit: int = 200):
    """Returns a dependency callable clamping `limit` to [1, max_limit].
    200 matches the ceiling this app's own already-clamped routes mostly
    already use (get_signal_history/get_trading_history/get_advisory_
    applied_changes all use exactly 200) - not an independently chosen
    number. Call as Depends(paginate()) for the default ceiling, or
    Depends(paginate(max_limit=N)) for a route that needs a different one."""
    def _dependency(limit: int = Query(default=50)) -> int:
        return max(1, min(limit, max_limit))
    return _dependency
```

- [ ] **Step 4: Wire it into all 7 routes**

For each of the 7 routes, add `Depends` to that file's existing `from
fastapi import ...` line (none currently import it, confirmed by this
task's own research), add `from services.pagination import paginate`, and
change the route's own `limit: int = 50` (or `= 25`/`= 20`/`= 10`,
whichever this route's current default is) parameter to `limit: int =
Depends(paginate(max_limit=200))`, keeping every OTHER parameter (`offset`,
`resolved_only`, `component`, `tier`, `q`, `min_volume`, `category`,
`live_only`, etc.) exactly as they are — this task touches only the
`limit` parameter's own default expression, nothing else in any of these
7 signatures.

Example (`services/alerting/routes.py`), change:

```python
from fastapi import APIRouter
...
@router.get("/api/alerts/history")
async def get_alert_history(limit: int = 50):
    return {"alerts": alerting.recent(limit=limit)}
```

to:

```python
from fastapi import APIRouter, Depends
...
from services.pagination import paginate
...
@router.get("/api/alerts/history")
async def get_alert_history(limit: int = Depends(paginate(max_limit=200))):
    return {"alerts": alerting.recent(limit=limit)}
```

Apply the identical transformation to the other 6 routes, each in its own
file, preserving that file's own existing import ordering/style.

- [ ] **Step 5: Run all new tests, confirm pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_pagination.py -q"`
Expected: all pass.

- [ ] **Step 6: Regression-check every modified route file**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_alerting_routes.py tests/test_analytics_routes.py tests/test_diagnostics_routes.py tests/test_backup.py tests/test_market_catalog_routes.py -q"`
(confirm each exact filename first via `ls tests/ | grep -i
"alerting\|analytics\|diagnostics\|backup\|market_catalog"` — this task's
own research did not confirm all 5.)
Expected: no regressions — every existing caller's un-parameterized
`?limit=` request (the common case, no query string at all) still
defaults to 50, unchanged.

#### Task 6b: TTL cache for `/api/confidence-calibration/report` and `/api/candidate-log/summary`

**Files:**
- Modify: `services/whale_calibration/routes.py:87-118`
  (`get_confidence_calibration_report`)
- Modify: `services/analytics/routes.py:80-106`
  (`get_candidate_log_summary`)
- Test: `tests/test_whale_calibration_routes.py`, `tests/
  test_analytics_routes.py` (extend both existing files — confirm exact
  filenames first, per Task 6a's own note)

**Interfaces:** Both routes keep their exact current response shape and
query parameters. Each gets a module-level `{"cached_at": float, "value":
dict}` cache (same idiom as `services/market_watch/discovery_cache.py`'s
`_fetch_category_metadata`, this repo's own already-established TTL-cache
pattern — `if cache.get("fetched_at") and (now - cache["fetched_at"]) <
ttl_sec: return cache`), invalidated purely by elapsed time, not by any
config/state change (both underlying computations are read-only reports
over accumulating history, not something a single write invalidates
precisely).

**Chosen TTL, stated as an estimate with reasoning:** 30 seconds. Task 2
already throttles the FRONTEND's own poll of these two routes to no more
than once per 30s (calibration report) via the History-tab loaders'
30,000ms throttle. A 30s server-side cache is the natural backstop for
callers OTHER than the throttled dashboard poll (a second browser tab, a
direct curl, `services/research/research.py`'s own periodic pull) without
being so long that a human clicking "Apply" right after a real config
change sees a stale gate result for an uncomfortably long window. Both
numbers are explicitly coordinated, not independently guessed.

- [ ] **Step 1: Confirm both current routes match**

`services/whale_calibration/routes.py:87-118` (the report route,
`_build_report()` closure calling `signal_log.resolved_signals_with_
factors()`) and `services/analytics/routes.py:80-106`
(`get_candidate_log_summary`, its own comment already recording the
17-38s live py-spy measurement) — re-read both fresh, confirm they still
match this task's own research before editing.

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_whale_calibration_routes.py`:

```python
def test_confidence_calibration_report_is_cached_within_ttl(monkeypatch):
    """Task 6b of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md: resolved_signals_with_factors() cannot be scoped with
    since_ts (it computes a total-sample gate, verified in this task's own
    research), so the fix is a short-TTL cache on the ROUTE, not a query
    bound - confirmed live cost is real (~1s at 103k+ rows per signal_
    log.py's own docstring)."""
    from services.whale_calibration import routes as wc_routes

    calls = []
    monkeypatch.setattr(wc_routes.signal_log, "resolved_signals_with_factors",
                         lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(wc_routes, "_report_cache", {"cached_at": None, "value": None})
    # match this file's existing config_store/cc_cfg mocking convention -
    # confirm via `grep -n "config_store.get\|cc_cfg" tests/
    # test_whale_calibration_routes.py` before writing

    asyncio.run(wc_routes.get_confidence_calibration_report())
    asyncio.run(wc_routes.get_confidence_calibration_report())

    assert len(calls) == 1, "second call within TTL should reuse the cached result"


def test_confidence_calibration_report_recomputes_after_ttl_expires(monkeypatch):
    ...  # advance a monkeypatched time.time() past the TTL, confirm calls == 2
```

Add the mirrored pair to `tests/test_analytics_routes.py` for
`get_candidate_log_summary`/`candidate_log.population_gate_summary`.

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_whale_calibration_routes.py -k is_cached_within_ttl -q"`
Expected: **fails** (`calls == 2`, no caching exists yet).

- [ ] **Step 3: Implement both caches**

In `services/whale_calibration/routes.py`, add near the top:

```python
import time

_REPORT_CACHE_TTL_SEC = 30  # 2026-09-03, Task 6b of docs/superpowers/
# plans/2026-09-03-tier1-backend-hygiene.md: resolved_signals_with_
# factors() cannot be query-bounded (it's a total-sample gate, not a
# recency-scoped read - verified in this task's own research). 30s matches
# Task 2's own History-tab de-poll interval for this exact route -
# coordinated, not independently chosen.
_report_cache: dict = {"cached_at": None, "value": None}
```

Change `get_confidence_calibration_report`'s body from calling
`_build_report()` via `tick_executor.run(_build_report)` unconditionally
to checking the cache first:

```python
    now = time.time()
    if _report_cache["cached_at"] is not None and (now - _report_cache["cached_at"]) < _REPORT_CACHE_TTL_SEC:
        result = dict(_report_cache["value"])
    else:
        result = await tick_executor.run(_build_report)
        _report_cache["cached_at"] = now
        _report_cache["value"] = result
    result["evidence_provenance"] = evidence_provenance.current_completeness_state()
    return result
```

(Preserve every existing line above this exactly as read in Step 1 —
`cc_cfg`/the disabled-gate early return/`_build_report`'s own closure
definition are unchanged; only the final `result = await tick_executor.
run(_build_report)` line and what follows it changes shape.)

Apply the identical pattern to `services/analytics/routes.py`'s
`get_candidate_log_summary`, caching the `population_gates` value (the
~4.8s call, per §"Research correction" above — historically 17-38s
pre-2026-08-26-fix) with its own `_population_gates_cache` dict and the
same `_POPULATION_GATES_CACHE_TTL_SEC = 30` constant, same reasoning.

- [ ] **Step 4: Run the new tests, confirm pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_whale_calibration_routes.py tests/test_analytics_routes.py -q"`
Expected: all pass.

- [ ] **Step 5: Confirm the manual-apply route (`POST /api/confidence-
      calibration/apply`) is NOT accidentally served stale cached data**

`services/whale_calibration/routes.py:125-150`'s
`apply_confidence_calibration_suggestion` has its OWN `_build_report()`
call, separate from the GET route's. Confirm this task's Step 3 change did
NOT touch that second call site (a human clicking "Apply" must always act
on a freshly-computed report, never a cached one — re-read the current
file to confirm this distinction after editing, don't assume it from the
plan alone).

---

### Task 7: `event_live_data` throttled to its own real refresh cadence; `bump_generation()` coarsened

**Files:**
- Modify: `main.py:1525-1608` (`_build_state_body`)
- Modify: `services/app_state.py:397-406` (`state["generation"]`'s
  comment, `bump_generation`)
- Test: `tests/test_state_view.py` or wherever `_build_state_body`/
  `get_state` are currently tested (confirm exact filename via `grep -rln
  "_build_state_body\|def test_get_state" tests/*.py` first — this task's
  own research did not confirm it, since `main.py`'s own test coverage is
  spread across several files per Tier 0's own prior finding about this
  file's test conventions)
- Test: `tests/test_app_state.py` (**new file** — none exists today,
  confirmed by direct `ls tests/`; `bump_generation`'s coarsening is
  cleanly testable in isolation without the heavyweight `import main`
  setup `test_trading_gate.py` needs, since `tests/conftest.py`'s
  module-level `install_runtime_isolation()` — confirmed by direct read,
  line 11, runs before any test module is collected — already makes a
  bare `from services import app_state` safe without a bespoke per-file
  DB-redirection dance)

**Data-plane tradeoffs, named explicitly per the HARD RULE — both verified
to have zero trading-decision dependency, not assumed:**

1. **`event_live_data`'s HTTP-send cadence, not its underlying refresh.**
   `grep -rn 'state\["event_live_data"\]' services/*.py services/**/*.py
   main.py` shows exactly two touch points in the entire backend:
   `main.py:953` (the periodic REST-poll writer) and `services/state_view.
   py:200` (`_scoped_event_live_data`, read only by `_build_state_body`).
   Nothing trading-decision-facing reads it. The underlying data itself
   already refreshes no more than once every `_EVENT_LIVE_DATA_REPOLL_SEC`
   = 60 seconds per event (`services/market_watch/event_metadata.py:129`,
   confirmed live), so sending it on every 6-30s `/api/state` poll already
   re-sends unchanged data most of the time. This task throttles the SEND
   to match the EXISTING 60s refresh ceiling — reusing that constant
   directly rather than picking an independent number, so the two can
   never drift apart.
2. **`bump_generation()`'s coarsening affects `_build_state_body()`'s
   OWN memoization, not just the ETag.** `_build_state_body()` already
   returns the SAME cached body object for any two calls landing within
   one `state["generation"]` value (`if _state_body_cache["generation"] ==
   state["generation"]: return _state_body_cache["body"]`) — this is not
   new; this task's coarsening widens how long that memoization window
   already is. Coarsening to at most one bump per 1.0 second means a
   normal (non-conditional) `GET /api/state` response can be up to 1.0s
   stale relative to the instant a real change happened — far below any
   poll cadence in this app (5-30s) and far below human perceptual
   granularity for a dashboard, and confirmed (same grep as above, `state`
   is read directly in-process by every actual trading-decision path, not
   through this memoized HTTP snapshot) to never affect anything that
   places, sizes, or gates a trade.

- [ ] **Step 1: Confirm the current `_build_state_body`/`bump_generation`
      match, before editing**

`main.py:1525-1552` (relevant excerpt):

```python
def _build_state_body() -> dict:
    ...
    if _state_body_cache["generation"] == state["generation"]:
        return _state_body_cache["body"]
    scoped_market_titles = _scoped_market_titles(_relevant_tickers())
    event_info = _scoped_event_titles(scoped_market_titles)
    event_live_data = _scoped_event_live_data(scoped_market_titles)
    live_game_state = _scoped_live_game_state(scoped_market_titles)
    ...
    body = {
        ...
        "event_live_data": event_live_data,
        ...
    }
    _state_body_cache["generation"] = state["generation"]
    _state_body_cache["body"] = body
    return body
```

`services/app_state.py:401-406`:

```python
def bump_generation() -> None:
    """Marks a real change to anything /api/state reports - see
    state["generation"]'s own comment above. ..."""
    state["generation"] += 1
```

Confirm both still match before editing — if not, stop and re-derive from
current source.

- [ ] **Step 2: Live-verify the payload-share and bump-frequency claims
      one more time, immediately before implementing (they may have
      shifted since this plan's own research)**

Run: `curl -sk -m 30 "https://kalshi-whale-poc.ddev.site:8443/api/state" -o /tmp/state_check.json` then a short Python snippet computing each top-level key's serialized byte share (same method this plan's own research used) — confirm `event_live_data` is still the dominant field before proceeding; if it has dropped substantially (e.g. because the watchlist emptied), note the new number rather than proceeding on a now-stale assumption.

- [ ] **Step 3: Write the failing tests for `event_live_data` throttling**

Add to whichever test file was confirmed in Step 0/the Files section
above (write against `main._build_state_body` directly, matching that
file's own established `import main`-with-redirected-DB_PATH convention —
confirm the exact pattern from an existing passing test in the same file
before writing):

```python
def test_event_live_data_is_throttled_to_its_own_repoll_cadence(monkeypatch):
    """Task 7 of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md: event_live_data is 87.3% of /api/state's payload (live-
    measured 2026-09-03) despite already being scoped to currently-
    relevant events (services/state_view.py's _scoped_event_live_data,
    2026-08-21) - the underlying data itself only refreshes once every
    _EVENT_LIVE_DATA_REPOLL_SEC (60s), so resending it on every poll
    resends unchanged data most of the time. First call after a bump
    includes it; a second call inside the 60s window gets an empty dict
    (frontend already Object.assign-merges rather than replaces, so this
    is a safe no-op, not a missing-data bug)."""
    monkeypatch.setattr(main.state_view, "_scoped_event_live_data", lambda titles: {"EVT-1": {"score": 1}})
    monkeypatch.setattr(main, "_last_event_live_data_sent_at", 0.0)
    main.bump_generation()

    body1 = main._build_state_body()
    assert body1["event_live_data"] == {"EVT-1": {"score": 1}}

    main.bump_generation()  # a real change happened again, within the 60s window
    body2 = main._build_state_body()
    assert body2["event_live_data"] == {}, "should not re-send within the 60s repoll window"
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/<confirmed_file>.py -k event_live_data_is_throttled -q"`
Expected: **fails** — `body2["event_live_data"]` is not empty (no
throttling exists yet).

- [ ] **Step 4: Implement the `event_live_data` throttle**

Add near `_build_state_body`, importing `_EVENT_LIVE_DATA_REPOLL_SEC`
directly rather than duplicating its value:

```python
from services.market_watch.event_metadata import _EVENT_LIVE_DATA_REPOLL_SEC
```

(check `main.py`'s current import block for whether
`_EVENT_LIVE_DATA_REPOLL_SEC` is already imported under a different name —
this plan's own Live re-verification section found `main.py:112` already
imports it via `from services.market_watch import (... _EVENT_LIVE_DATA_
REPOLL_SEC, ...)`; reuse that existing import, do not add a duplicate.)

```python
_last_event_live_data_sent_at = 0.0  # 2026-09-03, Task 7 of docs/
# superpowers/plans/2026-09-03-tier1-backend-hygiene.md: event_live_data
# is 87.3% of /api/state's payload (live-measured) despite already being
# event-scoped; the underlying data only refreshes once every
# _EVENT_LIVE_DATA_REPOLL_SEC (60s, services/market_watch/event_metadata.py),
# so resending it every poll resends unchanged data most of the time.
# Reuses that existing constant directly rather than picking an
# independent number.
```

Change:

```python
    scoped_market_titles = _scoped_market_titles(_relevant_tickers())
    event_info = _scoped_event_titles(scoped_market_titles)
    event_live_data = _scoped_event_live_data(scoped_market_titles)
    live_game_state = _scoped_live_game_state(scoped_market_titles)
```

to:

```python
    global _last_event_live_data_sent_at
    scoped_market_titles = _scoped_market_titles(_relevant_tickers())
    event_info = _scoped_event_titles(scoped_market_titles)
    now_for_eld = time.time()
    if now_for_eld - _last_event_live_data_sent_at >= _EVENT_LIVE_DATA_REPOLL_SEC:
        event_live_data = _scoped_event_live_data(scoped_market_titles)
        _last_event_live_data_sent_at = now_for_eld
    else:
        event_live_data = {}  # client already Object.assign-merges rather
        # than replaces (polling-and-websocket.js:87) - an empty dict here
        # is a safe no-op, not missing data.
    live_game_state = _scoped_live_game_state(scoped_market_titles)
```

(`time` is already imported at `main.py`'s top level — confirm via `grep
-n "^import time" main.py` before assuming; if using a different alias,
match it.)

- [ ] **Step 5: Run the new test, confirm it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/<confirmed_file>.py -k event_live_data_is_throttled -q"`
Expected: passes.

- [ ] **Step 6: Write the failing test for `bump_generation` coarsening**

Create `tests/test_app_state.py`:

```python
"""
services/app_state.py's bump_generation() coarsening (Task 7 of docs/
superpowers/plans/2026-09-03-tier1-backend-hygiene.md). No bespoke DB-
redirection needed here - tests/conftest.py's module-level
install_runtime_isolation() (confirmed by direct read, line 11) already
runs before this file is collected, same as every other test file in this
suite.
"""
from services import app_state


def test_bump_generation_coarsens_to_at_most_once_per_second(monkeypatch):
    """whale_stream_handlers.py's _process_stream_trade calls
    bump_generation() unconditionally on every processed trade message
    (confirmed live, all 3 of its exit paths) - this made state["generation"]
    (the literal ETag value) change far more often than /api/state's own
    body actually did, defeating the ETag's whole purpose (a 6-30s poll
    landing between real changes should get a 304, and almost never could).
    """
    start_gen = app_state.state["generation"]
    fake_now = [1000.0]
    monkeypatch.setattr(app_state.time, "time", lambda: fake_now[0])
    monkeypatch.setattr(app_state, "_last_bump_ts", 0.0)

    app_state.bump_generation()
    app_state.bump_generation()  # same instant - must be suppressed
    assert app_state.state["generation"] == start_gen + 1

    fake_now[0] += 1.1  # past the 1.0s coarsening window
    app_state.bump_generation()
    assert app_state.state["generation"] == start_gen + 2
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_app_state.py -q"`
Expected: **fails** — `state["generation"]` increments on every call
today, `start_gen + 2` instead of `start_gen + 1` after the two same-
instant calls.

- [ ] **Step 7: Implement the coarsening**

Change:

```python
def bump_generation() -> None:
    """Marks a real change to anything /api/state reports - see
    state["generation"]'s own comment above. Lives here (not in main.py)
    so every router/module that mutates `state` can call it without
    reaching back into main.py - main.py's modularization pass (2026-08-21)
    moved this alongside `state` itself since it's called from nearly every
    bucket main.py is being split into."""
    state["generation"] += 1
```

to:

```python
# 2026-09-03, Task 7 of docs/superpowers/plans/2026-09-03-tier1-backend-
# hygiene.md: bump_generation() fires on every processed trade message
# (confirmed live - services/whale_stream/whale_stream_handlers.py's
# _process_stream_trade calls it unconditionally on all 3 exit paths),
# which made state["generation"] (the literal /api/state ETag value)
# change far more often than the response BODY actually did, defeating
# the ETag's whole purpose. 1.0s is an estimate: far below every poll
# cadence in this app (5-30s) and far below human perceptual granularity
# for a dashboard - stated as a data-plane tradeoff (see this plan's Task
# 7 header for the verified zero-trading-decision-dependency argument),
# not assumed correct without that reasoning.
_GENERATION_BUMP_MIN_INTERVAL_SEC = 1.0
_last_bump_ts = 0.0


def bump_generation() -> None:
    """Marks a real change to anything /api/state reports - see
    state["generation"]'s own comment above. Coarsened (2026-09-03) to at
    most once per _GENERATION_BUMP_MIN_INTERVAL_SEC - see that constant's
    own comment for why. Lives here (not in main.py) so every router/
    module that mutates `state` can call it without reaching back into
    main.py - main.py's modularization pass (2026-08-21) moved this
    alongside `state` itself since it's called from nearly every bucket
    main.py is being split into."""
    global _last_bump_ts
    now = time.time()
    if now - _last_bump_ts < _GENERATION_BUMP_MIN_INTERVAL_SEC:
        return
    _last_bump_ts = now
    state["generation"] += 1
```

(`time` is already imported at `services/app_state.py`'s top level,
confirmed in this task's own research.)

- [ ] **Step 8: Run the new test, confirm it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_app_state.py -q"`
Expected: passes.

- [ ] **Step 9: Full regression check, both changes together**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/ -k 'state or generation or event_live_data' -q"`
Expected: no regressions. Any test that asserts `state["generation"]`
increments exactly once per some single action must still pass (the
coarsening only suppresses REPEATED bumps within 1s, and a single test
calling `bump_generation()` once is unaffected — `_last_bump_ts` starting
at `0.0` and real timestamps being far greater than `1.0` means the FIRST
bump in a fresh process/test always fires).

- [ ] **Step 10: Live validation**

After merge, probe `GET /api/state` twice, ~2s apart, with a real
`If-None-Match` round-trip during a QUIET period (no whale trades
processed in between) and confirm a `304`. Separately confirm `event_live_
data` is present on the first poll after a >60s gap and empty (not
missing) on a poll inside 60s of the last one — record the actual
before/after 304-rate and average response-body-size numbers in this
task's completion note, per the data-plane HARD RULE's "measure them;
never infer health from the absence of errors."

---

### Task 8: `alerting.py`'s 3 discarded task handles; `http_client.py`'s explicit `AsyncClient` timeout/limits

#### Task 8a: Retained task references in `services/alerting/alerting.py`

**Files:**
- Modify: `services/alerting/alerting.py:91-94` (`record_alert`),
  `:270-273` (`_check_transition`), `:294-300`
  (`_expire_stale_crash_alerts`)
- Test: `tests/test_alerting.py` (extend existing file — has an autouse
  `_isolated(monkeypatch, tmp_path)` fixture and `_record`/`_transition`
  async wrapper helpers, confirmed by direct read)

**Verified mechanism, not the audit's own citation:** the second audit's
§8 Tier-1 item 14 cites line `:91` and describes "3 discarded task
handles" using `asyncio.create_task`/`ensure_future` directly — `grep -n
"asyncio.create_task\|asyncio.ensure_future" services/alerting/
alerting.py` returns ZERO hits in current source. All 3 real call sites go
through `task_supervisor.supervise(...)`, which itself calls
`asyncio.create_task(_run())` internally
(`services/task_supervisor.py:73`) and RETURNS the `Task` — but all 3 of
`alerting.py`'s own call sites discard that return value (call `task_
supervisor.supervise(...)` as a bare statement, never assigning it). The
underlying hazard is the same one the audit names (Python's own
`asyncio.create_task` documentation: "Save a reference to the result of
this function... The event loop only keeps a weak reference to a Task. A
task that isn't referenced elsewhere may get garbage collected at any
time, even before it's done"), just one layer removed from where the
audit's line-number citation points.

**Interfaces:** New module-level helper
`_supervise_background(coro_fn, *, component, operation) -> asyncio.Task`
wraps `task_supervisor.supervise(...)`, retains the returned `Task` in a
module-level `set`, and removes it via `add_done_callback` once complete.
All 3 existing call sites switch from `task_supervisor.supervise(...)` to
`_supervise_background(...)` with identical arguments — a pure wrapper
substitution, no change to `task_supervisor.supervise`'s own contract or
`_dispatch_notification`'s behavior.

- [ ] **Step 1: Confirm all 3 current call sites match**

`services/alerting/alerting.py:91-94` (inside `record_alert`):

```python
    task_supervisor.supervise(
        lambda: _dispatch_notification(category, severity, message, now),
        component="alerting", operation="dispatch_notification",
    )
```

`:270-273` (inside `_check_transition`):

```python
        task_supervisor.supervise(
            lambda: _dispatch_notification(category, "info", resolved_message, time.time()),
            component="alerting", operation="dispatch_notification",
        )
```

`:294-300` (inside `_expire_stale_crash_alerts`):

```python
        task_supervisor.supervise(
            lambda alert_id=alert_id: _dispatch_notification(
                "crash", "info",
                f"Crash alert #{alert_id} auto-resolved after {max_age_sec}s with no recurrence", now,
            ),
            component="alerting", operation="dispatch_notification",
        )
```

Confirm all 3 still match before editing.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_alerting.py`, using its existing `_isolated`/`_record`
conventions:

```python
def test_record_alert_retains_a_strong_reference_to_its_dispatch_task():
    """Task 8a of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md - Python's own asyncio.create_task() docs: 'Save a
    reference to the result... a task that isn't referenced elsewhere may
    get garbage collected at any time, even before it's done.'
    task_supervisor.supervise() already returns a real Task; alerting.py's
    3 call sites all discarded it. This is the fix for all 3, verified via
    the one it's cheapest to check directly."""
    async def run():
        alerting.record_alert("kill_switch", "critical", "test", now=1000.0)
        assert len(alerting._background_tasks) == 1
        task = next(iter(alerting._background_tasks))
        await task
        assert task not in alerting._background_tasks

    asyncio.run(run())
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_alerting.py -k retains_a_strong_reference -q"`
Expected: **fails** — `AttributeError: module 'services.alerting.alerting'
has no attribute '_background_tasks'`.

- [ ] **Step 3: Implement `_supervise_background` and wire it into all 3
      call sites**

Add near `_last_known_bad`:

```python
# 2026-09-03, Task 8a of docs/superpowers/plans/2026-09-03-tier1-backend-
# hygiene.md: asyncio.create_task()'s own documentation - "Save a
# reference to the result of this function, to avoid a task disappearing
# mid-execution. The event loop only keeps weak references to tasks."
# task_supervisor.supervise() already returns a real Task; all 3 of this
# module's own fire-and-forget dispatch call sites discarded it. Standard
# asyncio idiom (a set + add_done_callback to discard once complete), not
# a hand-rolled task registry.
_background_tasks: set = set()


def _supervise_background(coro_fn, *, component: str, operation: str) -> "asyncio.Task":
    task = task_supervisor.supervise(coro_fn, component=component, operation=operation)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
```

(Add `import asyncio` to this file's imports if not already present —
confirm via `grep -n "^import asyncio" services/alerting/alerting.py`
first; currently this file has no top-level `asyncio` import since it
only ever called `task_supervisor.supervise`, never `asyncio.*` directly.)

Change all 3 call sites from `task_supervisor.supervise(...)` to
`_supervise_background(...)`, arguments unchanged:

```python
    _supervise_background(
        lambda: _dispatch_notification(category, severity, message, now),
        component="alerting", operation="dispatch_notification",
    )
```

(and identically for the other two, keeping each one's own exact lambda
and surrounding comments.)

- [ ] **Step 4: Run the new test, confirm it passes**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_alerting.py -q"`
Expected: all pass, including the new one and every pre-existing test in
this file (this is a pure wrapper substitution — `task_supervisor.
supervise`'s own crash-handling/restart behavior is unchanged, confirmed
by not modifying `task_supervisor.py` at all).

#### Task 8b: Explicit `httpx.AsyncClient` timeout/limits in `services/http_client.py`

**Files:**
- Modify: `services/http_client.py:428-430` (`get_client`)
- Test: `tests/test_http_client.py` (extend existing file)

**Verified, not assumed — httpx's actual installed defaults (2026-09-03,
`docker exec ddev-kalshi-whale-poc-fastapi python3` against the real
installed version, not httpx's documentation or memory):** httpx 0.27.2's
own defaults are `Timeout(timeout=5.0)` and `Limits(max_connections=100,
max_keepalive_connections=20, keepalive_expiry=5.0)`. The bare
`httpx.AsyncClient()` is NOT "no timeout at all" — it already has a
reasonable 5.0s default. This task pins that CURRENT default explicitly
(named constants with reasoning) rather than leaving it as "whatever
httpx happens to default to on whatever version is installed," and ties
`max_connections`/`max_keepalive_connections` to this app's own fd-budget
context (the 2026-09-02 6.8-hour fd-exhaustion incident Tier 0 addresses
elsewhere) — **no bottleneck was measured at these values**, so this task
changes NO runtime behavior, per the data-plane HARD RULE's ban on tuning
a capacity number without a measured cause.

**Interfaces:** `get_client() -> httpx.AsyncClient` keeps its exact
signature and singleton behavior; only the `httpx.AsyncClient()`
construction call gains explicit `timeout=`/`limits=` arguments.

- [ ] **Step 1: Confirm the current `get_client` matches**

`services/http_client.py:428-430`:

```python
def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client
```

Confirm it still matches before editing.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_http_client.py`:

```python
def test_get_client_pins_explicit_timeout_and_limits(monkeypatch):
    """Task 8b of docs/superpowers/plans/2026-09-03-tier1-backend-
    hygiene.md - makes the shared client's timeout/connection-pool ceiling
    explicit, pinned to httpx 0.27.2's own already-measured defaults
    (Timeout(timeout=5.0), Limits(max_connections=100,
    max_keepalive_connections=20)) rather than an implicit, version-
    dependent default. No behavior change - a bottleneck was never
    measured here, only the values made explicit."""
    monkeypatch.setattr(http_client, "_client", None)
    client = http_client.get_client()
    assert client.timeout.connect == 5.0
    assert client.timeout.read == 5.0
    assert client._transport._pool._max_connections == 100
    # (Exact private-attribute path for max_connections confirmed against
    # the installed httpx version before trusting this assertion - httpx's
    # public API doesn't expose Limits back off a constructed client
    # directly; re-verify this attribute path is still correct for
    # whatever httpx version is installed at execution time, since this
    # is exactly the kind of private-internal detail that can shift
    # across httpx releases.)
    asyncio.run(http_client.close_client())
```

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_http_client.py -k pins_explicit_timeout -q"`
Expected: passes already for the `timeout.connect`/`timeout.read`
assertions (httpx's own default already IS 5.0) — this is a real
limitation of testing "made a value explicit" when the explicit value
matches the prior implicit one; the test's real value is pinning the
number so a FUTURE httpx upgrade that changes its own default can't
silently change this app's behavior without this test catching it. Note
this honestly (same as Task 3c's Step 2) rather than claiming a red step
that doesn't apply.

- [ ] **Step 3: Apply the fix**

Change:

```python
def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client
```

to:

```python
# 2026-09-03, Task 8b of docs/superpowers/plans/2026-09-03-tier1-backend-
# hygiene.md: pins this shared client's timeout/connection-pool ceiling
# explicitly rather than leaving it as an implicit, httpx-version-
# dependent default. Values are httpx 0.27.2's OWN measured defaults
# (docker exec ddev-kalshi-whale-poc-fastapi python3, confirmed live,
# 2026-09-03: Timeout(timeout=5.0), Limits(max_connections=100,
# max_keepalive_connections=20, keepalive_expiry=5.0)) - no bottleneck was
# measured at these values, so this changes no runtime behavior; it only
# stops a future httpx upgrade from silently changing this app's behavior
# by changing its own defaults out from under an implicit construction.
# max_connections/max_keepalive_connections are also an fd-budget concern
# now (this app's 2026-09-02 6.8h fd-exhaustion incident) - each open
# connection is a socket file descriptor, and this shared client serves
# every caller of get_client() (Kalshi public REST via KalshiPublicGateway,
# Google OAuth, event_schedule.py) through one pool.
_HTTP_CLIENT_TIMEOUT_SEC = 5.0
_HTTP_CLIENT_MAX_CONNECTIONS = 100
_HTTP_CLIENT_MAX_KEEPALIVE_CONNECTIONS = 20


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_HTTP_CLIENT_TIMEOUT_SEC),
            limits=httpx.Limits(
                max_connections=_HTTP_CLIENT_MAX_CONNECTIONS,
                max_keepalive_connections=_HTTP_CLIENT_MAX_KEEPALIVE_CONNECTIONS,
            ),
        )
    return _client
```

- [ ] **Step 4: Run the new test, confirm pass**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/test_http_client.py -q"`
Expected: all pass.

- [ ] **Step 5: Confirm every existing `get_client()` caller still works
      (per-call `timeout=` overrides are unaffected)**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/ -k 'auth or event_schedule or generic_rest or kalshi_public' -q"`
Expected: no regressions — every existing call site that passes its own
`timeout=` argument to a specific `.get()`/`.post()` call (confirmed
present at `services/auth.py:92`, `services/kalshi/public.py:85`,
`services/whalewatchers/generic_rest.py:99`, `services/index_feed/
backfill.py:315`, `services/alerting/alerting.py:215`) keeps overriding
the client-level default exactly as before — this task changes only the
CLIENT-level default those calls already override, never the override
mechanism itself.

---

### Task 9: Full regression suite + live validation

**Not a code task** — matches this repo's own standing practice for a
multi-file plan's final task (Tier 0's own Task 10 is the precedent).

- [ ] **Step 1: Run the full local test suite**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -m pytest tests/ -q -m 'not slow'"`
Expected: same pass count as the pre-existing baseline (confirm fresh via
`git log -1 --format=%h` on `main`, not a remembered number) plus this
plan's net new tests across Tasks 1, 3a, 3b, 3c, 4, 5, 6a, 6b, 7, 8a, 8b
(count them from the actual diff, not estimated here), all passing.

- [ ] **Step 2: `import main` sanity check**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene && python -c 'import main' && echo IMPORT_OK"`
Expected: `IMPORT_OK` — no import errors from any new module
(`services.pagination`) or new imports (`suggestion_decisions` in
`main.py`, `Depends` in 6 route files, `tick_executor` in
`whale_stream_handlers.py` if not already present).

- [ ] **Step 3: Frontend build**

Run: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/tier1-backend-hygiene/frontend && npm run lint && npm run build"`
Expected: no lint errors, `static/js/dashboard.bundle.js` rebuilds.

- [ ] **Step 4: Deploy and re-measure the live symptoms this plan targets**

After this branch merges and the primary checkout's running app reloads
it (`git merge origin/main` on primary, confirm via `docker logs
--timestamps ddev-kalshi-whale-poc-fastapi` that WatchFiles picked up the
change):

1. **Task 2**: nginx 504-rate over a comparable foreground window,
   before/after (per Task 2's own Step 5).
2. **Task 6b**: `GET /api/candidate-log/summary` response time on a
   SECOND call within 30s of the first (should be near-instant from
   cache, vs. the ~4.8s uncached compute cost the first call still
   pays).
3. **Task 7**: `GET /api/state` twice ~2s apart during a quiet period,
   confirm a `304`; confirm `event_live_data`'s presence/absence pattern
   across a >60s window.
4. **Task 1**: force a real stall (or wait for a natural one) and confirm
   a `fault_log` row with `component="loop_watchdog"` now carries a
   non-empty `first_traceback`.

Record actual before/after numbers for each in this task's completion
note — per the data-plane HARD RULE, these properties fail silently and
must be measured, not inferred from "the code changed and tests pass."

- [ ] **Step 5: Record the result**

Update `docs/next-action.md` and, for anything Step 4 finds still open
(the History-tab 504 rate not fully resolved, `event_live_data` still
disproportionate for a reason this plan didn't anticipate, etc.), add an
explicit line to `docs/open-decisions.md` rather than leaving it
unrecorded.

---

## Plan self-review

**Research coverage:** All 8 audit items (7-14, second-pass audit's own
§8 Tier 1) are covered, each mapped to a Task above. Two of the audit's
own citations were found to be wrong or imprecise during this plan's own
research and are corrected in-place rather than silently repeated: Task
6's "§9.2 finding #3" (the actual citation is the first audit's own
Tier-1 items #3/#4, a different section) and its "PR #424" reference —
corrected during this plan's own adversarial review (a `git log`-only
check had wrongly concluded the citation was unverifiable; `gh pr view
424` shows it is a real, directly analogous precedent, just for a
different sibling function, `check_confidence_input_coverage`). Both
corrections are load-bearing for how Task 6 is actually designed (a
cache, not a `since_ts` bound), not cosmetic.

**Placeholder scan:** Every code block in Tasks 1, 2, 3b, 3c, 5, 7, 8a,
8b is either a verbatim transcription of currently-read source or a
concrete, complete new implementation. Three explicit, disclosed
exceptions (matching the precedent plan's own self-review shape, not
silently hidden): Task 3a's two orchestrator tests
(`_analyze_market_uncached`'s and `_build_full_spectrum_context`'s full
collaborator-mocking lists were not exhaustively traced), Task 4's own
test (the existing `test_whale_stream_stage_timing.py` fixture setup for
reaching `_process_stream_ticker` with a `matched_market` was not fully
traced), and Task 7's test-file location (which exact file currently
tests `_build_state_body`/`get_state` was not confirmed by this plan's own
research). Each is marked with a `...` and an explicit paragraph
explaining what's missing and how to fill it in — not left as a silent
gap.

**Verified-vs-assumed claims, stated plainly:** three claims in this plan
are the product of this plan's OWN experiments/live probes, not copied
from either audit: (1) the exact `config_store.update()` comment-wipe
mechanism (falsified against a scratch file, never the live one) and the
discovery that the audit's own suggested "just deep-merge" fix has a real
correctness hazard the audit didn't find; (2) that `resolved_signals_
with_factors()`/`population_gate_summary()` cannot be `since_ts`-bounded
without an accuracy regression (contradicting the audit's own one-line
framing, verified by reading every call site); (3) `event_live_data`'s
live 87.3%-of-payload share and `bump_generation()`'s unconditional
per-trade-message firing (both read from current source, both quantified
against a live probe, not inferred). Each is called out inline where it
first appears, not folded in as if it were always known.

**Type/interface consistency:** Task 1's `fault_log.record_fault` gains
`tb` as an additive, defaulted, last-position keyword arg — verified
against every existing call site (`grep -rn "record_fault("`) rather than
assumed safe. Task 3c's DDL-sharing direction (capture_writer.py as
owner) was chosen specifically because it's the only direction the
existing import graph allows without restructuring it — verified via
`grep -n "^from services\|^import"` on all three files, not assumed from
the audit's own less specific framing. Task 6a's `paginate()` factory
matches FastAPI's own documented sub-dependency pattern exactly (a
callable returned by a callable, not a hand-rolled abstraction).

**Scope boundary, stated plainly:** this plan does not touch any of Tier
0's five target `_connect()` functions or `services/diagnostics/routes.py`
(confirmed not yet landed, so nothing here can conflict with it); does not
restore `config/settings.yaml`'s wiped comment (a human decision, `docs/
open-decisions.md`); does not implement the Tier-2-scale "pydantic schema
+ values-only YAML" config redesign, the DuckDB/aiosqlite migrations, the
persistence-module consolidation, or the Preact frontend migration —
every one of these is named explicitly in the relevant task as out of
scope, not silently omitted. Task 3a's fix is additive only (adds a
missing kwarg at 3 call sites); it does not audit whether `declined_ids`'s
OWN semantics are correct, only that all 6 call sites now agree on
whether to pass it.

**What this plan does NOT claim:** it does not claim the History-tab
504 storm fully resolves (Task 2's own note: "Expect the 504 bursts to
stop; do not expect the stalls to" — matching the audit's own honest
framing, the underlying `tick_executor` pool contention this plan's Task
6b also reduces is a separate, only-partially-addressed mechanism); it
does not claim `event_live_data`'s per-event payload size itself shrinks
(only how often it's sent); and it does not claim Task 8b's explicit
timeout/limits fix anything currently broken (explicitly: "no bottleneck
was measured at these values").
