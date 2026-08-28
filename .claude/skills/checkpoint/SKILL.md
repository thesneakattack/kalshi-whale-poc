---
name: checkpoint
description: This skill should be used at natural breakpoints in a long working session on autotrade — after a verified, test-passing chunk of work, before starting a materially different task, or when the user asks to "checkpoint", "save progress", "commit and push", or "offload testing to CI". Runs the test suite, commits at a clean point, pushes to trigger Woodpecker CI, and flags whether it's a good moment to /compact or /clear.
---

# Checkpoint (verify, commit, push, offload CI)

Operationalizes CLAUDE.md's "Long-session workflow" directive: don't let
verified work sit uncommitted, and use Woodpecker CI (2026-08-24, replaced
GitHub Actions as the authoritative push/PR gate - see
`.claude/skills/ci-cd-guardrails/SKILL.md`'s "Local vs CI verification
policy") as a second, clean-environment test run on top of (not instead
of) the local per-edit hook. See `run_tests.py` / `session_orient.sh` /
`post_compact_reorient.sh` in `.claude/hooks/`, `.woodpecker/*.yml`, and
`scripts/woodpecker-status` for the pieces this ties together.

## Steps

1. **Branch check.** `git branch --show-current` — per
   `.claude/rules/branching-and-ci.md`, normal implementation work doesn't
   commit directly to `main`. Already on an initiative branch → continue.
   On `main` with real implementation work about to be committed → stop
   and create/switch to one (`git checkout -b <feat|fix|refactor|chore|
   docs>/<name>`) before proceeding to step 3. A checkpoint that's purely
   about docs/rules/skills housekeeping still follows this — see that rule
   file for the full branch-naming and lifecycle policy; this skill only
   covers the verify/commit/push/confirm-CI mechanics within it.

2. **Do not run the suite locally.** CI is the only full-suite owner; the
   per-edit hook already ran the edited module's own tests. Run one file
   locally only while actively debugging a specific failure.

3. **Review scope.** `git status` and `git diff --stat` — confirm nothing
   unexpected is about to be staged (a stray `data/*.db`, `.env`, a session
   scratch file, or unrelated work-in-progress from earlier in the session
   that isn't actually verified yet). Stage specific paths only — never
   `git add -A` / `git add .`.

4. **Commit.** Write the message around *why*, in this repo's existing
   terse, direct style (`git log` for tone/examples — e.g. "Fix X", "Ship
   Y", "Cross-reference Z against W"). One commit per logical unit of
   work — if the session covered several unrelated things, split it into
   separate commits rather than one bundled one.

5. **Push.** `git push` on the current branch (the initiative branch from
   step 1, not `main`). This repo is already activated in the shared
   Woodpecker instance (`portfolio/ci-cd/` - confirmed live 2026-08-25 via
   real GitHub commit statuses and pipeline numbers in the high 20s, not
   just the setup docs' claim), so this triggers every `.woodpecker/*.yml`
   workflow whose `when:` matches the push automatically - branch pushes
   included, per `.claude/rules/branching-and-ci.md`.
   `.github/workflows/tests.yml`/`quality.yml` are `workflow_dispatch`-only
   now and do NOT run automatically on push - don't wait on them here.

6. **Confirm CI and interpret the result — this is the real gate when
   step 2 skipped the local run. Actually run this step; do not assume
   green and do not substitute a local full-suite re-run instead** (found
   live 2026-08-25: `quality-architecture-audit` sat red across three real
   pushes - `d644034`, `21a303a`, `b28a380` - unnoticed, because this step
   was never actually exercised in that stretch of work).

   Zero-setup check, no personal `WOODPECKER_TOKEN` needed - Woodpecker
   posts a commit status back to GitHub per workflow, and `gh` is already
   authenticated in every Claude session:
   ```bash
   gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status
   ```
   Read each entry's `context` (`ci/woodpecker/push/<workflow-name>`) and
   `state` independently, the same way the former per-job GitHub Actions
   checks were read individually rather than off one aggregate status - a
   `failure` on any single context means the change is NOT verified yet,
   regardless of the others being green:
   - `tests-pytest` must succeed. On failure, pull the failing step's log
     (`scripts/woodpecker-status --pipeline N --log STEP`, which needs a
     personal `WOODPECKER_TOKEN` - see that script's own header - or the
     failing status's own `target_url`, the Woodpecker web UI), fix it the
     same way any bug gets fixed, verify locally to close the loop fast
     rather than round-tripping CI again for the same fix, then commit +
     push the fix — never leave `main`'s `tests-pytest` workflow red.
   - `tests-dependency-audit` should show **zero known vulnerabilities**
     (fixed 2026-08-23 — `ROADMAP.md`'s "Path to production" section,
     checked off; confirmed live 2026-08-24 running this exact pipeline via
     `woodpecker-cli exec`: "No known vulnerabilities found"). If it shows
     a new failure, that's a fresh regression to investigate, not
     pre-triaged debt — earlier versions of this skill said otherwise
     (20 CVEs, deliberately left unfixed); that was true when written but
     is stale now that the fix shipped.
   - `quality-architecture-audit` failing often means a structural-drift
     check, not a scanner finding — check `static/project-manifest.json`
     first (`python -m tools.project_manifest --check
     static/project-manifest.json --repo-root .`; regenerate with
     `--write` in place of `--check` if it reports stale) before assuming
     a real new `tools.quality_audit` finding.
   If `gh api` itself is unavailable (rare - it's the same `gh` used for
   PRs), fall back to `scripts/woodpecker-status`/the Woodpecker web UI; if
   neither is reachable, say so rather than silently skipping this step.

7. **Roadmap sync check.** If this checkpoint closes out a `ROADMAP.md`
   item, run `/close-roadmap-item` now, before moving on — cheap, and keeps
   the checklist from quietly drifting out of sync with what's actually
   shipped.

8. **Kanban board sync (mechanical sources only).** Run the fast,
   fully-deterministic slice of the board sync — worktrees, `ROADMAP.md`,
   `active-tracks-board.md` — so the GitHub Issues board doesn't drift too
   far behind a normal working session:
   ```bash
   python -m tools.kanban_sync sync --sources worktree,roadmap,track
   ```
   This does not run the judgment-assisted plan-doc classification step —
   that's the standalone `kanban-board-sync` skill, run on demand, since
   it's slower and shouldn't gate every checkpoint. If `gh` is missing the
   `project` scope, this prints an actionable error and exits nonzero —
   don't treat that as a checkpoint failure, just note it and move on;
   getting that scope added is a one-time human action
   (`gh auth refresh -s project`), not something to fix mid-checkpoint.

9. **PR check — only when this checkpoint completes the initiative, not
   every mid-initiative checkpoint.** If the branch has no open PR yet and
   the initiative this branch covers is actually done, `gh pr create`; if
   CI (step 6) is green and the diff has been reviewed, `gh pr merge
   --merge` (preserves individual commits) then delete the branch — see
   `.claude/rules/branching-and-ci.md`'s "Integration lifecycle" for the
   full policy. A checkpoint in the middle of a multi-task initiative just
   leaves the branch pushed and green; it doesn't open or merge a PR yet.
   Right after a merge, run `scripts/cleanup-worktrees.sh` (no flags) to
   sweep every registered worktree, not just the one just merged — it
   only ever removes a worktree/branch whose PR is confirmed `MERGED` via
   `gh` *and* whose tip is a confirmed ancestor of `main`, with a clean
   working tree; anything short of that (open/closed-without-merge PR,
   uncommitted changes) is left alone and reported, never forced.

10. **Session-hygiene prompt.** Give a short 2-3 sentence summary of what
    this checkpoint covered (keeps continuity across a later `/compact`).
    Then suggest — don't insist — whichever fits: `/compact` if context is
    getting heavy after a substantial chunk of work, `/clear` if the next
    task is materially unrelated to what was just finished.

## Scope note

This assumes CLAUDE.md's standing instruction authorizing proactive
commit/push at verified checkpoints (dated 2026-08-16) — it doesn't
re-litigate whether that's OK, only when/how. It never commits a failing
or half-finished state, and it doesn't decide what counts as "done" — that
judgment happens first, same boundary `/close-roadmap-item` already draws
for itself.
