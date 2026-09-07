# Self-review: persistence-layer db.py migration implementation plan

Self-review of `docs/archive/lane-5-runtime-infrastructure/plans/2026-09-03-persistence-layer-db-migration-implementation.md (moved there 2026-09-06, planning-lanes migration)`,
per CLAUDE.md's "nothing advances on one pass" HARD RULE — this document is its own artifact,
not a section folded into the plan itself, matching how the design spec (PR #505) and its own
review cycle were structured, not PR #484's looser precedent of an embedded closing section.

## Two real defects found and fixed during this pass

Self-review here meant re-verifying every module-specific claim in the plan against a fresh,
direct read of the actual current source — not re-reading the plan's own prose for internal
consistency alone. That process found two genuine errors, both now fixed in the plan document
itself (not merely noted here):

1. **`settlement_edge.py`'s table name.** The plan's first draft assumed the table `_connect()`
   creates is named `settlement_edge` (matching the module's own file name) — an assumption, not
   a citation; neither pre-audit document ever states the table's literal name. Direct
   verification (`grep -n "CREATE TABLE" services/settlement_edge.py`) found the real name is
   `window_observations`. This was wrong in two places: Task 1's table-name-uniqueness fixture
   and Task 11's Step 3 prose. Both fixed, with the fix itself citing the grep that caught it, so
   a reviewer doesn't have to re-derive why the plan now says something different from what a
   naive read of the module name would suggest.
2. **`paper_broker.py`'s table count.** The plan's first draft, following the 5-module
   pre-audit's own summary too literally, assumed the 13 `add_column_if_missing` calls spread
   across one or two tables (`trades`, maybe `pending_orders`) — the pre-audit's own text never
   states this explicitly either way, and the plan's first draft filled the gap with an
   assumption rather than a direct read. Direct verification (full read of
   `services/paper_broker.py:163-267`) found **four** tables: `broker_meta` (no schema
   evolution), `positions` (3 column-adds), `trades` (8 column-adds + the one index), and
   `pending_orders` (2 column-adds). This is the most safety-sensitive task in the plan
   (dedicated PR, paper-trading execution path) and the error would have produced an incomplete
   migration — `db.connect(db_path, tables=("trades",))` alone would never create `broker_meta`
   or `positions`, breaking every real call site on first use. Fixed: Task 7's "Current state"
   section now carries a full table (module doc table, not database table) mapping every column
   to its owning table and line number; the fixture, test, and Step 3 code description all
   updated to match.

Both were caught by the same method: re-run the primary-source grep this plan's own Gate 1 text
already prescribes ("read that module's actual `_connect()` body in full, current-source, not
from this spec's or any prior document's summary") against the plan's *own* drafted content,
not just against the pre-audit documents it cited. The lesson generalizes: a pre-audit
document's own text can be locally accurate (nothing in either pre-audit document asserts a
false table name or table count) while still supporting a plan-author's incorrect inference
if the plan fills a gap the pre-audit left open. Six other modules' table names were spot-checked
the same way during this pass (`risk_manager.py`, `candidate_ledger.py`, `series_evaluator.py`,
`trade_category.py`, `tools/coordination_engine.py`, `backup/backup.py`) and all matched the
plan's existing text exactly — no further corrections needed there.

## Scope check against the design spec's Gate 0/1/2, module classification, and D1/D2/D3

Every module named in either Gate-1 pre-audit has a task in the plan (Tasks 3–13) or is named in
Task 14's tracking list. Counting: 11 individually-tasked modules (`candidate_log.py`,
`series_watcher.py`, `observability.py`, `risk_manager.py`, `paper_broker.py`,
`candidate_ledger.py`, `series_evaluator.py`, `trade_category.py`, `settlement_edge.py`,
`backup/backup.py`, `tools/coordination_engine.py`) + 15 tracked = 26, matching the design
spec's own corrected scope exactly. `services/diagnostics/store_stats.py` is correctly absent
(design spec: not a migration candidate — already fixed). Tier0's 5 modules are correctly absent
(already fixed, PR #501). The plan's own top-of-document "Correction this plan makes to the
design spec's own bucket count" section explains why the general bucket is 15, not the design
spec's literal "17," with the arithmetic shown rather than asserted — checked again here and
still correct after the two fixes above (neither fix moved a module between tiers).

## Internal consistency check

Task 3/4 both depend on Task 2 (D2's shared `init_fn` requirement) — verified both tasks' code
blocks import `capture_writer.init_*`, neither defines a local closure around the shared DDL
strings. Task 4 explicitly excludes `_ensure_schema_aio` from its own diff (its Step 4 verifies
this with a targeted `git diff` grep, not just a stated intention) — this is D1's ruling made
mechanically checkable, not just asserted in prose. Task 13 is the only task touching files
outside its own module + own test file — justified explicitly (bare-assignment call-site shape,
24 real callers across 6 files, verified by the general-bucket pre-audit's own repo-wide grep),
with a verification step (its Step 5) that fails loudly (empty-grep assertion) rather than
trusting the update was complete.

The table-name-uniqueness fixture in Task 1 (`test_table_name_uniqueness_across_full_migration_scope`)
now lists 18 table names after the two fixes (`window_observations` replacing the wrong
`settlement_edge`; `broker_meta`/`positions` added alongside `trades`/`pending_orders`) — all
pairwise distinct, re-checked by inspection: `raw_trades`, `rejected_candidates`,
`rejection_events`, `book_snapshots`, `metric_samples`, `risk_meta`, `broker_meta`, `positions`,
`trades`, `pending_orders`, `candidates`, `series_status`, `trade_category`,
`window_observations`, `backup_runs`, `signal_state`, `coordination_runs`, `cleanup_actions` — 18
names, no duplicates. (The fixture's own inline comments undercount slightly in prose — they
group by task without restating this final count — but the literal `table_names` list is what
the test asserts against, and it is correct.)

## Gate coverage check

Gate 0 (raise-on-conflict, corrupted-DB test, monkeypatch test, KeyError-on-unregistered-table
test, table-name-uniqueness fixture, concurrent-registration lock test) — all six present in
Task 1's test list (15 tests total), not a subset. Gate 1 (read-before-edit, preserve
non-`CREATE TABLE` statements, real-close test, monkeypatch-convention test, event-loop check,
repo-wide call-site-shape grep) — present in every module task (Tasks 3–13), with the
call-site-shape grep specifically elevated to its own dedicated steps in Task 13, the one task
where it's load-bearing rather than a formality. Gate 2 (full suite, fd-count check,
`capture_writer.py` re-check-if-ever-migrated) — Task 15's Steps 1–3; the `capture_writer.py`
re-check is correctly absent since this plan does not migrate `capture_writer.py` (design spec:
out of scope, already closes correctly).

## Known gaps this plan does not resolve, stated rather than hidden

1. **`series_cache.py`'s event-loop verdict is unresolved** — carried into Task 14's tracking
   issue rather than resolved here, matching the general-bucket pre-audit's own stated gap. Not
   silently closed by this plan.
2. **This plan does not itself decide PR-grouping ordering beyond the Architecture table** (PR A
   before PR B; PR C/D/E can run in any order relative to each other and to F/G/H/I; Task 15
   last) — a legitimate execution-time sequencing choice, not a plan defect.
3. **Task 2's exact insertion point in `capture_writer.py`** is stated approximately ("after
   current `:198`, before `:199`") and the task text itself already requires a fresh read
   (`sed -n '130,202p'`) before inserting — correctly deferred, not a guess presented as fact.
4. **No task in this plan independently re-verifies the general-bucket pre-audit's own claims**
   for the 15 tracked-only modules (`accounts_store.py`, `alerting.py`, etc.) the way this
   self-review just did for the 11 individually-tasked ones — appropriate, since Task 14 doesn't
   ship code and whoever picks up each module's migration will do their own Gate 1
   read-before-edit at that time, but worth naming so a reader doesn't assume the same
   fresh-verification rigor was applied to all 26 modules equally in this pass.

## Verdict

Two real, load-bearing errors found and fixed (one factual citation error, one significant scope
gap in the plan's most safety-sensitive task). No further defects found in the remaining scope,
gate-coverage, or internal-consistency checks. This plan is ready for the independent
adversarial review (autotrade-a3, per coordinator autotrade-1d's assignment) — not for
consolidation or the next pipeline stage, which still require that separate pass plus a
consolidation document reconciling both, per CLAUDE.md's HARD RULE.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
