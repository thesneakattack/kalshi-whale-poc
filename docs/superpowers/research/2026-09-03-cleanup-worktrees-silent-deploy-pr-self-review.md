# PR self-review — fix/cleanup-worktrees-silent-deploy (PR #544)

Stage 1 of the PR-level review cycle CLAUDE.md's "nothing advances on one pass"
HARD RULE requires before merge, distinct from and in addition to the
design-stage cycle in the `-consolidation.md` file. Same author/context;
checks the PR as pushed against the design it claims to implement.

## Fix-by-fix check against the consolidation's merged fix list

1. **AR-1 (deploy table corrected to 7 fast-forwards / 3 reloads).** Applied to
   the design note (§2), not the script. Verified the note's table now lists
   all 7 with a per-row "touched a `.py`?" column. Correct.
2. **AR-2 (§3 rewritten: two triggers, `feat/persistence-layer-unified-connect`
   named).** Applied. The script's own new comment above `branch -D` (§7 item 3
   of the design) restates the two-trigger point in its own words rather than
   quoting the note verbatim — checked they say the same thing: "redundant …
   vacuous … or wrong" matches the design's three-way disposition exactly.
3. **AR-3 (is_ancestor=0 refusal gets a direct test).** Present:
   `test_a_branch_whose_tip_has_commits_not_yet_in_origin_main_is_kept_even_with_a_merged_pr`.
   Checked it actually exercises `is_ancestor=0` and not `is_clean=0` or
   `is_merged=0`: the added commit is on `wt` (the worktree checkout of
   `feat/x`), then committed — so `is_clean` is true, `pr_state` from the shim
   is still `MERGED` (env fixed at `_setup` time, unaffected by later commits)
   so `is_merged` is true, and only `merge-base --is-ancestor feat/x
   refs/remotes/origin/main` goes false because the new commit was never
   pushed to origin. Correct isolation of the one variable under test.
4. **AR-4 (test 3 rewritten to construct the actual no-upstream/stale-HEAD
   precondition).**
   `test_no_upstream_branch_with_stale_local_head_is_still_removed` resets
   local `main` to the pre-merge commit rather than deleting a remote ref.
   Verified against the description in the design note's §8 item 3 — matches.
   Separately falsified: reverted `-D`→`-d` locally, ran this one test, got
   the exact `set -euo pipefail` abort the PR fixes (`error: the branch
   'feat/x' is not fully merged`, exit 1), then restored `-D` and reran the
   full suite (16/16 green). This is the load-bearing regression proof for
   the whole PR and it was actually executed both ways, not just described.
5. **AR-5 (notice uses `--left-right --count`, states the refuse-cases).**
   Script uses `rev-list --left-right --count main...refs/remotes/origin/main`
   (triple-dot). Two behind-notice tests exist
   (`test_script_never_advances_local_main_and_reports_how_far_behind`,
   `test_diverged_local_main_does_not_abort_the_sweep`) plus an absence test
   (`test_no_behind_notice_when_local_main_is_already_current`). The
   suggested-command message states it "may refuse if a tracked file there
   has local modifications" — matches the design's requirement not to imply a
   guarantee the command can't back.

Nits (AR-6 through AR-11): census additions folded into design note §4 only
(not script-visible, correctly — they're context, not behavior this script
changes); `-d` reframed as redundant/vacuous/wrong in the script's own comment,
matching AR-7's wording almost verbatim; option G recorded in design note §6,
not implemented (correct — it was a "should-consider," not adopted); `--dry-run`
usage line corrected in the script; abort blast-radius description expanded in
design note §3 (script comment doesn't need this — it's about the old
behavior, already removed); SR-3's stale SHA is a self-review artifact, left
as historical record per convention, with the correction living in the
consolidation doc instead of rewriting history.

## Independent checks not already covered by the design-stage cycle

**Ordering check.** The design's §7 item 2 says the notice compares against a
freshly-fetched `origin/main`. Read the script: `git -C "$PRIMARY" fetch origin
main --quiet` now precedes the notice block (fetch moved before the
`if [ ... = "main" ]` check, where the old fast-forward used to follow it
directly). Confirmed by re-reading lines 176-186 — fetch first, then the
`ahead`/`behind` computation. Correct; this was not explicit in an earlier
draft and needed verifying it actually landed that way in the diff, not just
in the note.

**Comment line-number staleness.** The `-D` comment originally referenced the
`is_ancestor` check by a line number (`:194`) that had already drifted after
the notice block was inserted earlier in the file. Caught and fixed before
commit (changed to "this loop's own merge-base --is-ancestor check above" —
no line number to go stale). This is exactly the kind of small
self-inflicted staleness the fix-list recheck is supposed to catch; recorded
here so it isn't mistaken for something the adversarial pass needs to find
independently — it's already fixed.

**Test suite run, not just written.** `docker exec
ddev-kalshi-whale-poc-fastapi sh -c "cd /app/.claude/worktrees/cleanup-worktrees-deploy
&& python3 -m pytest -q tests/test_cleanup_worktrees.py"` → 16 passed, run
three times across the session (after initial write, after the `-d`/`-D`
falsifier round-trip, and once more before commit). Not asserted from reading
the test file — actually executed each time.

**Scope check.** `git diff --stat` for both commits together: `scripts/cleanup-worktrees.sh`,
`tests/test_cleanup_worktrees.py`, `docs/open-decisions.md`, plus the four
research-stage docs. Nothing in `services/`, no risk/broker/settlement/auth
code, no `data/*.db`, no CI config. Matches CLAUDE.md's automation-boundary
concern (moot here since this is a direct fix, not automation, but the file
list is worth recording for the adversarial pass to check independently
rather than trust).

**What this self-review does not re-verify.** It does not re-derive the
uvicorn/watcher mechanism, the `git branch -d` fallback semantics, or the
reflog counts from scratch — those were independently re-derived once already
by the design-stage adversarial review (fresh agent, own synthetic
reproductions) and are being treated as settled inputs to this PR, per
CLAUDE.md's guidance that each stage's adversarial pass doesn't re-litigate a
prior stage's. What this review checks is narrower and different: did the
implementation actually do what the reviewed design said, and did it do so
correctly on its own terms (ordering, isolation of test variables, actual
falsification).

## Verdict

No discrepancy found between the merged fix list and what shipped. Two
implementation-only issues were found and fixed before this review was
written (fetch ordering was already correct on inspection; the line-number
staleness was caught and fixed). Ready for the independent adversarial pass.
