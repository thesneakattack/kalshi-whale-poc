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

4. **Push.** `git push` on the current branch. Once this repo is activated
   in the shared Woodpecker instance (`portfolio/ci-cd/` - a one-time
   manual step via its web UI, see `.claude/skills/ci-cd-guardrails/
   SKILL.md`), this triggers every `.woodpecker/*.yml` workflow whose
   `when:` matches the push. `.github/workflows/tests.yml`/`quality.yml`
   are `workflow_dispatch`-only now and do NOT run automatically on push -
   don't wait on them here.

5. **Confirm CI and interpret the result — this is the real gate when
   step 1 skipped the local run.** Use `scripts/woodpecker-status` (needs a
   personal `WOODPECKER_TOKEN` in the environment - the script explains how
   to get one if it's missing) to list this commit's pipeline runs and
   their conclusions across the separate `.woodpecker/*.yml` workflows.
   Read each workflow's conclusion separately, the same way the former
   GitHub Actions jobs were read individually rather than off one
   aggregate status:
   - `tests-pytest` must succeed. On failure, pull the failing step's log
     (`scripts/woodpecker-status --log <pipeline>` or the Woodpecker web
     UI), fix it the same way any bug gets fixed, verify locally to close
     the loop fast rather than round-tripping CI again for the same fix,
     then commit + push the fix — never leave `main`'s `tests-pytest`
     workflow red.
   - `tests-dependency-audit` should show **zero known vulnerabilities**
     (fixed 2026-08-23 — `ROADMAP.md`'s "Path to production" section,
     checked off; confirmed live 2026-08-24 running this exact pipeline via
     `woodpecker-cli exec`: "No known vulnerabilities found"). If it shows
     a new failure, that's a fresh regression to investigate, not
     pre-triaged debt — earlier versions of this skill said otherwise
     (20 CVEs, deliberately left unfixed); that was true when written but
     is stale now that the fix shipped.
   If `WOODPECKER_TOKEN` isn't set and Woodpecker's web UI isn't checked
   either, say so rather than silently skipping this step — same standard
   `gh auth status` used to set for the GitHub Actions path.

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
