---
name: checkpoint
description: This skill should be used at natural breakpoints in a long working session on autotrade — after a verified, test-passing chunk of work, before starting a materially different task, or when the user asks to "checkpoint", "save progress", "commit and push", or "offload testing to github". Runs the test suite, commits at a clean point, pushes to trigger GitHub Actions CI, and flags whether it's a good moment to /compact or /clear.
---

# Checkpoint (verify, commit, push, offload CI)

Operationalizes CLAUDE.md's "Long-session workflow" directive: don't let
verified work sit uncommitted, and use GitHub Actions as a second,
clean-environment test run on top of (not instead of) the local per-edit
hook. See `run_tests.py` / `session_orient.sh` / `post_compact_reorient.sh`
in `.claude/hooks/` and `.github/workflows/tests.yml` for the pieces this
ties together.

## Steps

1. **Verify green.** Run the full suite locally: `ddev exec -s fastapi
   python3 -m pytest -q` (see the `/run` skill if `ddev` isn't up). If
   anything fails, stop here and report it — do not commit on red tests.

2. **Review scope.** `git status` and `git diff --stat` — confirm nothing
   unexpected is about to be staged (a stray `data/*.db`, `.env`, a session
   scratch file, or unrelated work-in-progress from earlier in the session
   that isn't actually verified yet). Stage specific paths only — never
   `git add -A` / `git add .`.

3. **Commit.** Write the message around *why*, in this repo's existing
   terse, direct style (`git log` for tone/examples — e.g. "Fix X", "Ship
   Y", "Cross-reference Z against W"). One commit per logical unit of
   work — if the session covered several unrelated things, split it into
   separate commits rather than one bundled one.

4. **Push.** `git push` on the current branch. This is what actually
   triggers `.github/workflows/tests.yml`'s `push`/`pull_request`
   triggers — GitHub now independently re-runs the full suite in a clean
   environment, not just this machine's `ddev` container.

5. **Confirm CI.** `gh run watch` (or, to trigger a run without waiting on
   the push-triggered one — e.g. re-running against the same commit —
   `gh workflow run tests.yml` first). Report pass/fail back. If `gh auth
   status` isn't authenticated, say so rather than silently skipping this
   step.

6. **Roadmap sync check.** If this checkpoint closes out a `ROADMAP.md`
   item, run `/sync-status-docs` now, before moving on — cheap, and keeps
   `status.html` from quietly drifting the way it already has once before.

7. **Session-hygiene prompt.** Give a short 2-3 sentence summary of what
   this checkpoint covered (keeps continuity across a later `/compact`).
   Then suggest — don't insist — whichever fits: `/compact` if context is
   getting heavy after a substantial chunk of work, `/clear` if the next
   task is materially unrelated to what was just finished.

## Scope note

This assumes CLAUDE.md's standing instruction authorizing proactive
commit/push at verified checkpoints (dated 2026-08-16) — it doesn't
re-litigate whether that's OK, only when/how. It never commits a failing
or half-finished state, and it doesn't decide what counts as "done" — that
judgment happens first, same boundary `/sync-status-docs` already draws
for itself.
