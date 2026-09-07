# Adversarial review — cleanup-worktrees silent-deploy design note

Reviews `2026-09-03-cleanup-worktrees-silent-deploy.md` and its self-review.
Stage 3 of 4. Fresh Agent call, no memory of the authoring session. Stance:
the note is wrong until each load-bearing claim is re-derived from primary
sources. Nothing was taken from the note's tables, quotes, or reproductions.

`scripts/cleanup-worktrees.sh` was **not run** in any form. No git command that
writes to the primary was run. Reproductions live under the session scratchpad
(`adversarial/repro_branch_d.sh`, 12 scenarios S1–S12, git 2.43.0, isolated
`GIT_CONFIG_GLOBAL=/dev/null`); the primary's reflog was read as a file.

## Re-derivations

### Claim 1 — primary is the deploy source; a `.py`-touching ff reloads. CONFIRMED

- `.ddev/docker-compose.fastapi.yaml`: `volumes: "../:/app"`, `working_dir: /app`,
  `command: [uvicorn, main:app, ..., --reload, --reload-exclude, /app/.claude/worktrees, ...]`.
- Live container, `cat /proc/1/cmdline` / `readlink /proc/1/cwd`:
  `/usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude /app/.claude/worktrees --proxy-headers --forwarded-allow-ips=*`, cwd `/app`.
- Installed `uvicorn 0.32.0`, `uvicorn/supervisors/watchfilesreload.py` dumped
  via `inspect.getsource` in the container: `default_includes = ["*.py"]`;
  `self.includes` = defaults not in `reload_excludes` + `reload_includes`; with
  none passed, `["*.py"]`. `--reload-exclude` values that are directories go to
  `exclude_dirs` and are checked with `exclude_dir in path.parents`.
  `WatchFilesReload.__init__` appends `Path.cwd()` to `reload_dirs`.
- Two caveats the note does not state: `tests/**/*.py` is inside `/app` and not
  excluded, so a fast-forward touching only test files also reloads (this
  matters for Claim 3); and `exclude_dirs` is populated only if the path
  `is_dir()` at uvicorn start — true today, out of this design's scope.

### Claim 2 — `git branch -d` falls back to HEAD; removing the ff aborts the sweep. CONFIRMED, mechanism INCOMPLETE, live-shape evidence REFUTED for 3 of 4 named branches

`git help branch` (2.43.0): "must be fully merged in its upstream branch, or in
HEAD if no upstream was set". Synthetic results, all with local `main` stale and
`merge-base --is-ancestor feat/x refs/remotes/origin/main` = yes:

| scenario | upstream config | `refs/remotes/origin/feat/x` | `git branch -d feat/x` |
|---|---|---|---|
| S2 (the note's case) | `origin/feat/x` | absent (pruned) | **refuses**, exit 1 |
| S3 | none | present | **refuses**, exit 1 |
| S1 | `origin/feat/x` | present | deletes, warning "merged to origin/feat/x, not yet merged to HEAD" |
| S4 | `origin/main` | present | deletes, warning as above |
| S5 = S3 after `merge --ff-only refs/remotes/origin/main` | none | present | deletes, exit 0 |
| S6 = S3 with `-D` | none | present | deletes; `feat/x`'s commit still reachable from `origin/main` |
| S8 = `-D` with the worktree still registered | — | — | **refuses** ("used by worktree") |

- S11, a `set -euo pipefail` mini-script mirroring `:227`→`:244`: `worktree remove`
  succeeds, `branch -d` exits 1, script dies with exit 1; `worktree list` shows the
  worktree deregistered and `feat/x` still exists. The note's abort claim holds.
  It understates the blast radius: the remote-branch delete (`:245-247`), the
  `removed:` line, the summary line, and **every later worktree in the sweep**
  are also skipped.
- **Incomplete mechanism:** S3 shows the HEAD fallback fires whenever *no upstream
  is configured*, remote-tracking ref present or not. The note's condition
  ("`refs/remotes/origin/<branch>` is gone") is sufficient, not necessary.
  S3 is exactly `tests/test_cleanup_worktrees.py::_setup`'s shape (`worktree add -b`
  from HEAD, `push` without `-u`).
- **Live-shape evidence refuted:** of the four branches the note names,
  `feat/candlestick-volatility`, `fix/tier0-live-incident-remediation` (and this
  review's own `fix/cleanup-worktrees-silent-deploy`) have
  `branch.<b>.remote=origin`, `branch.<b>.merge=refs/heads/main` — upstream is
  `origin/main`, so `-d` checks against the *correct* ref and passes with a stale
  local `main` (S4). `feat/observability-db-migration` now has a remote ref and
  upstream `origin/feat/observability-db-migration`; `worktree-agent-a0bbb1970e0b725bc`
  is no longer registered. The one branch that *would* hit the HEAD fallback today
  is `feat/persistence-layer-unified-connect` (`remote=<none>`, `merge=<none>`,
  remote ref present — S3), which the note's stated condition does not flag.
  The 2026-08-31 incident in `docs/open-decisions.md:35` (upstream pruned,
  primary not on `main`) remains real and is the S2 shape; SR-5 was right to
  make it the load-bearing evidence.
- **Is `-D` safe given `is_ancestor` at `:194`?** No losing case constructed.
  `is_ancestor` is evaluated against `refs/remotes/origin/main` refreshed by
  `:164`'s fetch (scratch `fetch_rewind.sh`: after rewinding the bare remote by
  one commit, `git fetch origin main` moved `refs/remotes/origin/main` backwards
  to match — the `+refs/heads/*:refs/remotes/origin/*` refspec forces it),
  `gh api .../branches/main/protection` reports `allow_force_pushes:false`,
  `allow_deletions:false`, `enforce_admins:true`, and the only window is a
  remote rewind between `:164` and `:244` within one run.
  Reflog-only commits are equally unprotected by `-d`. Uncommitted work is
  `is_clean`'s job. S8 shows `-D` keeps git's checked-out-elsewhere refusal.
  What `-d` adds is, by upstream config: redundant (upstream=`origin/main`, S4),
  **vacuous** (upstream=`origin/<branch>`: S7 deletes an *unmerged* branch with
  only a warning), or wrong (HEAD fallback with stale HEAD, S2/S3).

### Claim 3 — reflog spelling distinguishes the script. CONFIRMED; the count REFUTED

- S10: `merge --ff-only refs/remotes/origin/main` → `merge refs/remotes/origin/main: Fast-forward`;
  `merge --ff-only origin/main` → `merge origin/main: Fast-forward`;
  `pull --ff-only origin main` → `pull -q --ff-only origin main: Fast-forward`.
- `grep -rn 'refs/remotes/origin/main' scripts tools .claude .woodpecker docs tests`:
  the only `merge` of that spelling in the repo's tooling is
  `scripts/cleanup-worktrees.sh:166` (`ci-skip-*.sh` fetch *into* it in CI; no HEAD reflog).
  A human typing the long form is indistinguishable, so attribution is
  "consistent with the script", not proof — the note's own §2 correction says
  as much.
- `.git/logs/HEAD` parsed as a file, entries dated 2026-09-03 UTC (89 entries,
  01:39:01Z–21:20:20Z). Script-spelled fast-forwards: **7**, not 3 —
  01:39:01, 03:48:42, 07:41:53, 12:12:42, 12:32:14, 16:40:05, 17:15:23.
  17:15:23Z `a5e8e74e→b1b8b49b` matches #535 exactly.
- Which of the 7 touched a `.py` outside `.claude/worktrees/`
  (`git diff --name-only old new`): 03:48:42Z (3, all `tests/`), 16:40:05Z (6),
  17:15:23Z (1). The other four, **including 12:32:14Z in the note's table**,
  touched 0 `.py` and therefore did not reload. "Three deploys" is numerically
  right by coincidence with one wrong member; "fired three times" is wrong.
- Post-note drift: a manual `merge origin/main` at 21:20:20Z moved local `main`
  to `080b17e0`; SR-3's "primary at `cb5d26b`" is already stale. Behind-count now 0.

### Claim 4 — SR-9, `--dry-run` currently fast-forwards. CONFIRMED

`DRY_RUN` is set at `:49-51` and consulted only at `:204` and `:257`. `:153-156`
exit before the fetch only when there are zero branch worktrees. `:164-167` are
unconditional. With ≥1 branch worktree, `--dry-run` runs the fetch and the
ff-merge. `test_dry_run_reports_and_mutates_nothing` cannot detect it: `_setup`
merges `feat/x` into local `main` and pushes, so `main == origin/main` and the
ff is a no-op; the test asserts only that the worktree and branch survive.
Also: the fetch itself mutates `.git` in dry-run (harmless, needed), so
`:38`'s "never mutates anything" is wrong in a second, minor way.

### Claim 5 — census of local-`main` readers. INCOMPLETE; nothing breaks

Grepped `tools/ scripts/ .claude/ .woodpecker/ tests/` for `main..`, `..main`,
`"main"`, `refs/heads/main`, `main_branch`, `symbolic-ref`, `--show-current`,
plus every `git` subprocess naming `main`/`merge-base`/`rev-list`/`log`.

- Named by the note: `tools/quality_coordination.py:112` `_branch_first_commit_at`.
- Missed: `tools/quality_coordination.py:482,486` `delete_merged_branch` and `:503`
  `delete_sdd_scratch` — `merge-base --is-ancestor <branch> main` with
  `main_branch="main"` (local). Dormant: `_CLEANUP_ACTION_FOR_DOMAIN = {}` since
  2026-08-30 (`:568-576`); fail closed (`refused:not-merged`). Degrade, not break.
- Missed, and in the note's favor: `docs/next-action.md:134` verifies a deploy
  with `merge-base --is-ancestor <sha> HEAD` in the primary — it reads local
  `main` *because* that is the deployed tree; option A makes that read more
  meaningful, not less.
- Not readers of local `main`'s position: `.claude/hooks/orient.sh:15,26` and
  `.claude/skills/checkpoint/SKILL.md:10` (branch *name* only);
  `scripts/ci-skip-*.sh` (`CI_COMMIT_BRANCH`, fetch into `refs/remotes/origin/main`);
  `tools/quality_ratchet.py:462` (GitHub compare API); `tools/kanban_sync`
  (name comparisons); `tools/project_manifest.py` (HEAD, i.e. the deployed tree).
- `.claude/hooks/guard_workflow.py`: no `main` reference beyond `def main`.
- No consumer breaks. Option C stays out of contention.

### Claim 6 — honesty of the option rejections. CONFIRMED, one unnamed option

The four options are #535's own list, so evaluating all four is the assignment,
not a strawman violation. B still deploys — rejection stands. C's "strictly more
code for less separation" is a value judgment but the facts under it (redundant
with a one-line manual merge; still needs `-D`) hold. D's coupling to
`FileFilter.includes` is real (source above), and S12 adds a reason the note
missed: `merge --ff-only` already fails silently under `|| true` whenever the
pull touches a locally modified tracked file — the primary has `M config/settings.yaml`
right now — so the "courtesy" was never reliable.

Unnamed option G: keep the courtesy only where it is free. `git fetch origin main:main`
refuses whenever `main` is checked out in the primary *or any worktree* (S9:
`fatal: refusing to fetch into branch 'refs/heads/main' checked out at ...`),
and never touches a working tree. `git -C "$PRIMARY" fetch --quiet origin main:main 2>/dev/null || true`
is therefore self-gating: it advances local `main` in exactly the 82834dc case
(primary parked elsewhere) and is a no-op otherwise. One line, no deploy path.
It reintroduces "sometimes current" (deterministic on the primary's branch, not
on diff content), which the note used against D. Given §4's readers are dormant
or advisory, A alone is acceptable; G is a should-consider, not a defect.

### Claim 7 — `git fetch origin main --quiet` has no waved-away cost. CONFIRMED (deploy), nit (poll)

Fetch writes only under `.git/` (`FETCH_HEAD`, `refs/remotes/origin/main`,
objects). Nothing matches `*.py`; no reload. Poll cost: uvicorn passes
`watch_filter=None`, so watchfiles 1.2.0's Python-side `DefaultFilter` — whose
`ignore_dirs`, read in the container, is `['.git', '.hg', '.hypothesis', '.idea',
'.mypy_cache', '.pytest_cache', '.svn', '.tox', '.venv', '__pycache__', 'node_modules']`
— is not in play, and the force-polling walk stats `.git` too
(`docs/reload-watcher-polling-research-2026-09-03.md:169,183`). The primary's
`.git` holds 5,607 files (5,315 under `objects/`) against 2,019 app files; one
fetch adds a handful. Pre-existing, unchanged by the design, negligible. One
sentence in the note would close it.

### Claim 8 — the test list catches what it claims. PARTLY REFUTED

`tests/test_cleanup_worktrees.py` read in full.

- `test_dry_run_reports_and_mutates_nothing`: cannot catch SR-9 (see Claim 4).
  The new dry-run assertion must advance `origin/main` from a clone *and* assert
  `rev-parse main` unchanged.
- `test_diverged_local_main_does_not_abort_the_sweep`: passes trivially after A.
  Retargeting is fine, but see AR-5 — the notice code is the new thing that can
  misbehave on divergence.
- `test_stale_local_main_does_not_hide_a_branch_merged_into_origin_main`: primary on
  `other`, which contains `feat/x`; `-d`'s HEAD fallback passes. Unaffected, as the note says.
- Note's new test 3 is mis-specified in two ways. (a) In `_setup`, `feat/x` has
  no upstream, so the HEAD fallback fires with the remote ref *present* (S3);
  deleting the ref is unnecessary and the docstring would name the wrong
  mechanism. (b) `_setup` merges `feat/x` into local `main` before pushing, so
  local `main` already contains the tip; "origin ahead" alone does not make
  `-d` refuse. The test must construct "local HEAD lacks the branch tip" (reset
  `main` to `initial` while on `main`, then fetch). Written literally as
  described, it might pass against `-d` and not be the falsifier the note claims.
- **Missing entirely:** no test exercises `:252-253`'s `is_ancestor=0` refusal
  ("branch has commits not yet in main"); `grep -rn 'not yet in main' tests/`
  is empty. With `-D` that check is the sole guard between a `gh`-reported
  MERGED PR and deleting unmerged commits. Its absence is tolerable today only
  because `-d` sometimes duplicates it.

### Self-review spot checks

SR-1 CONFIRMED (S9). SR-2 CONFIRMED: `.claude/skills/checkpoint/SKILL.md:83`,
`.claude/hooks/orient.sh:40`, `.claude/rules/branching-and-ci.md:86` — none
mentions local `main`; neither does `docs/archive/lane-9-tooling-ci-process-governance/plans/2026-08-27-workflow-remediation.md:283`.
SR-4 CONFIRMED and then some (7, not 3 — see Claim 3). SR-5 CONFIRMED as far
as it goes: `gh pr list --head <b> --state all` returns `[]` for
`feat/candlestick-volatility`, `fix/tier0-live-incident-remediation`, and
`feat/persistence-layer-unified-connect`, and `#540 OPEN` for
`feat/observability-db-migration` — none is MERGED, so none reaches `:244`
today; but the upstream-config finding in Claim 2 is the stronger correction
(three of them would pass `-d` even if they did). SR-7 CONFIRMED: the only new quantity is a commit count; the
`--left-right` form in AR-5 adds a second count of the same dimension. SR-8
CONFIRMED: nothing in the change touches trading, risk, sizing, calibration,
settlement, auth, kill switch, `data/*.db`, or `trading_enabled`.

## Findings

**AR-1 — should-fix.** §2 conflates "fast-forwarded" with "deployed". The day's
reflog shows 7 script-spelled fast-forwards; 3 touched a `.py` and reloaded
(03:48:42Z, 16:40:05Z, 17:15:23Z); 12:32:14Z, in the note's table as a deploy,
touched none. Restate as "7 fast-forwards, 3 reloads", drop 12:32:14Z from the
deploy column, add 03:48:42Z. *Falsifier:* `git diff --name-only 84ce051b 9754e4b4 | grep '\.py$'` non-empty.

**AR-2 — should-fix.** §3's mechanism omits the no-upstream case (S3) and its
"4 of 12 live" evidence is wrong for 3 of the 4: their upstream is `origin/main`,
so `-d` would pass. Name the two triggers (no upstream; upstream ref absent),
name `feat/persistence-layer-unified-connect` as today's live S3 shape, and keep
open-decisions #35 as the load-bearing incident. *Falsifier:* `git config branch.feat/candlestick-volatility.merge` ≠ `refs/heads/main`, or S3 succeeding.

**AR-3 — should-fix (gate on the implementation PR).** Add a test for the
`is_ancestor=0` refusal: `gh` says MERGED, tip has a commit not in
`refs/remotes/origin/main`, worktree clean → kept, branch survives, exit 0.
With `-D` this is the only guard; a regression to bare `origin/main` (the
4f35ea5 shadow bug) or a dropped check would otherwise delete unmerged work
with no test noticing. *Falsifier:* such a test already exists — none found.

**AR-4 — should-fix.** §8 test 3's precondition must be "local HEAD does not
contain the branch tip" (constructed explicitly), not "remote ref missing";
the docstring must name the no-upstream mechanism. *Falsifier:* the test as
literally described fails against the current script with the ff removed —
it will not unless local `main` is reset below the merge.

**AR-5 — should-fix.** §7 item 2's notice prints "N behind" from
`rev-list --count main..refs/remotes/origin/main` and "the exact command"
(`merge --ff-only`). On a diverged `main` the count is non-zero and the
command fails; on a dirty tracked file the pull touches (S12; the primary has
`M config/settings.yaml` now) it also fails. Use
`rev-list --left-right --count main...refs/remotes/origin/main`, print
"diverged (N ahead)" when ahead > 0, and say the ff can refuse on local
modifications. The retargeted diverged test should assert this. *Falsifier:* none needed — design detail.

**AR-6 — nit.** §4 census misses `delete_merged_branch`/`delete_sdd_scratch`
(dormant, fail closed) and `docs/next-action.md:134` (a deliberate local-`main`
reader that A improves). Add both; conclusion unchanged.

**AR-7 — nit.** SR-6 frames `-d` as an "independent second guard" being traded
away. S7 shows it deletes an *unmerged* branch when upstream is its own
`origin/<branch>`, with only a warning. The honest statement — and the one to
put in the script comment and next to `tools/quality_coordination.py:477` — is
that `-d`'s check is redundant, vacuous, or wrong depending on upstream config,
never independent of it.

**AR-8 — nit.** Option G (self-gating `fetch origin main:main || true`) was not
named. A remains acceptable; if §4's readers ever matter, G is the one-line answer.

**AR-9 — nit.** `:38`'s "never mutates anything" should become "never touches
the working tree, a branch, or a worktree"; the fetch mutates `.git` by design.
One sentence on the fetch's negligible poll-set cost closes Claim 7.

**AR-10 — nit.** §3 understates the abort: the remote-branch delete, the summary
line, and every later worktree in the sweep are skipped too.

**AR-11 — nit.** SR-3 pins `cb5d26b`; already `080b17e0`. State "0 behind at
review time" without a SHA.

## Recommendation

**GO** on the design as written — remove the ff, print a behind-notice, `-d` → `-D`
with the `is_ancestor` justification. Every check that could have overturned it
(HEAD fallback real: S2/S3/S11; ff removal would ship the abort: S11; `-D` safe
under `is_ancestor`: S6/S8 plus no losing construction; reload mechanism: source
and `/proc/1`) came back confirming it. What is wrong is evidence presentation
(AR-1, AR-2), test-spec precision (AR-3, AR-4), and one notice edge case (AR-5).
Fix AR-1 through AR-5 in the note before the implementation stage; AR-3 and
AR-4 are gates on the implementation PR's test list. No blocking finding.
