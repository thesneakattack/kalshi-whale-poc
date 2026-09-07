# Self-review: fault_log NULL exc_type dedup fix (issue #543)

Author: same session that wrote the fix (`autotrade-bc`), on
`fix/fault-log-null-exc-type-dedup`. Per CLAUDE.md's "nothing advances on
one pass" HARD RULE, self-review checks the artifact's own internal
consistency and unaddressed scope before the independent adversarial pass
runs. This is not the independent review — that's a separate, fresh Agent
dispatch with no memory of this session, next.

## What was checked

1. **Design choice re-derived, not just recalled.** Two fix shapes were
   named in the issue (sentinel-string normalization vs. COALESCE-based
   partial unique index) without a decision. Sandboxed both directly rather
   than reasoning abstractly:
   - A single `COALESCE(exc_type, '')`-expression unique index covering all
     rows (replacing the raw constraint) works, but the raw table-level
     `UNIQUE` constraint can't be dropped without a full table rebuild for
     any database that already has one (no `ALTER TABLE DROP CONSTRAINT`
     for inline `UNIQUE` in SQLite) — too invasive for what the bug needs.
   - A genuine SQLite **partial** unique index (`WHERE exc_type IS NULL`),
     purely additive alongside the existing constraint, chained as a second
     `ON CONFLICT` target in the INSERT — verified this exact combination
     works in a standalone sandbox script before writing it into the module.
     Chosen: smaller diff, no schema rebuild, doesn't touch the columns'
     stored values (`exc_type` stays true `NULL`, matching the existing
     `test_non_exception_edge_cases_are_recordable` assertion
     `r["exc_type"] is None` — this test needed zero changes).

2. **The retroactive-data question the issue explicitly flagged was not
   optional, and that was discovered, not assumed.** Sandbox-tested
   `CREATE UNIQUE INDEX ... WHERE exc_type IS NULL` against a table that
   already had duplicate rows violating it: it raises `IntegrityError`
   immediately, and `IF NOT EXISTS` does **not** suppress this (verified in
   isolation before writing any module code) — it only skips *re*-creation
   once the index already exists. This means the naive fix would have taken
   down the entire fault_log store's write/read path silently the moment it
   ran against the live 86,506-row production database (every public
   function in this module wraps `_connect()` in a broad
   `try/except Exception`, so the failure would not crash the app — it would
   just make every future `record()`/`record_fault()`/`recent()`/`summary()`
   call return `False`/`[]`/`{"error": ...}` from that point on, silently,
   which is exactly the `game_state.py` failure mode this module's own
   docstring exists to prevent). This was root-caused by directly
   reproducing it in a sandbox script before writing any migration code —
   not inferred from documentation or memory.

3. **Migration correctness, tested end-to-end against a simulated legacy
   database** (raw duplicate rows written with plain `sqlite3`, bypassing
   the module entirely, matching how a real pre-fix production file would
   look): confirms count sums correctly, `last_seen` takes the max,
   `first_traceback`/`context`/`severity` survive from the earliest-inserted
   row unchanged, real-exception rows and non-duplicated `record_fault()`
   rows are untouched, and the second `_connect()` call takes the fast
   `IF NOT EXISTS` path without re-running the merge. This exact scenario is
   also now a permanent regression test
   (`test_pre_existing_duplicate_null_exc_type_rows_are_merged_on_first_connect`),
   not just a one-off sandbox script.

4. **The other 9 named `record_fault()` call sites were individually read**,
   not trusted from the issue's own characterization — confirmed via direct
   source inspection (not the issue's summary) that `exit_engine.py`,
   `settlement_resolver.py`, `index_feed/backfill.py` (both sites),
   `index_feed/ingestion.py`, `main.py`, and `kalshi/websocket.py`'s
   `force_reconnect` genuinely interpolate per-instance data (ticker, ms
   timing, queue depth, attempt count) and won't meaningfully dedupe either
   way. Also found — not called out by the issue — that
   `kalshi_websocket/handle_message_timeout:{cls}` and two
   `milestone_live_data.py` sites and `strategy_engine.py`'s
   `me_gate_unknown` path use a fixed or low-cardinality message *and* have
   their own in-process "seen" gates, meaning this fix gives them a real,
   secondary benefit (cross-window/cross-restart dedup) beyond the
   `loop_watchdog` case the issue focused on — confirmed against the live
   database's actual row/distinct-message counts, not predicted.

5. **Live measurement, not just synthetic test numbers**: queried
   `data/fault_log.db` read-only (no write, no mutation — this fix's
   migration will apply automatically on the next real deploy's first
   `_connect()`, not run manually here) for a current, freshly-measured
   before/after picture: 86,506 NULL-exc_type rows collapse to 20,122
   distinct groups (76.7% reduction) once deployed. Per-operation
   breakdown in the commit message. This number is explicitly labeled
   point-in-time (still growing until the fix ships), not a constant to
   keep in sync.

## Issues found and fixed in this pass

- **Stale line-number citation** (`_connect()`'s own docstring, "4 of them
  - services/fault_log.py:129, 153, 191, 203"): my insertion shifted every
  call site referenced there. Caught by re-grepping actual line numbers
  after the edit rather than trusting the original text — fixed to 256,
  292, 330, 342. **Caught this a second time**: a subsequent edit (the next
  finding below) shifted the file again, silently making my first fix
  stale before it was even committed — re-verified after every edit, not
  just once, and only trusted the final `grep` output, not the arithmetic.
- **Stale duplicate-count figure** in `_ensure_null_exc_type_dedup_index`'s
  own docstring: still said "55,635" (the issue's original number) while
  the commit message and live re-measurement say 57,021 (more rows
  accumulated in the time between the issue being filed and this fix being
  written). Fixed to the fresher number, with explicit "point-in-time, not
  a constant" framing so it doesn't read as a claim this number is exact
  forever.

## Verification after fixes

- `services/fault_log.py:256,292,330,342` re-grepped directly against the
  current file content (not recomputed by hand) after both docstring edits
  landed — confirmed accurate, not just asserted.
- Full targeted suite re-run after the self-review fixes:
  `tests/test_fault_log.py` (20/20) and `tests/test_pipeline_health_cost.py`
  (its own `services/fault_log.py:85-87` line citation, unaffected by these
  edits, re-checked and still accurate) — 36/36 passed.

## What this self-review did NOT re-litigate

- The design choice itself (partial index vs. COALESCE expression index) —
  already resolved with a direct sandbox comparison during implementation,
  not revisited here without new information.
- Whether `record()`'s exception-path dedup still works — untouched by this
  change, and covered by 12 pre-existing tests that all still pass
  unmodified.

## Open item for the adversarial pass

- Whether any genuine multi-threaded concurrency exists on this write path
  (a race between two connections both hitting the `IntegrityError` branch
  on the very first post-deploy connection) was checked during
  implementation: every `fault_log.record()`/`record_fault()` call site
  in this codebase runs from an `async def` coroutine on the single asyncio
  event loop, never from a separate OS thread via `asyncio.to_thread` (the
  to_thread-heavy modules — `kalshi_trade_tape.py`, `_scoring_pool.py`,
  `diagnostics/_aio_db.py`, `loop_watchdog.py`, `whale_pipeline_perf.py` —
  were grepped directly and none call `fault_log.*`). Since `_connect()`'s
  setup code (including the new migration) has no `await` between opening
  the connection and finishing schema setup, it cannot be interleaved with
  another coroutine's `_connect()` call either. Flagging this explicitly for
  the adversarial reviewer to re-derive independently rather than asserting
  it settled here — this is exactly the kind of claim that HARD RULE says
  self-review shouldn't be trusted alone on.
