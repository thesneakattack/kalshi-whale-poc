# Diagnostics Pool Addition (Revision 3) — Review Consolidation

Scoped to only the new material added in revision 3 (sections 1b, two new
non-goals, 4a, section 6's diagnostics bullets, section 7, section 8's revision-3
bullets) — the whale-scoring half of this spec already passed its own review cycle
and is not re-litigated here.

## Verdict: NO-GO on the new material as written. Revise, then recheck.

Every core structural claim held up (same `tick_executor` singleton, real recurring
faults, live hang reproduced independently) — but the causal narrative is
incomplete in a way that changes both the fix's framing and its sizing, and it
missed an already-documented, same-day parked decision it should have cited.

## Merged required-fix list

1. **(Real, load-bearing) A sibling, already-documented cost inside the same
   `run_offline()` call was missed entirely.** `docs/open-decisions.md` already
   records (2026-09-01, whale-confidence-scoring-remediation Task 9): one of
   `run_offline()`'s *other* pre-loop checks, `check_confidence_input_coverage()`,
   calls `resolved_signals_with_factors()` with no `since_ts` — a full unscoped
   fetch against `signal_log.db`'s 103k+ rows, independently measured at ~1.0-1.1s,
   already flagged as "roughly a 20% duty cycle on [tick_executor] for this one
   diagnostic alone." Section 1b's root-cause narrative reads as if `funnel()`/
   `raw_trades` were the whole story. Must cite this entry directly rather than
   silently re-deriving a partial picture — and note explicitly that section 4a's
   isolation fix *also* resolves this item's specific "eats into tick_executor
   capacity" framing as a side effect (the whole `run_offline()` call leaves that
   pool, not just the per-series loop), even though the underlying per-call cost of
   `resolved_signals_with_factors()` itself is unchanged (still the query-bounding
   non-goal, correctly out of scope). `docs/open-decisions.md`'s entry should note
   this resolution once 4a ships, not be silently left stale.
2. **(Correctness) Two citation inaccuracies, fix before this is trusted:**
   `funnel()` actually spans lines 519-652, not 519-567 as cited (the two specific
   query-line citations, 543-547/548-552, are exact — only the outer span is
   oversold). `check_series_funnel()` does not call `funnel()` unconditionally — it
   calls `reconcile()` first for every series (its own real query cost, never
   mentioned) and only calls `funnel()` when both `signal_accuracy_pct` and
   `realised_win_rate_pct` are non-None.
3. **(Reframe required, not just a wording fix) The fix confidently explains one
   symptom and only partially explains the other — say so precisely, don't imply
   otherwise.** `tick_executor` pool-sharing is well-supported as the cause of
   recurring `capture_writer` "database is locked" faults (verified: same pool
   object, faults still recurring live, ~11 minutes into a fresh process). But the
   broader "the bare event loop stalls, other endpoints hang too" symptom is *not*
   fully explained by this mechanism: `GET /api/health/pipeline` (confirmed via
   source to use `asyncio.to_thread`, never `tick_executor`) was independently
   reproduced hanging too. The more likely explanation is the *other* half of this
   same spec — the whale-scoring connection-reuse fix, which (until it ships) still
   runs on that same shared default `asyncio.to_thread` executor. Section 1b/4a
   should say plainly: this fix resolves the `tick_executor`/`capture_writer`
   mechanism specifically; the broader event-loop-stall symptom needs *both* fixes
   in this spec shipped together to be fully addressed, not section 4a alone. This
   strengthens the case for the unified spec rather than weakening it — say that.
4. **(Design change, not just documentation) 1 worker for the diagnostics pool is
   an unverified, probably-wrong assumption — reconsider the count.** The spec's
   justification ("never concurrent with itself in practice") doesn't hold: real,
   structural other callers exist beyond the dashboard's 5s poll —
   `.claude/hooks/guard_workflow.py`'s R2 rule routes sessions to this exact
   endpoint, CLAUDE.md's own "Start investigations here" names it step 1 (printed
   every session banner), `tools/quality_coordination.py` also calls it, and this
   repo explicitly runs multiple parallel Claude sessions that each independently
   check it (this investigation's own two peer sessions today are a live example).
   With individual calls already running 15-20s+, overlap is plausible, not an edge
   case. Size for realistic known concurrent callers (poll + occasional manual/
   tooling checks) rather than either the unverified "1 is enough" or a blind bump
   — and make the pool's own queue depth observable (same "bounded and visible, not
   silently thrashing tick_executor" property section 4's whale-scoring pool
   already established) so if this sizing turns out wrong, it shows up as
   measurable backlog, not a guess nobody checks.
5. **(State explicitly, not implicitly) After this fix, `/api/quality/summary`
   will likely still be slow** — item 1's sibling cost and `funnel()`'s own
   per-series scans are both untouched by an isolation-only fix. Real win (stops
   taking `capture_writer` down with it), but the spec should say plainly that the
   endpoint itself may still look "hung" from the dashboard's perspective, not
   imply the user-facing symptom fully resolves.

## Not required, informational

- Handspun-vs-proven / pool-consolidation: reviewer confirmed keeping diagnostics on
  its own pool (not folded into the 4-worker whale-scoring pool) is correct —
  incompatible workload shapes (many-short-latency-sensitive vs. few-very-long), and
  sharing would relocate today's exact failure mode onto the pool responsible for
  real-time signal detection. No change needed.
- `run_offline()` confirmed fully read-only (`tests/test_diagnostics.py`'s own mtime
  test covers the whole function) — moving the entire call to a new pool, not just
  the per-series loop, is safe.

## Disposition

Items 1-3 change the narrative/framing (and item 1 requires a cross-reference to an
existing parked decision); item 4 is a real design change (worker count); item 5 is
a stated-explicitly correction. None of this invalidates the isolation approach
itself — every reviewer opinion on the mechanism (separate pool, not shared with
`tick_executor` or the whale-scoring pool) held up.
