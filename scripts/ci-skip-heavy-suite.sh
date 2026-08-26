#!/bin/sh
# Decides whether a CI step can skip its expensive work (full pytest suite,
# browser E2E build+run) because the change under test cannot possibly need
# it. Prints exactly "SKIP" or "RUN" to stdout and always exits 0 - never
# fails the calling step. POSIX /bin/sh, not bash: this runs in the tiny
# `alpine/git` image (busybox ash), the only image in these pipelines that
# actually has a git binary - see the design note below.
#
# Why this exists: investigation/research-only branches (docs/superpowers/**,
# .claude/**, root scaffold files) were paying the full battery - including
# npm ci + Selenium + Playwright and the full backend test suite - on every
# single push, even when the diff touched nothing but markdown. Direct user
# report (2026-08-25): this was inflating session wall time from ~2h to ~20h
# across an investigation with many small commits. This is deliberately NOT
# a general path-to-test-file relevance mapping for real code changes -
# tests-pytest.yml's own header comment already explains why this repo
# rejects that ("a change anywhere in services/ or main.py can regress any
# other module"): any actual code change still gets the FULL suite. This
# script only recognizes the one case where the relevant test set is
# genuinely empty - nothing outside docs/.claude changed at all.
#
# Design constraints (do not relax without re-reading these):
#   - This script NEVER changes which Woodpecker pipelines/steps RUN, and
#     NEVER changes what commit status gets posted to GitHub. Every step in
#     every gated pipeline still executes and still reports success/failure
#     exactly as before; only the WORK a step's commands do is conditional.
#     A pipeline/workflow skipped via Woodpecker's own `when.path` posts NO
#     commit status at all (confirmed live, see docs/woodpecker-ci.md's
#     "path-filtered workflow posts no status when skipped" note) - doing
#     that to a REQUIRED branch-protection context would permanently block
#     merging for any change that doesn't touch the filtered path. Nothing
#     here uses `when.path` for that reason; the pipeline/step structure is
#     unchanged, only the shell logic inside existing steps.
#   - git is not present in the images that run the actual heavy work
#     (python:3.13-slim - confirmed by quality-architecture-audit.yml's own
#     comment, "a python:3.13-slim image has no git binary anyway" - and
#     node:22-slim). So this script runs once, in its own first step using
#     the tiny `alpine/git` image, and writes its verdict to
#     build/.ci-skip-verdict in the shared pipeline workspace; every later
#     step reads that file (no git needed there) instead of re-running this
#     script per step.
#   - Fail SAFE, not fail fast: any ambiguity (can't fetch main, can't
#     compute a merge-base, verdict file missing/unreadable) means RUN,
#     never SKIP. A false "RUN" costs a few CPU-minutes; a false "SKIP"
#     means a real code change goes unverified.
#   - The comparison is against the WHOLE branch's cumulative diff from
#     main (via merge-base), not just the latest commit - a branch that
#     starts docs-only and later adds a real code change (this repo's own
#     investigation branch did exactly this, adding tests/test_risk_manager.py
#     in a later commit) must start requiring the full suite again on its
#     very next push, not just on the commit that introduced the change.
#
# Usage: sh scripts/ci-skip-heavy-suite.sh   (run from repo root, after clone)
set -u

SAFE_PATTERN='^(docs/|\.claude/|README(\.[A-Za-z]+)?$|CHANGELOG\.md$|ROADMAP\.md$|BUNDLE_README\.md$|INSTALL_.*\.md$|START_.*\.md$|PACKAGE_MANIFEST\.json$)'

run() { echo "RUN"; exit 0; }
skip() { echo "SKIP"; exit 0; }

# Only push/pull_request ever reach these pipelines (.woodpecker/*.yml's own
# `when: event:`); anything else (manual, cron if ever added, or a bare
# manual invocation outside CI with no CI_PIPELINE_EVENT set at all) runs
# full and - importantly - returns before the safe.directory config below,
# so running this by hand on a real machine never touches that machine's
# own git config.
case "${CI_PIPELINE_EVENT:-}" in
  push|pull_request) ;;
  *) run ;;
esac

# Woodpecker's own clone (and this script's alpine/git step) may run as a
# different UID than the one owning the checked-out files, which git 2.35+
# refuses to operate on by default ("detected dubious ownership in
# repository") - caught live during offline container verification of this
# script (root inside alpine/git vs. the host UID owning the bind-mounted
# tree). Wildcard rather than a specific path since Woodpecker's real clone
# path isn't pinned down here. --global, not --local: git blocks writing
# local config to an untrusted repo for the same reason it blocks reading
# it, so local scope can't bootstrap past this check.
git config --global --add safe.directory '*' 2>/dev/null || true

if [ "${CI_COMMIT_BRANCH:-}" = "main" ]; then
  # A push landing directly on main is a just-merged PR. Compare against the
  # pre-merge tip (first parent) - "what did this merge introduce".
  CHANGED="$(git diff --name-only 'HEAD^1..HEAD' 2>/dev/null)" || run
else
  # A feature/initiative branch (push or pull_request event): compare the
  # branch's FULL cumulative diff against where it forked from main, not
  # just the latest commit - see the design note above. Explicit
  # destination refspec: `git fetch origin main` alone updates only
  # FETCH_HEAD, not refs/remotes/origin/main, so the merge-base lookup
  # below would silently fail even on "success" (caught live: this exact
  # bug made every case fail-safe to RUN during offline verification).
  git fetch --quiet origin main:refs/remotes/origin/main 2>/dev/null || run
  BASE="$(git merge-base origin/main HEAD 2>/dev/null)" || run
  [ -n "$BASE" ] || run
  CHANGED="$(git diff --name-only "$BASE" HEAD 2>/dev/null)" || run
fi

if [ -z "$CHANGED" ]; then
  skip   # nothing changed at all relative to the base - nothing to verify
fi

if echo "$CHANGED" | grep -qvE "$SAFE_PATTERN"; then
  run    # at least one changed path is outside the docs/.claude allowlist
fi

skip
