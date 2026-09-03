# Consolidation: persistence-layer db.py migration implementation plan (PR #516)

Reconciles three independent passes against
`docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation.md`, per
CLAUDE.md's "nothing advances on one pass" HARD RULE:

1. **Self-review** (this session, plan's author) —
   `docs/superpowers/plans/2026-09-03-persistence-layer-db-migration-implementation-self-review.md`.
2. **Coordinator sign-off-condition check** (autotrade-1d) — PR #516 comment, checked the three
   conditions attached to the API-shape decision at the design stage (PR #505's "Open question
   for explicit sign-off").
3. **Independent adversarial review** (autotrade-a3) — cross-session message, formed
   independently of autotrade-1d's check, per autotrade-1d's own explicit request not to share
   conclusions between the two until both were done.

## Why no adjudication was needed

The two reviews (autotrade-1d, autotrade-a3) were run in parallel with no cross-contamination —
autotrade-1d explicitly withheld its findings from autotrade-a3 until both had reported. They
turned out **complementary, not conflicting**: each found exactly one real, non-overlapping gap,
neither found anything the other flagged, and neither found anything the self-review missed
that the other also missed (the self-review's own two catches —
`settlement_edge.py`→`window_observations`, `paper_broker.py`'s four tables — were independently
re-confirmed correct by autotrade-a3 against current source, not just trusted). No disagreement
between any of the three passes exists to adjudicate on the merits; every finding below is
additive.

## Findings and disposition

| # | Source | Finding | Severity | Disposition |
|---|---|---|---|---|
| S1 | Self-review | `settlement_edge.py`'s table is `window_observations`, not `settlement_edge` | Load-bearing (wrong table name in Task 1's fixture + Task 11) | **Fixed**, before this PR was first pushed — verified via `grep -n "CREATE TABLE" services/settlement_edge.py` |
| S2 | Self-review | `paper_broker.py` has 4 tables (`broker_meta`/`positions`/`trades`/`pending_orders`), not the 1-2 the pre-audit's summary implied | Load-bearing (Task 7 would have shipped an incomplete migration on the most safety-sensitive module in the plan) | **Fixed**, before this PR was first pushed — verified via full direct read of `services/paper_broker.py:163-267`, table added to Task 7's own text with a full column→table→line mapping |
| C1 (autotrade-1d) | Coordinator check | Sign-off condition 3 (referencing `fix/db-foundation-must-fix-tests`) not carried: Task 1 independently re-derives 3 of that branch's 4 relevant tests (reassuring — two authors converging) but silently drops the 4th (real lock-contention test) and never states the branch's disposition | Important, not blocking a GO per autotrade-1d's own framing (design spec classified the lock-contention test should-fix-not-blocking) | **Fixed**: Task 1 now has an explicit "Disposition of `fix/db-foundation-must-fix-tests`" note (adopted: the lock-contention test, transcribed as `test_lock_contention_raises_operational_error_matching_capture_writer_pattern`, now the 16th test in Task 1's suite; not adopted: the path-keyed registry the design spec's C2 finding rejected; branch fate: superseded once this task lands, no further code pulled in) |
| A1 (autotrade-a3) | Adversarial review | Task 3's event-loop note frames `candidate_log.py`'s exposure as "negligible," discussing only `clear_range()` — but omits that `count_range()` (one of the same task's own six migrated call sites) is the single most severe finding in the entire `/api/reset` event-loop investigation: 34,129.54ms against 22,596,141 rows (PR #512, merged), reachable from both `GET /api/reset/preview` and `POST /api/reset` | Real, load-bearing — a reader of Task 3 alone would underestimate this module's exposure | **Fixed**: Task 3's event-loop note rewritten to name `count_range()` explicitly, cite the exact figures and reachability path, and state plainly this is the worst-measured instance of the #510 pattern in this migration's scope while keeping the "not fixed here, tracked under #510" framing unchanged (matches Task 3's own no-fix mandate — Global Constraints still says this migration's job is closing connections, not routing through `tick_executor.run()`). Global Constraints' own event-loop bullet updated to match, so the summary-level reader gets the same correction, not just Task 3's detail. |

Every fix above was independently re-verified against primary source before being written into
the plan — not applied on the reviewer's word alone: `count_range()`'s existence, line number,
and `_reset_domain_counts`' direct call to it were re-confirmed by reading
`services/candidate_log.py:412-432` and `services/reset/routes.py:79-158` directly; the
34,129.54ms/22,596,141-row figures were re-confirmed by reading PR #512's own merged research
document (`docs/reset-routes-event-loop-blocking-2026-09-03.md`) rather than trusted from the
adversarial review's paraphrase; `e74096a`'s test content was re-confirmed by reading
`git show e74096a:tests/test_db.py` directly.

## Verdict: GO

All three findings are fixed on the branch. No open, unresolved finding remains from any of the
three passes. Per this repo's "a revision is checked against the fix list item by item, never
accepted on its own completion claim" rule: this consolidation *is* that check — each of the
three rows above states what was verified, not merely that a change was made. None of the three
fixes changed the plan's scope (module list, task count, or classification tiers are unchanged)
or introduced a claim neither review saw, so a full second self-review-plus-adversarial-review
cycle is not required — this consolidation stands as the completed gate for this PR-stage
review cycle.

This plan is ready to merge. Per coordinator autotrade-1d's explicit direction, this session
merges it directly once CI is green — the review cycle's completion (this document) is the gate,
not a further coordinator step.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
