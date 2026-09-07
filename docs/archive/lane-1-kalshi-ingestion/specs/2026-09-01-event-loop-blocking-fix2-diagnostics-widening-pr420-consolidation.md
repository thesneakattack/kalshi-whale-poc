# PR #420 Consolidation — PR-Stage Cycle

Reconciles `...-pr420-self-review.md` and `...-pr420-adversarial-review.md`,
the second required review cycle per CLAUDE.md's "nothing advances on one pass"
stacking requirement (self-review + adversarial review + consolidation running
again against the PR as actually submitted, distinct from the branch-level
cycle in `...-pr-self-review.md`/`...-pr-adversarial-review.md`/`...-pr-consolidation.md`).

## Result

**GO.** Both reviews agree: the PR as opened is byte-identical to the branch tip
the branch-level cycle approved, all 5 required CI contexts are genuinely green,
and a fresh independent read of `services/diagnostics/_aio_db.py` against the
installed `aiosqlite` 0.22.1 source confirmed every load-bearing claim in the
connection-lifecycle logic (non-daemon thread, close() no-op, ValueError signal,
cross-loop freedom, compare-and-swap eviction, schema-init close-before-raise).

## Findings and disposition

- **F4 (bookkeeping — Test-plan checkbox said "done" but was unchecked):**
  fixed directly in the PR body (this is the PR body's own text, not the
  implementer's diff — corrected by the review-cycle owner, not dispatched).
- **F2 (Minor — outage-warning wording had gone stale since it was written):**
  fixed directly in the PR body, re-verified against the live container
  (`aiosqlite` is importable today via an ephemeral writable-layer install, not
  the image; the rebuild is still genuinely required for durability). Corrected
  by the review-cycle owner, same reasoning as F4.
- **F1 (Minor — exit-cleanup hook's own comment overstates its failure-mode
  safety) and F3 (Minor — one test file missing the `_reset_aio_db_cache`
  fixture, measured accumulation but no hang):** both are real code/test
  changes, dispatched to a fresh implementer round for proper TDD treatment
  rather than fixed unilaterally by the controller — tracked separately, see
  that round's own commit(s) if it lands before merge, otherwise recorded in
  the PR body's "Deferred, not blocking" section as legitimate small follow-ups
  that don't block merge on their own severity.

## GO/no-go

**GO for merge.** Neither Minor finding is a correctness regression, a safety-gate
weakening, or something that changes the reviewed behavior — both are
defense-in-depth improvements on an already-verified-working mechanism (C1's
fix independently reconfirmed working under the exact load F3 measured). Proceed
to real CI confirmation (already done — all 5 required contexts green) and
`gh pr merge --merge`, then execute the I6 outage-minimizing sequence immediately.
