# PR consolidation — PR #544 (`fix/cleanup-worktrees-silent-deploy`, closes #535)

Reconciles the PR self-review and the independent PR adversarial review into
one verdict and merged fix list. Stage 3 of the PR-level review cycle
CLAUDE.md's "nothing advances on one pass" HARD RULE requires before merge —
distinct from and in addition to the design-stage cycle already completed
(`2026-09-03-cleanup-worktrees-silent-deploy-consolidation.md`).

## Verdict: **GO, after applying the fix list below**

No disagreement between the two PR-stage reviews needed adjudication. The
adversarial review's findings are corrections and additions the self-review
didn't reach (it explicitly scoped itself to checking the implementation
against the design's fix list, not to re-deriving the mechanism or
independently constructing new attack shapes) — nothing in the self-review
was contradicted. The adversarial review's own headline result: it built an
independent synthetic harness (different repo layout, a second worktree in
the sweep, the literal 2026-08-31 incident configuration) and ran the real
script against it, plus three deliberate mutations of the fix, each caught by
the test suite and each reverted with a verified-clean `git status`/`git
diff`. No blocking finding from either review.

## Merged fix list, applied before merge

1. **PR-AR-1 (should-fix).** The script's header comment (`:14-17`, item 2 of
   the staleness checklist) still described local `main` as "only
   fast-forwarded when the primary checkout happens to be on main itself" —
   exactly the behavior this PR removed. Reworded to state local `main` is
   never touched by this script (citing #535), so it can be arbitrarily
   stale, which is the actual current reason `origin/main` rather than local
   `main` is the correct comparison target.
2. **PR-AR-3 (should-fix, applied as a code change rather than deferred to a
   follow-up issue).** `merge-base --is-ancestor "$branch" ...` resolves a
   bare ref name as a tag before a branch (gitrevisions(7) order); a
   same-named tag pointing at an already-merged commit would make the
   ancestry check pass for the tag while the actual branch still carried an
   unmerged commit. Reviewer's own scenario (j) reproduced this against the
   real script and showed the old `-d` refused where `-D` (this PR's own
   change) would delete the unmerged work — meaning this PR's `-d`→`-D`
   change is exactly what promotes this pre-existing resolution weakness from
   latent to load-bearing. Fixed to `refs/heads/$branch`, the same
   disambiguation the script already applies to the `origin/main` side
   (`:208-211`, unchanged). Chosen over deferring to a follow-up issue: it is
   a one-token change directly closing a gap this PR itself creates, not
   unrelated scope; the real repo has zero tags today so nothing here changes
   current behavior, only removes a hazard this PR would otherwise ship.
   Falsified before landing: reverted to the bare `"$branch"` form, ran the
   new regression test, confirmed it fails by actually deleting the branch
   (`wt.exists()` False) exactly as the reviewer's scenario (j) predicted;
   restored the fix, confirmed the full suite (17/17) passes again.
3. **PR-AR-4 (nit, folded in).** The `-D` comment cited
   `docs/open-decisions.md #35` by line number, in a file whose own header
   says lines are removed once resolved — a number certain to drift. Changed
   to cite by content (issue #535 and "this script's branch-deletion
   behavior") instead.
4. **PR-AR-5 (nit, folded in).** Three stale references in the test file:
   the module docstring's "4f35ea5 ff-only-failure-must-not-abort" label
   didn't note that fix was itself superseded by this PR (the ff-only merge
   it guarded no longer exists); `test_stale_local_main_...`'s docstring
   still described the now-removed fast-forward in the present tense; the new
   AR-3 test cited the refusal message's old pre-PR line numbers. All three
   corrected.
5. **New test added, not in the original design or self-review: the
   tag-shadowing regression itself
   (`test_a_same_named_tag_does_not_make_an_unmerged_branch_look_merged`).**
   The adversarial review found this as an exploratory probe (scenario j),
   not as a gap in the shipped test list — it is added here because PR-AR-3's
   code fix needs its own falsifying test by the same standard applied to
   every other change in this PR (actually run failing, then passing). One
   test-helper wrinkle handled: `git branch --format=%(refname:short)` prints
   the surviving branch as `heads/feat/x` rather than the bare `feat/x` once
   a tag of the same name exists (git's own disambiguation), noted inline so
   a future reader doesn't mistake it for a bug in the assertion.

## Not applied, with reasons

- **PR-AR-2 (PR body test-count wording).** Applied directly to the PR body
  via `gh pr edit`, not to any file — see the merge-readiness section below.
- **PR-AR-6 (self-review's "ordering check" overstated what changed — the
  fetch was already before the comparison, not moved).** Left as a historical
  artifact in the self-review file rather than rewritten, consistent with
  this initiative's existing convention (the design-stage self-review's
  stale-SHA reference, SR-3, was likewise left in place and corrected in that
  stage's consolidation instead). Recorded here as the correction of record.
- **PR-AR-7 (open-decisions.md's file-header "remove when done" rule vs. the
  de-facto "leave a dated RESOLVED entry" convention).** Real, but pre-existing
  drift across 12 other entries in that file, not something this PR
  introduced or should unilaterally resolve by picking a side.
- **PR-AR-8, PR-AR-9.** Both are the reviewer's own explicit "observation, no
  action" findings — the notice's ≥1-worktree gating and the short-vs-long
  `origin/main` spelling are both working as intended.

## Merge-readiness actions (not file changes)

- **CI**: confirmed `575cf096` (the PR-self-review commit the adversarial
  review reviewed) at 12/12 `success` via
  `gh api repos/thesneakattack/kalshi-whale-poc/commits/.../status`. The new
  commit carrying this consolidation's fixes needs its own fresh CI
  confirmation before merge — the merge commit is a new, untested
  combination per branching-and-ci.md, not something the prior green run
  covers.
- **PR body**: the "10 pre-existing ... plus 6 new" test-count claim
  (PR-AR-2) is being corrected to reflect the actual post-consolidation
  count (12 pre-existing/retargeted + 5 new, after this fix list's addition)
  via `gh pr edit`, along with checking off the CI checklist item against the
  confirmed 12/12 result.
- **Labels**: add `phase:research` (PR-AR-10) alongside the existing
  `phase:implementing` — this PR bundles the research/design commit
  (`d12590e`) with the implementation, and branching-and-ci.md requires both
  labels when a PR bundles more than one stage.

## Authority note

Per CLAUDE.md, self-review and adversarial review are separate, non-duplicated
passes, and the adversarial pass must be a fresh Agent call with no memory of
the authoring session — satisfied here (the PR adversarial review agent had no
context beyond the PR, the design-stage docs, and CLAUDE.md itself, and
independently re-ran the script rather than trusting the self-review's
claims). This consolidation, like the design-stage one, is produced by the
same session that wrote the implementation and the self-review, consistent
with "author and reviewer stay separate" applying to the adversarial pass
specifically, not to who performs consolidation.
