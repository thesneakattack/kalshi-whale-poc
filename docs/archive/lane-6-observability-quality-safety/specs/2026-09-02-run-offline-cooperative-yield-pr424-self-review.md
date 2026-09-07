# PR #424 Self-Review — `fix/run-offline-cooperative-yield`

Same-author self-review of the PR as submitted (`23f12e5..b44ba59`), per
CLAUDE.md's "nothing advances on one pass" HARD RULE's PR-stage requirement.
Scope: internal consistency and unaddressed scope in the PR as opened, not a
re-litigation of the branch-level full-branch review that already drove this
PR's content.

## What's actually in the diff

`git diff 23f12e5..HEAD --stat` (7 files, 1246 insertions, 8 deletions):
`services/diagnostics/diagnostics.py` (+59/-8), `tests/test_diagnostics.py`
(+36 new test), `docs/open-decisions.md` (1 line, corrected in place),
and 4 `docs/superpowers/specs/*.md` review artifacts. `_aio_db.py` and
`tests/test_aio_db.py` are absent from the diff entirely — confirmed via
`git diff 23f12e5..HEAD -- services/diagnostics/_aio_db.py tests/test_aio_db.py`
producing no output, i.e. byte-identical to the merge-base. That is the
intended shape: this PR's net effect on the codebase is small (one query
bound + its disclosure fixes + one test + one corrected doc line), with the
elastic-pool and yield-point ideas fully reverted rather than partially
walked back.

## Internal consistency check

- The PR body's narrative (kept the query bound, reverted the pool,
  corrected the figures) matches the actual diff content — verified by
  re-reading `git diff 23f12e5..HEAD -- services/diagnostics/diagnostics.py`
  and `docs/open-decisions.md` side by side with the PR body's claims
  before this self-review was written, not assumed from memory of writing
  them.
- `window_hours` is computed once, after `since_ts`'s default resolves
  (`since_ts = since_ts if since_ts is not None else now - 24*3600`), so it
  reflects the actual effective window whether the caller passed `since_ts`
  explicitly or not — this was a real risk (a naive implementation might
  have hardcoded "24h" in the message text regardless of what window was
  actually used) and is not present here.
- The `run_offline()` comment block correctly states `_aio_db.py` itself
  "carries no trace" of the pool attempt — true, since the file is a byte-
  identical revert. The comment is therefore the ONLY place in shipped code
  documenting that the pool was tried; if this comment were ever deleted
  without care, that history would only survive in git log and
  `docs/open-decisions.md`. Acceptable: git history and the open-decisions
  entry are this repo's designated permanent record per CLAUDE.md ("git
  log/blame/diff are the only maintained history").
- `docs/open-decisions.md`'s corrected entry accurately distinguishes
  "isolated mechanism savings" (~1.0s, independently reproduced) from
  "combined-design measurement" (13.98-14.28s wall for 5-concurrent, also
  independently reproduced by PR #424's own adversarial review at
  13.19-13.49s) rather than conflating the two, which is exactly the class
  of error (conflating a pool's effect with the query bound's effect) that
  produced the original wrong "269.79s -> 43.89s" figure this PR withdraws.

## Unaddressed scope

- The PR does not attempt a real checkout/lease connection pool redesign.
  This is explicitly out of scope per the full-branch review's own
  recommendation and is stated as such in the PR body — not a silent gap.
- `services/research/research.py`'s two differently-scoped `input_coverage`
  blocks (`confidence_calibration.input_coverage`, all-history, vs.
  `diagnostics.checks[].detail.input_coverage`, now 24h-scoped) live at
  different JSON paths, not colliding under one key — confirmed by reading
  `research.py:154-219` directly. No code change needed there; the window
  disclosure fix (N3) is the correct, sufficient fix for the ambiguity this
  could otherwise cause a reader.
- No change to `services/diagnostics/routes.py` or `services/quality/routes.py`
  — neither needed one; both already pass through to `run_offline()`/
  `check_confidence_input_coverage()` with whatever `since_ts` behavior they
  already had, and the fix's dimensional correctness holds for both.

## Gaps this self-review is flagging for consolidation

1. The PR body's Test Plan checklist has two stale/unchecked items as of
   this writing: "CI (Woodpecker) — pending as of PR open" (CI is now fully
   green, confirmed via `gh api .../commits/<sha>/status`) and "PR-stage
   self-review + adversarial review + consolidation — required before
   merge, not yet run" (the adversarial review has since completed; this
   document is the self-review leg; a consolidation document reconciling
   both is the next and final artifact before merge). Both need updating in
   the PR body before `gh pr merge`, per this repo's own "read the PR body
   before merging" rule — an unchecked box that's actually done is exactly
   the failure mode that rule exists to catch.
2. No new Critical or Important issues found beyond what the adversarial
   review already surfaced (its "arithmetic framing is imprecise" finding on
   the compounding-savings language is accepted as correct on its merits —
   see consolidation).

## Self-review verdict

No new blocking findings. Proceeding to reconcile with the independent
adversarial review in a consolidation document.
