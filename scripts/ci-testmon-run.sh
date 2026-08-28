#!/bin/sh
# Decides push-event vs PR-event/main-push pytest scope and runs it. A real
# script file, not inline Woodpecker `commands:` YAML - required, not a
# style choice. Found live 2026-08-26: Woodpecker performs its own
# `${VAR}` text substitution directly on inline `commands:` strings before
# a shell ever sees them, using its own known CI_* variable set - any
# `${VAR}` reference it doesn't recognize (a plain local shell variable
# like `${BRANCH_KEY}` set mid-script) is silently replaced with an empty
# string, not left for the shell to resolve at runtime. Confirmed via the
# real pipeline log: `BRANCH_KEY="$(echo "$CI_COMMIT_BRANCH" | tr '/' '_')"`
# executed correctly (its `${CI_COMMIT_BRANCH}` is Woodpecker-known), but
# every later `${BRANCH_KEY}` reference had already been blanked to "" in
# the text handed to the shell - `mkdir -p "/testmon-cache/${BRANCH_KEY}"`
# ran as `mkdir -p "/testmon-cache/"`, collapsing every branch onto one
# shared cache directory. A real script file has no such preprocessing -
# `sh scripts/ci-testmon-run.sh` reads this file and a real shell resolves
# every `$VAR` normally, exactly like scripts/ci-skip-heavy-suite.sh
# already does for the same underlying reason.
#
# Assumes the caller already installed requirements-dev.txt and already
# checked scripts/ci-skip-heavy-suite.sh's SKIP verdict - this script only
# decides full-suite vs testmon-scoped and runs pytest accordingly.
set -u

if [ "${CI_PIPELINE_EVENT:-}" = "pull_request" ] || [ "${CI_PIPELINE_EVENT:-}" = "manual" ] || [ "${CI_COMMIT_BRANCH:-}" = "main" ]; then
  # The actual merge gate (PR event), a manual "run the tests" trigger, or a
  # just-merged main push - always full, unscoped, never testmon-selected.
  # See tests-pytest.yml's header, tiers 2 and 4.
  #
  # -m "not slow" (2026-08-26): excludes tests/test_quality_audit.py's two
  # real-repo-tree scans, which run the identical checks
  # .woodpecker/quality-architecture-audit.yml's own `architecture-audit`
  # step already runs, independently, as its own required PR-gate context
  # - measured at 73.7s + 11.77s of this suite's wall time for zero
  # coverage beyond what that dedicated job already enforces. That job is
  # unaffected by this change; it doesn't invoke pytest at all.
  exec python -m pytest -n 4 -m "not slow"
fi

# A push to a non-main branch, not yet a PR - testmon-scoped (tier 3).
# Restore this branch's cached baseline if one exists (a brand-new branch
# has none, so pytest-testmon naturally treats everything as unstable and
# runs it all - correct, safe first-push behavior, not a special case
# here). wp-testmon-cache is a Woodpecker-managed named volume mounted at
# /testmon-cache (tests-pytest.yml); one subdirectory per branch, slashes
# stripped since a branch name like fix/foo can't be a single path segment.
BRANCH_KEY=$(echo "$CI_COMMIT_BRANCH" | tr '/' '_')
CACHE_DIR="/testmon-cache/$BRANCH_KEY"
mkdir -p "$CACHE_DIR"
if [ -f "$CACHE_DIR/.testmondata" ]; then
  cp "$CACHE_DIR/.testmondata" .testmondata
fi
python -m pytest --testmon -n 4 -m "not slow"
STATUS=$?
cp .testmondata "$CACHE_DIR/.testmondata"
exit "$STATUS"
