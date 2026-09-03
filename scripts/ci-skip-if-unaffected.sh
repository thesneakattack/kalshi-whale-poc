#!/bin/sh
# Decides whether a CI step can skip its expensive work because the change
# under test cannot possibly touch anything that step covers. Prints exactly
# "SKIP" or "RUN" to stdout and always exits 0 - never fails the calling
# step. POSIX /bin/sh (busybox ash in alpine/git), same as
# scripts/ci-skip-heavy-suite.sh, whose git/diff plumbing this mirrors.
#
# Difference from ci-skip-heavy-suite.sh: that script is EXCLUDE-based (skip
# only when EVERY changed file is docs/.claude/root-scaffold - a universal
# check every workflow can share). This one is INCLUDE-based and per-caller:
# skip when NO changed file matches the caller's own $RELEVANT_PATTERN.
# Deliberately a separate script, not a shared function ci-skip-heavy-suite.sh
# also calls, so each script's own logic stays simple enough to read in one
# pass - the git/diff block below is intentionally near-identical to that
# script's, not accidentally drifted.
#
# THIS IS NARROWER AND RISKIER THAN THE DOCS-ONLY CHECK - read before adding
# a new caller. tests-pytest.yml's own header already explains why this repo
# rejects guessed path-to-test mapping for real code changes ("a change
# anywhere in services/ or main.py can regress any other module") - that
# reasoning applies here too. This script is safe to call ONLY for a step
# whose own work has a genuinely traceable, non-transitive dependency
# surface - confirmed by reading its actual `import`/`from` statements, not
# assumed from directory names. tests-dependency-audit.yml's pip-audit is
# the first real user: it reads pinned version strings from requirements
# files only, never executes application code, so there is no transitive
# risk to trace. A workflow whose real dependency surface turns out to be
# wide once traced (kalshi-contract-fixtures.yml's 6 test files pull in
# risk_manager.py, market_history.py, signal_log.py, capture_writer.py, and
# more via real `from services... import` lines, confirmed 2026-09-03 - NOT
# just services/kalshi/) must NOT use this script with a narrow pattern; it
# would recreate exactly the missed-regression risk the tests-pytest.yml
# rejection above already guards against.
#
# Usage: RELEVANT_PATTERN='regex' sh scripts/ci-skip-if-unaffected.sh
set -u

if [ -z "${RELEVANT_PATTERN:-}" ]; then
  echo "ci-skip-if-unaffected.sh: RELEVANT_PATTERN must be set - failing safe to RUN" >&2
  echo "RUN"
  exit 0
fi

run() { echo "RUN"; exit 0; }
skip() { echo "SKIP"; exit 0; }

# Same event/safe-directory/branch-base logic as ci-skip-heavy-suite.sh - see
# that script's own comments for why each line is exactly this shape.
case "${CI_PIPELINE_EVENT:-}" in
  push|pull_request) ;;
  *) run ;;
esac

git config --global --add safe.directory '*' 2>/dev/null || true

if [ "${CI_COMMIT_BRANCH:-}" = "main" ]; then
  CHANGED="$(git diff --name-only 'HEAD^1..HEAD' 2>/dev/null)" || run
else
  git fetch --quiet origin main:refs/remotes/origin/main 2>/dev/null || run
  BASE="$(git merge-base origin/main HEAD 2>/dev/null)" || run
  [ -n "$BASE" ] || run
  CHANGED="$(git diff --name-only "$BASE" HEAD 2>/dev/null)" || run
fi

if [ -z "$CHANGED" ]; then
  skip   # nothing changed at all relative to the base
fi

if echo "$CHANGED" | grep -qE "$RELEVANT_PATTERN"; then
  run    # at least one changed path matches this caller's own relevant set
fi

skip
