# cleanup-worktrees.sh performs an unannounced deploy — research and design note

Issue: #535. Date: 2026-09-03. Stage: research/design (stage 1 of the
"nothing advances on one pass" pipeline). Author: `autotrade` worker session
(research role). Scope: decide *which* of the four options named in #535 is
right, and why; the implementation follows in a separate PR carrying its own
review cycle.

`scripts/cleanup-worktrees.sh` was **not run** during this investigation. Every
git-behavior claim below was reproduced in a throwaway synthetic repository
under the session scratchpad, never against the live repo.

**Review status: self-reviewed, independently adversarially reviewed (fresh
memory-less agent, own separate synthetic reproductions), consolidated — GO.**
The adversarial pass confirmed every mechanism claim and the recommendation
unchanged; it corrected the deploy count/table, the live-branch evidence in
§3, and two under-specified tests, all folded in below. Full trail:
`2026-09-03-cleanup-worktrees-silent-deploy-self-review.md`,
`…-adversarial-review.md`, `…-consolidation.md` in this directory.

---

## 1. The question

`scripts/cleanup-worktrees.sh:164-167` fast-forwards the primary checkout's
local `main` whenever `main` is what the primary has checked out:

```bash
git -C "$PRIMARY" fetch origin main --quiet
if [ "$(git -C "$PRIMARY" symbolic-ref --short HEAD 2>/dev/null || echo "")" = "main" ]; then
  git -C "$PRIMARY" merge --ff-only refs/remotes/origin/main --quiet || true
fi
```

Its own comment calls this "a courtesy for anything else that reads local main;
nothing downstream depends on it." #535 establishes that the primary is the live
app's bind mount, so this "courtesy" is a production deploy. The question this
note answers: **remove it, make it loud, gate it behind a flag, or make it
conditional on the pull being reload-inert?**

## 2. Verified mechanism

**The primary checkout is the deploy source.** `.ddev/docker-compose.fastapi.yaml`
mounts `"../:/app"` and runs
`uvicorn main:app --reload --reload-exclude /app/.claude/worktrees`. Read from
the file, not recalled.

**The watcher fires on `.py` and nothing else.** Read from the installed source
in the running container (`uvicorn 0.32.0`,
`uvicorn/supervisors/watchfilesreload.py`): `FileFilter.__init__` sets
`default_includes = ["*.py"]`, and with no `--reload-include` passed here,
`self.includes` is exactly `["*.py"]`. `WatchFilesReload` adds `Path.cwd()`
(`/app`) to `reload_dirs`. `--reload-exclude /app/.claude/worktrees` populates
`exclude_dirs`, which is why a worktree's own `.py` edits do **not** reload the
shared process but the primary's do — and note `tests/**/*.py` is *not*
excluded, so a fast-forward touching only test files also reloads.

So: a `git merge --ff-only` in the primary that touches any `.py` outside
`.claude/worktrees/` replaces the live worker. This is the whole mechanism.

**It fast-forwarded local `main` 7 times today from this script; 3 of those
touched a `.py` file and reloaded.** The primary's reflog (`.git/logs/HEAD`,
read directly) distinguishes the script's fast-forwards from a human's by ref
spelling — the script merges the fully-qualified `refs/remotes/origin/main`,
a human types the short `origin/main` — though this attribution is
"consistent with the script," not proof: nothing stops a person from typing
the long form too.

| UTC | reflog message | caller (by spelling) | touched a `.py`? |
|---|---|---|---|
| 01:39:01 | `merge refs/remotes/origin/main: Fast-forward` | script | no |
| 03:48:42 | `merge refs/remotes/origin/main: Fast-forward` | script | **yes (3, all `tests/`) — reloaded** |
| 07:41:53 | `merge refs/remotes/origin/main: Fast-forward` | script | no |
| 12:12:42 | `merge refs/remotes/origin/main: Fast-forward` | script | no |
| 12:32:14 | `merge refs/remotes/origin/main: Fast-forward` | script | no |
| 16:40:05 | `merge refs/remotes/origin/main: Fast-forward` | script | **yes (6) — reloaded** |
| **17:15:23** | `merge refs/remotes/origin/main: Fast-forward` | **script — the incident in #535** | **yes (1) — reloaded** |

17:15:23Z matches #535's reported timestamp exactly, one second before the
`WatchFiles detected changes in 'services/whale_stream/whale_stream_handlers.py'`
line it cites. The script fast-forwarded local `main` far more often than #535
implies — 7 times before the issue was filed — but most were no-ops for the
watcher; 3 actually reloaded the app.

**Correction to #535.** The issue says the effect "is indistinguishable from an
intentional one in the reflog." That overstates it: the ref-spelling difference
above does separate the two callers, in this repo's actual history. What's true
and still the point: nothing *announces* a reload at the time it happens, and
reading the distinction requires already knowing the script's exact ref
spelling and cross-referencing which fast-forwards touched a `.py` — exactly
the reconstruction #535 describes doing by hand. The fix should make that
reconstruction unnecessary, not rely on it.

## 3. Finding that changes the answer: the fast-forward is **not** a courtesy

The script's comment claims nothing downstream depends on it. That is false.
`git branch -d "$branch"` at `:244` depends on it.

`git help branch` (2.43.0): a branch "must be fully merged in its upstream
branch, or in HEAD if no upstream was set." **Two independent triggers** put
the check on HEAD instead of the correct ref: no upstream configured for the
branch at all (regardless of whether the remote-tracking ref exists), or an
upstream configured but its remote-tracking ref gone. In the primary, HEAD *is*
local `main`. So when either trigger fires and local `main` is behind,
`git branch -d` refuses — and under `set -euo pipefail` that aborts the entire
sweep, **after** `git worktree remove` already ran, also skipping the
remote-branch delete, the `removed:` line, the summary line, and every later
worktree in the sweep — leaving a removed worktree with a dangling branch, mid-run.

Reproduced both directions in a synthetic repo (git 2.43.0), same commit graph,
only local `main` differing:

```
local main stale, no upstream configured for feat/x (remote ref still present):
  $ git branch -d feat/x
  error: the branch 'feat/x' is not fully merged.   exit=1

after git merge --ff-only refs/remotes/origin/main:
  $ git branch -d feat/x
  Deleted branch feat/x (was 025b34e).              exit=0
```

**This shape is live today.** Checked `branch.<name>.merge` for each of the 12
worktree branches currently registered in the primary: most have upstream
`origin/main` or `origin/<own-name>`, so `-d` checks the *correct* ref and a
stale local `main` doesn't matter for them. The one branch that matches the
no-upstream trigger right now is `feat/persistence-layer-unified-connect`. The
load-bearing evidence for "this is not hypothetical" is
`docs/open-decisions.md` item #35, which records this exact abort hitting live
on 2026-08-31 (upstream pruned, primary not on `main`), worked around by hand
with `git branch -D`.

**Consequence: any option that removes or gates the fast-forward must fix
`git branch -d` in the same change, or it silently ships that abort.** This is
the single most important constraint on the decision, and it is absent from
#535's option list.

## 4. Other readers of local `main`

`tools/quality_coordination.py`'s `_branch_first_commit_at()` (`:107-116`) runs
`git log main..<branch>` as a branch-creation-time proxy, defaulting
`main_branch="main"` (local). A stale local `main` makes `main..branch` include
commits already in `origin/main`, yielding an artificially *old* creation time,
which perturbs `_cluster_siblings()`'s sibling-window heuristic.

The same module's `delete_merged_branch()` (`:474-486`) and `delete_sdd_scratch()`
(`:493-503`) also run `merge-base --is-ancestor <branch> main` against local
`main`. Both are dormant: `_CLEANUP_ACTION_FOR_DOMAIN` has been an empty dict
since 2026-08-30 (open-decisions), so neither is auto-selected, and both fail
closed (`refused:not-merged`) rather than deleting anything wrongly.

One reader is *improved* by this change, not degraded: `docs/next-action.md`'s
own deploy-verification procedure checks `merge-base --is-ancestor <merge sha>
HEAD` in the primary specifically because local `main` is the deployed tree —
keeping that read meaningful is part of why routine hygiene should not be
allowed to move it out from under a deploy-verification step in progress.

Honest weight: the clustering and dormant-cleanup readers are soft
degradations of advisory tooling, not breaks. Neither is a reason to keep
deploying from a cleanup script, but both should be named rather than
discovered later. Both are also *already* degraded today whenever the primary
is not on `main`, since the fast-forward is conditional on that.

## 5. The crux

There is no way to advance local `main` while `main` is the primary's
checked-out branch without rewriting the bind mount. `git fetch origin main:main`
refuses on a checked-out branch; `git update-ref` would desync index and working
tree. **"Keep local main current" and "deploy the live app" are the same
operation here.** Every option is therefore a position on one question: should
routine worktree hygiene deploy?

It should not:

- Cleanup is run by any session after any merge (`.claude/skills/checkpoint/SKILL.md`
  step 9), on no schedule.
- Deploy has a named owner precisely so it can be kept out of measurement
  windows (`docs/next-action.md`).
- The script's own correctness does not need it: all three staleness checks read
  `refs/remotes/origin/main`, which the unconditional `fetch` refreshes.

## 6. Options

| Option | Verdict |
|---|---|
| **A. Remove the fast-forward** | **Chosen**, with §7's additions. Simplest; separates the two jobs; the script keeps working because its checks use `origin/main`. |
| **B. Keep it, print loudly** | Rejected. Announcing a deploy does not stop it landing inside another session's measurement window, which is the actual harm. |
| **C. Gate behind `--allow-deploy`** | Rejected. Redundant capability: the deploy owner already deploys with one command, `git -C <primary> merge --ff-only origin/main`. A deploy flag on a worktree-cleanup script re-couples the two jobs this fix exists to separate, and the default-off path still needs §3's `branch -d` fix, so it is strictly more code for less separation. |
| **D. Skip when the pull touches a `.py`** | Rejected. Most complex, and its correctness is a function of `FileFilter.includes`, which changes if anyone ever passes `--reload-include`. It also makes local `main`'s freshness nondeterministic — sometimes current, sometimes arbitrarily behind — which is the 2026-08-28 "65 commits behind, every merged branch read as unmerged" bug class (`82834dc`) reintroduced by a different route. |

There is a fifth, unnamed option worth recording even though it is not
adopted: **G. self-gating fetch-into-local-branch** —
`git -C "$PRIMARY" fetch --quiet origin main:main 2>/dev/null || true`. Git
refuses this whenever `main` is checked out anywhere in the repo (reproduced:
`fatal: refusing to fetch into branch 'refs/heads/main' checked out at ...`),
so it never touches a working tree and never risks a reload; it advances local
`main` only in the 82834dc case (primary parked on some other branch) and is a
harmless no-op otherwise. It reintroduces "sometimes current, sometimes not,"
which was used against D above — but deterministically on the primary's
checked-out branch rather than on diff content, which is a materially
different (and cheaper to reason about) kind of nondeterminism. Given §4's
readers are dormant or advisory, A alone is judged sufficient and simpler; G is
recorded as a reasonable alternative for if that changes, not adopted here.

Additional evidence the "courtesy" was never fully reliable, found while
testing the options: `git merge --ff-only` under `|| true` already fails
silently whenever the pull would touch a file with local uncommitted
modifications in the primary — true right now (`config/settings.yaml` is
locally modified) — so the fast-forward has been silently skipping some
fraction of runs all along, on top of everything else wrong with it.

On the coordinator's stated preference — the simplest fix that does not
*silently* regress the courtesy: A alone would regress it silently, which is why
it is chosen only together with §7's notice. The notice is what makes the
removal non-silent; it is not decoration.

## 7. Design

1. **Delete the `merge --ff-only`.** Keep `git fetch origin main --quiet`
   unchanged — it refreshes `refs/remotes/origin/main`, which the staleness
   checks read, and it writes only to `.git`, matching no `*.py` include.
2. **Replace it with a behind-notice**, using
   `git rev-list --left-right --count main...refs/remotes/origin/main` (triple-dot,
   both counts) rather than the simpler `main..origin/main` — the two-count form
   also reports when local `main` has diverged (commits on both sides), which a
   one-sided "N behind" would silently mis-describe as "0 behind" or hide
   entirely. When the primary is on `main` and either count is non-zero, print:
   the behind count (and, if ahead > 0, "diverged (N ahead)" instead of a plain
   behind-count), that the script deliberately does not pull because the primary
   is the live app's bind mount and that pull *is* a deploy that fires
   `uvicorn --reload`, and the suggested command to run when a deploy is
   intended — noting in the message itself that the command can still refuse
   (diverged history needs a real merge, not `--ff-only`; a locally-modified
   tracked file the pull would touch makes even a clean fast-forward no-op
   under `|| true`, per §6's fifth finding) so the notice doesn't imply a
   guarantee it can't back. Silent when already current — a no-op should stay
   quiet.
3. **`git branch -d` → `git branch -D`**, with the justification in a comment
   that states the tradeoff honestly: `-d`'s own merge-check is not an
   independent safety net here — depending on the branch's upstream
   configuration it is redundant with, vacuous next to, or actively wrong
   compared to, the script's own `merge-base --is-ancestor "$branch"
   refs/remotes/origin/main` at `:194` (a precondition of ever reaching
   `:244`, checked against the authoritative fetched ref rather than whatever
   the primary's HEAD happens to be). This is a deliberate divergence from
   `tools/quality_coordination.py:474`'s `delete_merged_branch()`, which keeps
   `-d` specifically because *it* has no ancestry proof of its own — the
   comment should say so, so a future session doesn't "fix" this script back
   to `-d` believing it duplicates a check quality_coordination.py can't
   already make. This resolves `docs/open-decisions.md` item #35, which parks
   exactly this change. CLAUDE.md requires acting on an open-decisions line
   rather than re-planning it, so it belongs in this PR, not a follow-up.
4. **Update the script's header comment** — the "courtesy … nothing downstream
   depends on it" sentence is now doubly wrong (it deployed, and `branch -d` did
   depend on it) and must not survive the change.
5. **Update `docs/open-decisions.md`** to mark #35 resolved by this PR. Update
   the `--dry-run` usage line at `:38` from "report only, never mutates
   anything" to "never touches the working tree, a branch, or a worktree" —
   the unconditional `fetch` still writes `.git/FETCH_HEAD` and
   `refs/remotes/origin/main` in dry-run mode, which is intended and harmless,
   but "never mutates anything" was already inaccurate before this change.
   Check `.claude/skills/checkpoint/SKILL.md` step 9's one-line description
   for the same implication and correct it if present (verify at
   implementation time; do not assume).

## 8. Tests (`tests/test_cleanup_worktrees.py`)

Existing coverage that must keep passing, and what it means after the change:

- `test_stale_local_main_does_not_hide_a_branch_merged_into_origin_main`
  (`82834dc`) — unaffected; it exercises the primary *not* on `main`.
- `test_diverged_local_main_does_not_abort_the_sweep` (`4f35ea5`) — this test
  guards the `|| true` on a merge that no longer exists. It must not simply be
  deleted: retarget it so it still asserts the sweep completes with a diverged
  local `main`, which is now guaranteed structurally rather than by `|| true`.

New:

1. **The script does not advance local `main`.** Primary on `main`, behind
   `origin/main`; assert `rev-parse main` is unchanged after the run. This is
   the regression test for the deploy itself. Cover both plain invocation and
   `--dry-run` explicitly — the existing dry-run test's `_setup()` merges
   `feat/x` into local `main` before pushing, so `main == origin/main` there
   and it cannot detect a fast-forward either way; this new case needs
   `origin/main` genuinely ahead (e.g. push an extra commit from a second
   clone) to be a real assertion.
2. **The behind-notice is printed**, naming the commit count and the
   suggested command; is **absent** when local `main` is already current; and
   reports "diverged (N ahead)" rather than a plain behind-count when local
   `main` has commits `origin/main` doesn't.
3. **A branch with no upstream configured, whose local `main` doesn't yet
   contain its tip, still gets deleted and the sweep completes.** Construct the
   actual failing precondition directly rather than only removing the remote
   ref: after merging `feat/x` into local `main` (as `_setup()` already does)
   and pushing, reset local `main` back to its pre-merge commit while `main` is
   checked out, so `git branch -d feat/x` hits the no-upstream-HEAD-fallback
   path with an outdated HEAD. Assert: worktree gone, branch gone, exit 0. This
   is the §3 regression the `-D` change prevents; it must fail against the
   script with only the fast-forward removed and `-d` left as `-d` — that
   failure is the falsifier for this whole note.
4. **The `is_ancestor=0` refusal path still refuses under `-D`.** `gh` reports
   a branch's PR as MERGED, but the branch's actual tip has a commit not
   reachable from `refs/remotes/origin/main` (construct directly: commit on the
   worktree branch after the point the mock PR data claims was merged). Assert
   the branch and worktree both survive and the run reports it as kept
   ("branch has commits not yet in main"), exit 0. No such test exists today
   (`grep -rn 'not yet in main' tests/` is empty); under `-D` this check becomes
   the *only* guard between a stale/wrong `gh` answer and deleting unmerged
   commits, so it needs its own direct coverage rather than relying on `-d` to
   have been quietly backstopping it.

## 9. What would falsify this

- If `git branch -d` did **not** fall back to HEAD when the remote-tracking ref
  is absent, §3 collapses and option A needs no `-D` change. Reproduced above;
  re-runnable in any scratch repo in under a minute.
- If some consumer read local `main` in a way that *breaks* rather than
  degrades, option C would come back into contention. §4 is the full census of
  local-`main` readers found by grepping `tools/`, `scripts/`, `.claude/hooks/`;
  it is not a claim about every possible human habit.
- If uvicorn here ever gains a `--reload-include`, option D's rejection reason
  changes, but A is unaffected.

## 10. Out of scope

- Issue #539 and tonight's other `capture_writer`/deploy-adjacent findings are
  unrelated to this change; they share an evening, not a cause.
- Whether the primary should be parked on `main` at all is a deploy-model
  decision with a named owner, not this PR's to make.
- `tools/quality_coordination.py`'s use of local `main` (§4) is left as-is;
  changing it is a separate, independently-reviewable call.
