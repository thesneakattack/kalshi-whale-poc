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

1. **Decide where to verify.** Default: skip a redundant local full-suite
   run and let CI be the gate (steps 2-5) — the per-edit `PostToolUse`
   hook (`run_tests.py`) already ran pytest locally after every real
   `main.py`/`services/*.py` edit this session, so a second full local run
   right before committing is usually just repeating work CI is about to
   do anyway in a clean environment. Run locally first (`ddev exec -s
   fastapi python3 -m pytest -q`) instead when there's a specific reason
   to want faster/richer feedback than a ~40s CI round-trip: actively
   debugging a specific failure, a large/risky change you want to sanity
   check before it's even committed, or `gh`/CI itself is unavailable.
   Direct instruction (2026-08-16): "there'll still be occasions where you
   prefer to test locally first regardless, and i accept that" — this is
   a judgment call each time, not a hard rule either way.

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
   triggers — GitHub now independently runs the full suite in a clean
   environment.

5. **Confirm CI and interpret the result — this is the real gate when
   step 1 skipped the local run.** `gh run watch --exit-status` (or, to
   trigger a run without waiting on the push-triggered one, `gh workflow
   run tests.yml` first). On failure: `gh run view --log-failed` to pull
   the actual failing output back into the session, fix it the same way
   any bug gets fixed, verify (locally this time, to close the loop fast
   rather than round-tripping CI again for the same fix), then commit +
   push the fix — never leave `main` red. If `gh auth status` isn't
   authenticated, say so rather than silently skipping this step.

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
