---
name: checkpoint
description: This skill should be used at natural breakpoints in a long working session on autotrade — after a verified chunk of work, before starting a materially different task, or when the user asks to "checkpoint", "save progress", "commit and push", or "offload testing to CI". Commits at a clean point, pushes to trigger Woodpecker CI, confirms the real CI result, runs the repo's own workflow tools, and flags whether it's a good moment to /compact or /clear.
---

# Checkpoint (verify, commit, push, confirm CI)

## Steps

1. **Branch check.** `git branch --show-current` — on `main` with
   implementation work pending, stop and create an initiative branch first
   (`.claude/rules/branching-and-ci.md`).

2. **Verify.** The per-edit hook already ran each edited module's tests. Run
   whatever else the change warrants — the full suite locally is fine when the
   change is broad or risky; CI runs it regardless.

3. **Review scope.** `git status` and `git diff --stat`: nothing unexpected
   staged (a stray `data/*.db`, `.env`, session scratch, unverified
   work-in-progress). Stage specific paths only — never `git add -A` /
   `git add .`.

4. **Commit.** Message around *why*, in this repo's terse style (`git log` for
   tone). One commit per logical unit.

5. **Push.** `git push` on the current branch. Woodpecker runs every
   `.woodpecker/*.yml` whose `when:` matches; `.github/workflows/*` are
   `workflow_dispatch`-only fallbacks and do not run on push.

6. **Confirm CI — actually run this; never assume green** (2026-08-25:
   `quality-architecture-audit` sat red across three pushes because this step
   was skipped).
   ```bash
   gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status
   ```
   Read each `context` (`ci/woodpecker/push/<workflow>`) and its `state`
   independently; one `failure` means not verified.
   - `tests-pytest` must succeed. Pull the failing step's log with
     `scripts/woodpecker-status --pipeline N --log STEP` (needs
     `WOODPECKER_TOKEN`; see that script's header) or the status's
     `target_url`. Fix, verify locally, commit, push.
   - `tests-dependency-audit`: zero known vulnerabilities is the baseline
     (fixed 2026-08-23); a new one is a fresh regression, not pre-triaged debt.
   - `quality-architecture-audit` red usually means manifest drift, not a
     scanner finding: `python -m tools.project_manifest --check
     static/project-manifest.json --repo-root .` first (`--write` to
     regenerate), then `python -m tools.quality_audit`.
   - The `selenium` step shows `failure` on every pipeline, green ones
     included — it is the service container's teardown, not a check.
   If `gh api` is unavailable, use `scripts/woodpecker-status` or the
   Woodpecker UI; if neither is reachable, say so rather than skipping.

7. **Roadmap.** If this checkpoint closes a `ROADMAP.md` item, run
   `/close-roadmap-item` now.

8. **Workflow tools.** Run them for real signal — a handspun tool's place
   in this step is earned by its own run history, not assumed
   (`CLAUDE.md`'s Toolchain section, 2026-08-30).
   ```bash
   python -m tools.kanban_sync sync --sources worktree,roadmap,track   # mechanical board sync (plan-doc classification is /kanban-board-sync)
   python -m tools.quality_coordination                                # AQC: stale branches/worktrees, plans with unfinished tasks; ~16 s, read-only
   python -m tools.quality_ratchet &                                   # observation series over tools.quality_audit findings; fire and forget
   ```
   Report every AQC `escalation_eligible` line and add any new one to
   `docs/open-decisions.md` with a next action. A missing `gh` `project` scope
   prints an actionable error — note it, don't fix it mid-checkpoint
   (`gh auth refresh -s project` is a one-time human action). One session
   writes to the board at a time: `ListAgents` first.

9. **PR — only when this checkpoint completes the initiative.** `gh pr create`
   if none is open; read the body; when CI is green and the diff is reviewed,
   `gh pr merge --merge`, then:
   ```bash
   scripts/cleanup-worktrees.sh                                        # removes provably merged worktrees + branches; reports everything else
   (cd <primary> && npx gitnexus@1.6.10 analyze --skip-agents-md)      # graph back at HEAD; restart the session if the MCP then errors
   ```
   A mid-initiative checkpoint just leaves the branch pushed and green.

10. **Summary.** 2–3 sentences on what this checkpoint covered (survives a
    later `/compact`). Then suggest — don't insist — `/compact` if context is
    heavy, `/clear` if the next task is unrelated.

## Scope note

CLAUDE.md's standing instruction (2026-08-16) authorizes proactive commit/push
at verified checkpoints; this skill covers when and how. It never commits a
failing or half-finished state and never decides what counts as "done".
