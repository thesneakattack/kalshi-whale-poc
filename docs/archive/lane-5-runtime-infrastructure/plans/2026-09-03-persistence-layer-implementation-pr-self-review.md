# PR-Stage Self-Review — Persistence Layer Implementation Plan (PR #484)

Per CLAUDE.md's "nothing advances on one pass" HARD RULE: this PR already cleared a full
design-stage cycle (GO) and a full plan-stage cycle (GO-AFTER-FIXES, all 8 fixes applied and
verified). This is the required third cycle — self-review + independent adversarial review +
consolidation — run against the PR as submitted, before merge.

## What I checked

- Read the full implementation plan (`docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-persistence-layer-implementation.md (moved there 2026-09-06, planning-lanes migration)`,
  1582 lines) end to end.
- Read the plan-stage adversarial review (370 lines, verdict GO-AFTER-FIXES, 3 must-fix + 5
  should-fix) and confirmed every must-fix/should-fix item is actually present in the plan text
  I read (not just claimed in the consolidation) — spot-checked each of the 8 fixes against the
  live document content (F6's `_hold_write_lock` rewrite, F7a/F7b's alias fixes, F8/F12's
  settlement_resolver correction, F9's "five" correction, F10's "34" correction, F11's two added
  modules) — all present.
- Read the design-stage consolidation (GO) for context on what the plan builds on.
- **Checked whether the codebase has drifted since the plan-stage adversarial review ran**,
  given how much parallel work has landed on `main` today (tier0 remediation, tier1
  backend-hygiene, watchlist-gate fix, etc.) — this is the one check a same-day plan-stage
  review cannot itself perform, since it necessarily reviews against a point-in-time snapshot.

## Finding: real, confirmed drift since the plan's own review — not cosmetic

**`0e90287` ("refactor: one canonical DDL string per table, owned by capture_writer.py",
Task 3c of `docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-tier1-backend-hygiene.md (moved there 2026-09-06, planning-lanes migration)`, merged today as part of
PR #500) already centralized three of the exact tables this plan's Tasks 3 and 5 migrate.**

Verified directly against `origin/main`'s current source (not assumed from the commit message):

- `services/candidate_log.py`'s `_connect()` now does `conn.execute(capture_writer.
  REJECTED_CANDIDATES_DDL_SQL)` and `conn.execute(capture_writer.REJECTION_EVENTS_DDL_SQL)` —
  **both** tables' DDL are now imported constants, not hand-typed strings. Task 3's Step 3 (as
  written) hand-types both full `CREATE TABLE` bodies into `db.register_ddl("rejected_candidates",
  """...""")` / `db.register_ddl("rejection_events", """...""")` calls — implementing it
  literally would **reintroduce the exact AST-duplicated DDL the tier1 refactor just eliminated**,
  a real regression, not a citation-precision issue.
- `services/series_watcher.py`'s `_connect()` now does `conn.execute(capture_writer.
  RAW_TRADES_DDL_SQL)` for the `raw_trades` table. `book_snapshots`' DDL is still hand-typed
  (unaffected). Task 5's Step 3 hand-types **both** tables' DDL — same regression risk for the
  `raw_trades` half only.
- `services/observability/observability.py` (Task 6) is **unaffected** — `metric_samples` was
  never one of the three tables the tier1 refactor touched; verified its `_connect()` matches
  the plan's transcription exactly, including the exact import-ordering claim (`db` before
  `fault_log` in the existing multi-line import).
- `services/capture_writer.py`'s `_flush_store`/`flush_now` (Tasks 2/4) are **unaffected** —
  still the pre-fix signatures the plan's falsifier/budget-widening tasks target; verified line
  numbers shifted slightly (348→355 for `_flush_store`) but the signatures and call sites the
  plan depends on are otherwise exactly as described.

**Why this matters enough to flag, not just note:** this plan's Task 1 (`services/db.py`)
introduces its *own* DDL-registration mechanism (`db.register_ddl(table, ddl_string)`) — a
second, independently-designed centralization approach for the identical problem (AST-duplicated
DDL, architecture audit item 9.2/28) that `capture_writer.py`'s tier1-hygiene refactor already
shipped for three of these five tables, same day, uncoordinated (both cite "2026-09-03"). Neither
approach is wrong on its own terms, but Task 3/5 as literally written would silently step
backward on tables `capture_writer.py` already deduplicated, while still correctly advancing the
same goal for `book_snapshots`/`metric_samples`/the two `capture_writer.py`-only fixes. The
plan's own execution-time safety net (each task's Step 2: "confirm it still matches... if not,
stop and re-derive") would catch the mismatch when someone actually implements Task 3/5 — but
leaving the plan document silently unaware of a *confirmed*, not hypothetical, conflict means
whoever picks it up has to improvise a re-derivation with no guidance, and the plan as merged
would be instructing a real regression if followed literally.

**Scope of the actual fix needed:** narrow. `db.register_ddl()`'s own API (a table name + a DDL
string) is unaffected — it can register `capture_writer.REJECTED_CANDIDATES_DDL_SQL` exactly as
easily as a hand-typed string; only Task 3 and Task 5's specific code blocks need updating to
call `db.register_ddl("rejected_candidates", capture_writer.REJECTED_CANDIDATES_DDL_SQL)` (etc.)
instead of retyping the DDL text, and their "before" source transcriptions need correcting to
match current `main`. No design decision changes; no task added or removed.

## Everything else: no new issues found

Independently re-checked (not re-trusting the plan-stage review's own claims) the load-bearing
safety claims that don't depend on file freshness:

- `services/whale_stream/decision_bridge.py`, `services/candidate_ledger.py`,
  `services/tick_executor.py` are named nowhere in any task's "Files" section — grep-confirmed,
  current `main`. Trading-critical blast-radius claim still holds.
- No task deletes, moves, or truncates a `data/*.db` file; Task 9's integrity check is read-only
  by construction (unchanged).
- `contextlib.closing` is not used anywhere in the plan (grep-confirmed).
- Global Constraints' `candidate_log.py` caller list (post the F12 fix) still matches current
  `main`: `services/strategy_engine.py`, `services/kalshi/websocket.py`,
  `services/whalewatchers/kalshi_trade_tape.py`, `services/whale_stream/whale_stream_handlers.py`,
  plus `services/settlement_resolver.py` for `resolve_from_market_results()` — re-grepped fresh,
  no new caller has appeared today.

## Self-assessment

This is a real, must-fix finding for the PR stage — not a rubber-stamp of the plan-stage
review's already-thorough work, and not a reason to distrust that work either (it was correct
against the state it reviewed; the state has since moved, which is exactly the kind of thing a
same-day, second, independent PR-stage pass exists to catch). Dispatching a fresh, memory-less
adversarial review next to independently verify this finding's full scope (not trust this
document's own claim) and check for anything else the drift check didn't surface.
