# PR adversarial review — PR #544 (`fix/cleanup-worktrees-silent-deploy`, closes #535)

Stage 2 of the PR-level review cycle CLAUDE.md's "nothing advances on one pass"
HARD RULE requires before merge. Fresh Agent call, no memory of the authoring
session. Reviewed at PR head `575cf096` (the self-review commit; the worktree
moved from `629d57c3` to `575cf096` while this review was starting — the
parent session committed the self-review concurrently; the file on disk was
byte-identical to the committed one). Base `origin/main` = `d386b732`.

Stance: the implementation is wrong until checked. The design-stage
mechanism claims (bind mount + `--reload`, `branch -d` HEAD fallback, `-D`
safe under `is_ancestor`) are treated as settled by the prior cycle and were
not re-derived; what follows checks whether the shipped script and tests
actually do what that design says.

Constraints honored: the real `scripts/cleanup-worktrees.sh` was never run
against the real repo in any form; `main` in the primary was not touched;
nothing pushed, commented, or merged. Every synthetic repository lives under
the session scratchpad (`pr-review-adversarial/`, driver `scenarios.sh`,
`gh`/`ddev` shims in `bin/`, `fake_guard.py`). All three mutations below were
applied to this worktree's script, run, and reverted with
`git checkout -- scripts/cleanup-worktrees.sh`; `git status --short` and
`git diff --stat HEAD` were empty after each revert and at the end.

Reproducibility note on my own harness: the first scenario run had a bug —
`git clone` of the bare origin defaulted to `master`, so "origin ahead"
shapes silently ran with origin **not** ahead. Fixed (`clone -b main`) and
every scenario re-run; only the re-run results are cited below.

---

## 1. The script as shipped (`scripts/cleanup-worktrees.sh` @ 575cf096)

Read in full (299 lines).

- **Fast-forward is gone.** `grep -n 'ff-only\|merge ' ` on the script hits
  only comment/notice text (`:170`, `:182`, `:184`). No `git merge` command
  remains. The old block (origin/main `:165-167`: `if HEAD = main; then git
  merge --ff-only refs/remotes/origin/main --quiet || true; fi`) is replaced
  by `:177-186`.
- **Only one branch deletion, and it is `-D`.** `grep -n 'branch -d\|branch -D'`
  → `:170` (comment), `:279` (`git -C "$PRIMARY" branch -D "$branch"`). No
  other `-d` call site exists. The old `-d` was at origin/main `:244`.
- **Notice logic (`:177-186`) is correct bash.**
  - `:177` gates on `symbolic-ref --short HEAD` = `main` exactly; a detached
    HEAD yields `""` via `|| echo ""`.
  - `:178` `counts` is always set: `rev-list ... || echo "0<TAB>0"`. Verified
    the fallback literal is a real tab byte, not spaces: `sed -n 178p | tr -cd
    '\t' | wc -c` → `1`; `od -c` shows `"   0  \t   0   "`.
  - `:179-180` `cut -f1`/`-f2` on `rev-list --left-right --count`'s
    `<ahead>\t<behind>` output. `${ahead:-0}`/`${behind:-0}` default an empty
    field to 0.
  - `:181` diverged branch requires both > 0; `:183` behind-only; ahead-only
    and equal are silent. No off-by-one: (a) 3 behind → "3 commit(s)", (g) 1
    ahead / 2 behind → "(1 ahead, 2 behind)", both from live runs below.
  - Nothing in the block mutates any ref or the working tree; `rev-list`,
    `cut`, `echo` only. The `fetch` at `:176` writes only under `.git/`.
- **Header/usage edits.** `:38-41` now says `--dry-run` "never touches the
  working tree, a branch, or a worktree (it still fetches...)" — accurate.
  BUT `:14-17` still reads "local main is only fast-forwarded when the
  primary checkout happens to be on main itself, so it can be arbitrarily
  stale" — that clause describes behavior this PR removed (see PR-AR-1).
- **`-D` comment (`:263-278`)** matches the design's AR-7 wording
  ("redundant ... vacuous ... wrong") and correctly cites
  `tools/quality_coordination.py`'s `delete_merged_branch` (`:474-489` on
  this branch still uses `-d` with its own `is_ancestor` against local
  `main`, docstring "never -D" — confirmed by reading it). The comment cites
  "docs/open-decisions.md #35" by line number (see PR-AR-4).

## 2. Synthetic repositories, real script (host git 2.43.0)

Driver: `scratchpad/pr-review-adversarial/scenarios.sh`. Each scenario builds
a fresh primary (`main` with `initial`), a bare `origin.git`, a worktree
`.claude/worktrees/feat-x` on `feat/x` pushed **without** `-u` (no upstream,
same as the test harness), merged into `main` and pushed, plus a separate
clone for remote-only commits. `gh` shim answers `feat/x` → MERGED #7,
`feat/y` → OPEN #8; `ddev` shim always fails `describe`; guard prints an
empty `#sessions v1`. The script is invoked with `cwd` = the synthetic
primary, exactly as `tests/test_cleanup_worktrees.py::_run` does.

| # | Shape | Result (PR script @ HEAD) |
|---|---|---|
| (a) | primary on `main`, origin/main **3 ahead**; `--dry-run` then act | exit 0 both. `rev-parse main` unchanged after both. stderr both times: `note: primary's local main is 3 commit(s) behind origin/main - not pulling (that would deploy the live app via uvicorn --reload). To deploy deliberately: git -C "<primary>" merge --ff-only origin/main (may refuse if a tracked file there has local modifications)`. Dry-run: `stale: feat/x ... would remove`, worktree still present. Act: `removed: feat/x`, local + remote branch gone, `origin/main ahead of main by: 3` afterwards. |
| (b) | **no-upstream** `feat/x`, local `main` `reset --hard` to `initial` (HEAD lacks the tip; origin/main has it), plus a second OPEN worktree `feat/y` **after** it in the sweep | exit 0. `removed: feat/x`, `cleanup-worktrees: 1 removed, 1 kept`. Branch gone, worktree dir gone, `feat/y` still registered, `main` still at `initial`, notice "1 commit(s) behind". The sweep continued past the deletion. **Old origin/main script on the same shape:** exit 0 but `local main still at initial? no` — it fast-forwarded (the deploy) and only then did `-d` pass. |
| (c) | `feat/x` tip has an **unpushed** commit; gh says MERGED; `--dry-run` then act | exit 0 both, stderr empty. Dry-run: `keeping: feat/x (branch has commits not yet in main)`. Act: `0 removed, 1 kept`; branch, worktree and the commit all survive. No error, no abort. |
| (d) | primary **detached**, origin/main 1 ahead | exit 0, `note:` lines = 0, `feat/x` still removed (is_ancestor reads origin/main, not HEAD). |
| (e) | primary on `main-something`, then on `foo/main`, origin 1 ahead | exit 0, `note:` lines = 0 in both. |
| (f) | primary on `main`, **ahead only** (`counts` = `1\t0`) | exit 0, silent. |
| (g) | **diverged**: 1 local ahead, 2 remote behind | exit 0, `note: primary's local main has diverged from origin/main (1 ahead, 2 behind) - not touching it; a plain merge --ff-only will refuse`; `main` unchanged. |
| (k) | the **literal 2026-08-31 incident** (design AR's S2): `feat/x` has upstream `origin/feat/x`, remote ref then deleted + pruned, primary on `other` (from `initial`, lacks the tip) | PR script: exit 0, `removed: feat/x`, no notice (not on main). **Old script:** exit 1, `error: the branch 'feat/x' is not fully merged`, worktree dir already gone, branch dangling — the open-decisions #35 abort, reproduced. |
| (j) | **probe**: tag named `feat/x` pointing at origin/main; branch tip has an unmerged commit; gh says MERGED | `git rev-parse feat/x` → `warning: refname 'feat/x' is ambiguous`, resolves to the **tag**. `merge-base --is-ancestor feat/x origin/main` → yes; `... refs/heads/feat/x ...` → no. PR script: exit 0, `removed: feat/x`, **unmerged commit reachable from any ref? 0**. Old script (`-d`, HEAD current): exit 1, `not fully merged`, branch and commit survive. See PR-AR-3. |

Tests are not overfit to their harness: (b), (c), (k) reproduce the
design's claims under an independently written harness with a different
repo layout, a second worktree in the sweep, and the incident's exact
upstream/prune configuration, which the shipped test does not construct.

## 3. Test suite and mutation sensitivity (fastapi container)

Baseline: `docker exec ddev-kalshi-whale-poc-fastapi sh -c "cd
/app/.claude/worktrees/cleanup-worktrees-deploy && python3 -m pytest -q
tests/test_cleanup_worktrees.py"` → **16 passed in 8.27s**. Test count
on `origin/main`: 12; on HEAD: 16 (4 new functions; `test_diverged_...`
retargeted in place).

Mutations (each applied with the Edit tool to this worktree's script; the
repo's own PostToolUse `run_tests.py` hook ran the file automatically on each
edit; each reverted with `git checkout --` and verified clean before the next):

| Mutation | Result | Which test caught it |
|---|---|---|
| M1: old `merge --ff-only refs/remotes/origin/main --quiet \|\| true` re-inserted after `:177`, notice block kept | **1 failed, 15 passed** | `test_script_never_advances_local_main_and_reports_how_far_behind` — `rev-parse main` moved after `--dry-run` (`AssertionError: assert '9dbcb522...' == '2fd8c0a6...'`) |
| M2: exact old 3-line block restored (`:178-185` deleted, ff-only in their place) | **2 failed, 14 passed** | the above, plus `test_diverged_local_main_does_not_abort_the_sweep` — `'diverged from origin/main (1 ahead, 1 behind)' in r.stderr` false; stderr held git's `hint: Diverging branches can't be fast-forwarded` instead |
| M3: `:279` `-D` → `-d` | **1 failed, 15 passed** | `test_no_upstream_branch_with_stale_local_head_is_still_removed` — `assert r.returncode == 0` fails with `error: the branch 'feat/x' is not fully merged` (exit 1), after the behind-notice printed |

So: a reverted fix is caught in every form tried, and the PR body's
"falsified against the old `-d`" claim is independently reproduced.

## 4. Things the self-review could have missed

- **`counts` unset?** No path: `$( ... || echo ...)` always yields a value;
  `set -e` does not fire inside a `||` list. If `rev-list` failed for any
  reason the block goes silent (`0\t0`), never aborts.
- **`cut` on bad input.** Probe (`parse_probe.sh`, `set -euo pipefail`,
  same code shape): `''` → silent; `'0 0'` (space) and `'abc\tdef'` → `[:
  integer expression expected` on stderr, notice suppressed, **no abort**
  (`[` failing inside an `if` condition is exempt from `set -e`). Not
  reachable in practice: `rev-list --left-right --count` always emits a
  single tab, and the fallback's tab byte is verified above.
- **Notice firing when it should not:** detached (d), `main-something` and
  `foo/main` (e), ahead-only (f) all silent — verified live, not reasoned.
- **Notice not firing when it arguably could:** `:156-159` exits before
  the fetch when there are zero branch worktrees, so a behind local `main`
  with no linked worktrees prints nothing. Consistent with the notice's
  stated purpose (explain a pull the script isn't doing) — observation only.
- **Dead code:** none introduced. `SCRIPT_DIR` still used at `:137`.
- **`is_ancestor` uses the bare `"$branch"` (`:213`).** With `-D` this is
  the sole merge proof. gitrevisions resolves `refs/tags/<name>` before
  `refs/heads/<name>`, and scenario (j) shows a same-named tag makes the
  proof pass for the wrong object and `-D` then deletes an unmerged branch
  that `-d` refused. Pre-existing resolution weakness that this PR
  promotes to load-bearing (PR-AR-3). The script already reasons about
  exactly this hazard for `refs/remotes/origin/main` at `:208-211`.
- **Race between `:213` and `:279`:** the worktree is removed first, and
  git refuses to check the same branch out elsewhere, so no second checkout
  can add a commit in the window; `-d` would not have helped there either.
- **Scope:** `git diff --stat origin/main..HEAD` = the script, the test
  file, `docs/open-decisions.md`, 5 research docs. Nothing under
  `services/`, no risk/broker/settlement/auth, no `data/*.db`, no CI config.

## 5. `docs/open-decisions.md`

Edited line `:35`, the entry that parked this exact `-d` → `-D` change.
Shape matches the de-facto convention of the 12 other in-place `RESOLVED
<date> (<PR/branch>): ...` entries (e.g. `:15`, `:21`, `:39`): dated, names
the issue and branch, states what shipped and why the removed `-d` check was
never an independent guard, and ties it to the fast-forward removal. The
content is accurate against the diff (`-D` at `:279`, ff removed, comment
present). Two qualifications: (i) the file's own header (`:3-7`) says
"Remove a line when it is done; never archive here", which every RESOLVED
entry including this one ignores — pre-existing repo-wide drift, not this
PR's, but "matches conventions" should be read as "matches practice";
(ii) the original entry asked for a test of "primary not on main + branch
merged into origin/main but not local HEAD" — the shipped test uses primary
**on** `main` reset below the tip (the design's AR-4 rewrite, same
HEAD-fallback mechanism); scenario (k) above covers the literal shape and
passes, so nothing is lost, but the RESOLVED text does not say the test
shape changed.

## 6. CI

`gh api repos/thesneakattack/kalshi-whale-poc/commits/575cf096.../status`:
first read (21:49Z) all 12 contexts `pending`; second read 11 `success` +
`ci/woodpecker/pr/tests-pytest-tooling` `pending`; final read at the end of
this review: **`state: success`, 12/12 success, 0 non-success** (both
`pr/` and `push/` variants of `tests-pytest-app`, `tests-pytest-tooling`,
`tests-dependency-audit`, `quality-architecture-audit`,
`quality-browser-e2e`, `kalshi-contract-fixtures`).

## 7. PR body, commits, checklist

Body claims checked against the diff: fast-forward removed (true); notice
with behind/diverged forms (true, text verified live); `-d` → `-D` at the
one call site (true); resolves the open-decisions item (true, `:35`);
`--dry-run` previously deployed too because the ff was not gated on
`DRY_RUN` (true: origin/main `:165-167` has no `DRY_RUN` check); "7
fast-forwards / 3 reloads" (design-stage finding AR-1, not re-derived here).
One claim is **false**: "16/16 pass (10 pre-existing ... plus 6 new)" —
origin/main has 12 tests and HEAD 16, so 12 pre-existing + 4 new (one of the
12 retargeted). Total 16 is right, the split is not (PR-AR-2).

Checklist grep (`gh pr view 544 --json body,commits --jq ... | grep -n
'\[ \]\|\[x\]'`): 4 items, 3 checked, 1 unchecked.
- `[x]` 16/16 pass — verified (baseline run above).
- `[x]` falsified against old `-d` — verified (M3).
- `[x]` coverage list — verified (scenarios a–g, k; M1–M3).
- `[ ]` CI (Woodpecker) — was pending when the body was written; now 12/12
  success. **Not silently assumed**: the item names the exact verification
  command. It must be checked off in the PR (or its result stated in the
  consolidation) before `gh pr merge`; nothing else is left to run on its
  own. No `docs/superpowers/` doc linked from the body carries a checklist
  (`grep -n '\[ \]\|\[x\]'` over the four design-stage docs and the
  self-review: empty).

Commit messages: no checklist items. Labels: `phase:implementing` only
(PR-AR-10).

---

## Findings

**PR-AR-1 — should-fix.** `scripts/cleanup-worktrees.sh:14-17` still says
local `main` "is only fast-forwarded when the primary checkout happens to be
on main itself". The script no longer fast-forwards anything; the design's
§7 item 4 said the header's fast-forward rationale "must not survive the
change" — the fetch-block comment (`:161-175`) was rewritten, this
top-of-file sentence was not. Reword to "local main is never touched by this
script, so it can be arbitrarily stale". *Falsifier:* `sed -n 14,15p` on HEAD
not containing "only fast-forwarded" — it does.

**PR-AR-2 — should-fix.** PR body test-count split is wrong: "10
pre-existing ... plus 6 new" vs. actual 12 + 4 (one retargeted). `gh pr edit
544 --body` one-line fix. *Falsifier:* `grep -c '^def test_'` on
`origin/main:tests/test_cleanup_worktrees.py` returning 10 — it returns 12.

**PR-AR-3 — should-fix (low; acceptable as a named follow-up).** `:213`
`merge-base --is-ancestor "$branch" refs/remotes/origin/main` resolves the
bare name; a tag with the branch's exact name shadows it (gitrevisions
order), the proof passes on the tag, and `-D` then deletes a branch whose
tip is not in origin/main — scenario (j), where the old `-d` refused. `-D`
makes this the only guard, so it should resolve the branch unambiguously:
`refs/heads/$branch`, the same fix the script already applies to the other
side of the comparison at `:208-211`. Precondition is absent today
(`git tag -l` in the real repo is empty) and additionally needs a merged PR
plus an unpushed follow-up commit. *Falsifier:* a same-named tag not
shadowing the branch in `merge-base` — it does (scenario j: bare → tag sha,
`refs/heads/` → branch sha).

**PR-AR-4 — nit.** `:275` cites "docs/open-decisions.md #35" by line number
in a file whose header says lines are removed when done; the number will
drift. Cite by content (issue #535 / the branch-deletion entry).

**PR-AR-5 — nit.** Test-file staleness: `:4` still labels the retargeted
test "4f35ea5 ff-only-failure-must-not-abort"; `:214` docstring reads
present-tense "local main is only fast-forwarded when the primary is on
main"; `:305` cites `:252-253`, the pre-PR line numbers of the "not yet in
main" refusal (now `:287-288`).

**PR-AR-6 — nit.** The PR self-review's "Ordering check" says the fetch was
"moved before the `if [ ... = "main" ]` check". It was already before it
(origin/main `:164` vs `:165`); nothing moved. The conclusion (fetch
precedes the comparison) is right; the description of what changed is not.

**PR-AR-7 — nit / observation.** "Matches `docs/open-decisions.md`
conventions" is true of practice (12 in-place RESOLVED entries) and false of
the file's header rule ("Remove a line when it is done"). Not this PR's
drift; recorded so consolidation does not treat the convention claim as
unqualified.

**PR-AR-8 — observation, no action.** The notice is only reachable with ≥1
branch worktree (`:156-159` exits first). Consistent with its purpose.

**PR-AR-9 — observation, no action.** The suggested deploy command uses
short `origin/main` while the script itself uses `refs/remotes/origin/main`
for the shadowing reason at `:208-211`. For a human-typed command the short
form is fine, and per the design AR's Claim 3 it is the spelling that keeps
human and script fast-forwards distinguishable in the reflog.

**PR-AR-10 — nit (process).** `.claude/rules/branching-and-ci.md` says a PR
carrying a research/design doc under `docs/superpowers/` gets the matching
`phase:*` label(s), and "a PR bundling more than one stage ... gets more than
one label". This PR bundles the research/design note (d12590e) with the
implementation and carries only `phase:implementing`. Add `phase:research`
via `gh pr edit 544 --add-label`.

No blocking finding. Nothing found that makes the shipped script deploy,
move `main`, abort the sweep, or delete a branch whose tip is outside
origin/main under any realistic configuration; the one construction that
deletes unmerged work (PR-AR-3) requires a same-named tag that does not
exist in this repository.

## Recommendation

**GO** on merging PR #544 as-is with respect to behavior: the fast-forward is
gone in both modes, the notice prints the right counts in every shape tried
and never when the primary is not on `main`, `-D` removes the incident's
abort (reproduced in its literal S2 form and the harness's S3 form), the
sole remaining guard refuses an unmerged tip, and all three mutations are
caught by the suite. CI is 12/12 success at `575cf096`.

Before `gh pr merge`, per the fix-list recheck scope (not a new full cycle):
check off the CI checklist item against the 12/12 result; land PR-AR-1 and
PR-AR-2 (one comment line, one PR-body line — neither changes behavior); add
the `phase:research` label (PR-AR-10). PR-AR-3 is the one item with a code
change: recommended as the one-token `refs/heads/` hardening in this PR
(with the suite re-run) or, if consolidation prefers not to widen the PR's
scope after review, as an issue opened before merge so it is not lost.
PR-AR-4/5/6 are nits to fold in if the file is being touched anyway.
