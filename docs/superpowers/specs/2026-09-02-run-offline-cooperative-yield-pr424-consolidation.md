# Consolidation — PR #424 (`fix/run-offline-cooperative-yield`)

Reconciles the PR-stage self-review
(`...-pr424-self-review.md`) and the independent adversarial review (a
fresh, memory-less Agent-tool pass against the pushed branch and live
production data) of PR #424 as submitted, per CLAUDE.md's "nothing advances
on one pass" HARD RULE's PR-stage requirement. This is distinct from, and
in addition to, the branch-level self-review / adversarial-review /
consolidation / full-branch-review cycle already completed before this PR
was opened (`...-self-review.md`, `...-adversarial-review.md`,
`...-consolidation.md`, `...-full-branch-review.md`) — that cycle produced
this PR's content; this cycle checks the PR as submitted.

## Result: GO

Both reviews independently reached the same conclusion on the code: the
diff is correct, well-tested, and its performance claims hold up under
independent reproduction.

### Claims independently re-verified (adversarial review, fresh Agent, real production data)

All 6 of the PR body's numbered claims were re-derived from primary sources,
not taken on the PR's word:

1. 24h window bound + disclosure — confirmed correct for both the implicit
   default and explicit-`since_ts` callers, traced across all 3 real
   `run_offline()` callers.
2. UNKNOWN message rewrite — confirmed substantive: the calibration gate
   genuinely stays all-history and untouched, verified by reading its
   actual callers.
3. Pool/yield-point revert — confirmed via empty diff against `23f12e5` for
   `_aio_db.py`/`test_aio_db.py`: a genuine full revert, not a near-revert.
4. Combined-design performance numbers — independently reproduced with the
   reviewer's own throwaway script against real `/app/data/*.db`: cold
   3.20s / warm 2.68-2.72s / 5-concurrent 13.19-13.49s, versus this PR's
   claimed 4.30s / 2.78-2.80s / 13.98-14.28s — same order of magnitude and
   same qualitative shape, consistent with normal run-to-run variance on a
   live, actively-written system (file mtimes during both runs confirm real
   concurrent writer activity).
5. `docs/open-decisions.md` correction — confirmed honest and correctly
   scoped: the adjacent PR #388 and `yes_ask_dollars` entries are untouched,
   only the Task 9 line changed.
6. N7/N8 cleanup — confirmed: no dead top-level import, module still
   imports and runs, checks list is a plain literal again.

### Test non-vacuousness — independently falsified/verified

The adversarial review temporarily reverted just the `since_ts` default
line and confirmed `test_confidence_input_coverage_defaults_to_a_24h_window_not_full_history`
fails exactly as expected (`n=120`, not `n=60`), then restored the line and
confirmed a clean `git status`. This is the strongest form of test-quality
evidence available and removes any doubt this test could pass either way.

### Safety invariants — confirmed clean

No diff touches `config/settings.yaml`, `risk_manager.py`,
`kalshi_account_client.py`, `confidence_calibration.py`, `paper_broker.py`,
or any strategy/calibration/sizing code. No writes anywhere in
`run_offline()`'s call graph (guarded by an existing test, confirmed
present and passing). No Kalshi-shaped parsing/classification touched.

## Findings adjudicated

**Adversarial review's "arithmetic framing is imprecise" (Important) —
accepted on the merits, not fixed in code, noted here instead.** The PR's
open-decisions.md language ("the query-bound fix's ~1.0s-per-call savings
does compound under concurrency") implies roughly-additive per-call
savings across 5 concurrent calls. The adversarial review correctly points
out that `resolved_signals_with_factors()` runs via `asyncio.to_thread` on
its own worker thread, not the shared single `_aio_db` connection, so
concurrent calls' thread-offloaded work already overlaps rather than
serializes — making "compounds" an overstatement of the mechanism, even
though the measured direction (sub-additive ~1.0-2.4s reduction, not a
naive ~5s) is itself correct and matches what that mechanism predicts.
This is a wording-precision issue in a doc file, not a code defect or a
wrong number — adjudicated as not requiring a further commit; the
underlying measured figures in `docs/open-decisions.md` are accurate, only
the word "compounds" slightly overclaims the mechanism. Left as a
known-imprecise phrase rather than spending another commit on wording,
consistent with this repo's "decide, don't over-investigate" standing
guidance for a finding that doesn't change any number or any next action.

**Adversarial review's "cold-start magnitude not independently reproduced"
(Minor) — accepted, no action needed.** Both this PR and the independent
reviewer observed a cold-vs-warm gap in the same direction, differing only
in magnitude (1.5s vs 0.5s), plausibly explained by point-in-time
production write contention on both runs. Doesn't change any conclusion.

**Adversarial review's "the PR's own review-cycle gate is still open"
(Important) — this is what this consolidation, and the self-review before
it, exist to close.** Correct as identified; resolved by this document plus
`...-pr424-self-review.md`.

**Adversarial review's "stale checklist item" (Minor) — being fixed
directly in the PR body** (CI checkbox, and the review-cycle checkbox) as
part of closing this consolidation, before merge.

## Merged list of required fixes before merge

1. Update the PR body's Test Plan checklist: check off "CI (Woodpecker)"
   (confirmed all-green) and "PR-stage self-review + adversarial review +
   consolidation" (this cycle, now complete).
2. No code or doc changes required — both reviews found the diff itself
   correct as submitted.

## GO/no-go

**GO.** Proceed to update the PR body checklist, then `gh pr merge`.
