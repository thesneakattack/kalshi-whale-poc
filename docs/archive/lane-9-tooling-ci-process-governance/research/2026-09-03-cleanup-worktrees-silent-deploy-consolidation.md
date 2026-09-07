# Consolidation — cleanup-worktrees silent-deploy design note

Reconciles the design note, its self-review, and the independent adversarial
review into one verdict and one fix list. Stage 4 of 4 for this research/design
stage. No disagreement between the two reviews needed adjudication — the
adversarial review's findings are either confirmations, sharpenings of a
self-review finding, or new findings the self-review didn't reach; nothing in
the self-review was contradicted.

## Verdict: **GO**

Every claim the adversarial review could have overturned it confirmed:
primary-as-deploy-source and the `*.py`-only watcher (source + live `/proc/1`
read), the `branch -d` HEAD-fallback mechanism and that removing the
fast-forward ships the abort under `set -e` (12 synthetic scenarios), that
`-D` loses no safety the script doesn't already have from `is_ancestor` at
`:194` (no losing case constructed against real branch-protection settings),
and the census showing no consumer of local `main` breaks (only degrades, and
only a dormant one). The design — remove the fast-forward, print a
behind-notice, `git branch -d` → `-D` — stands as recommended.

What was wrong across both reviews was evidence precision, not the decision:
an overcounted/miscounted deploy table, an incomplete statement of which live
branches are actually at risk, and two under-specified tests. All are fixed in
the note (below) before implementation, per CLAUDE.md's "nothing advances on
one pass" — this artifact is the GO gate; the two must-fix items below
(AR-3, AR-4) additionally gate the implementation PR's test list.

## Merged fix list

Applied to `2026-09-03-cleanup-worktrees-silent-deploy.md` directly (this
consolidation records what changed and why; it does not duplicate the prose):

1. **AR-1 (was SR-4, sharpened).** §2's deploy table overcounted by omission and
   included a wrong member. Corrected to: 7 script-spelled fast-forwards on
   2026-09-03 (01:39:01, 03:48:42, 07:41:53, 12:12:42, 12:32:14, 16:40:05,
   17:15:23 UTC), of which 3 touched a `.py` file outside `.claude/worktrees/`
   and reloaded (03:48:42, 16:40:05, 17:15:23) — 12:32:14Z touched none and is
   dropped from the deploy claim. 17:15:23Z remains the exact match for #535.
   "3 deploys" survives as a claim; "3 fast-forwards" does not.
2. **AR-2 (was SR-5, corrected, not just sharpened).** §3's "4 of 12 live
   branches" evidence was wrong for 3 of the 4 — their upstream is
   `origin/main`, so `-d`'s check lands on the right ref and stale local `main`
   doesn't matter (confirmed against real `branch.<b>.merge` config, not
   assumed). The actual live shape today is `feat/persistence-layer-unified-connect`
   (no upstream configured), which the note's original condition
   ("remote-tracking ref absent") didn't even name as a trigger — the real
   second trigger is "no upstream configured" independent of whether the
   remote ref exists. §3 rewritten to state both triggers and the one branch
   that currently matches either. `docs/open-decisions.md` #35 remains the
   load-bearing incident evidence, unaffected.
3. **AR-3 (new, gates the implementation PR).** No existing or proposed test
   covers the `is_ancestor=0` refusal path (`:252-253`, "branch has commits not
   yet in main"). Under `-D` this becomes the sole guard between a `gh`-reported
   MERGED PR and deleting commits not yet in `origin/main`. Added to §8 as a
   required test, independent of the note's original three.
4. **AR-4 (fixes a bug in the note itself).** §8's original test 3 was
   mis-specified: it described "remote ref missing" as the trigger, but in the
   test harness's own `_setup()`, `feat/x` has no upstream configured and the
   remote ref is present — deleting the ref is unnecessary, and local `main`
   already contains the branch tip after `_setup`'s own merge, so "origin
   ahead" alone would not make `-d` refuse. Rewritten to construct the actual
   failing precondition (reset local `main` below the tip while `main` is
   checked out, so HEAD-relative merge-check fails) and to name the no-upstream
   mechanism in the docstring.
5. **AR-5 (design fix, not just wording).** The behind-notice as originally
   specified (`rev-list --count main..origin/main` + "run this exact merge
   command") fails silently in two real cases: a diverged local `main`, and a
   locally-modified tracked file the pull would touch (confirmed live — the
   primary has `M config/settings.yaml` right now, S12). Changed to
   `rev-list --left-right --count main...refs/remotes/origin/main`, with a
   "diverged (N ahead)" branch in the message and a note that the suggested
   command can itself refuse on local modifications rather than silently
   failing a second time.

Nits folded in without separate discussion (AR-6 through AR-11): census
additions (`delete_merged_branch`/`delete_sdd_scratch`, dormant;
`docs/next-action.md:134`, a reader the fix improves rather than harms);
`-d` reframed as redundant/vacuous/wrong by upstream config rather than
"independent" (AR-7 — this line goes in the script comment verbatim so nobody
re-adds `-d` believing it duplicates `is_ancestor`); option G (self-gating
`fetch origin main:main`) recorded as a rejected-but-reasonable alternative,
not adopted, since §4/AR-6's readers are dormant or advisory and A is simpler;
`--dry-run`'s doc line corrected from "never mutates anything" to "never
touches the working tree, a branch, or a worktree"; the abort's blast radius
in §3 extended to cover the skipped remote-branch delete, summary line, and
rest of the sweep; SR-3's pinned SHA replaced with "0 behind at review time,
no SHA" since it was already stale by the time the adversarial review ran.

## What did not change

Options B/C/D remain rejected for the reasons in §6, now with S12 (the ff
already silently no-ops on a dirty tracked file) added as further evidence the
"courtesy" was never fully reliable even before #535. The core argument is
untouched: there is no way to advance local `main` while it's the primary's
checked-out branch without rewriting the bind mount, so "keep main current"
and "deploy" are the same operation, and routine worktree hygiene should not
be the thing that decides to deploy.

## Authority note

Per CLAUDE.md, self-review and adversarial review are separate, non-duplicated
passes; this document is the third, consolidating one, produced by the same
session that wrote the design note and self-review (not the adversarial
review) — consistent with "author and reviewer stay separate" applying to the
adversarial pass itself, not to who performs consolidation.
