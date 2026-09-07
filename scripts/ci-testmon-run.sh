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
#
# PYTEST_SPLIT (2026-09-03, required): "app" or "tooling" - selects which
# half of the suite this invocation runs, per
# tools/classify_pytest_app_vs_tooling.py's AST-based, import-derived split
# (a test file is "app" iff it imports something from services.* or main,
# anywhere in its own source, including function-local lazy imports - see
# that script's own module docstring for why this boundary is safe: the CI
# pipeline audit's pytest-profile doc already measured app-code and
# tooling-code tests as provably non-interacting). Two callers,
# .woodpecker/tests-pytest-app.yml and tests-pytest-tooling.yml, each their
# own required branch-protection context - replaces the single
# tests-pytest.yml context that used to run everything unconditionally on
# every PR/main push, the actual "massive suite on every push" complaint
# this whole audit started from. Each half gets its own testmon cache
# subdirectory (see CACHE_DIR below) so the two runs' fingerprints never
# clobber each other.
set -u

case "${PYTEST_SPLIT:-}" in
  app|tooling) ;;
  *)
    echo "ci-testmon-run.sh: PYTEST_SPLIT must be 'app' or 'tooling', got '${PYTEST_SPLIT:-<unset>}'" >&2
    exit 1
    ;;
esac

TARGET_FILES=$(python3 -m tools.classify_pytest_app_vs_tooling --list "$PYTEST_SPLIT")
if [ -z "$TARGET_FILES" ]; then
  echo "ci-testmon-run.sh: classify_pytest_app_vs_tooling returned zero files for '$PYTEST_SPLIT' - failing loud, not silently passing with nothing to test" >&2
  exit 1
fi
# shellcheck disable=SC2086
TARGET_PATHS=$(for f in $TARGET_FILES; do printf 'tests/%s ' "$f"; done)

if [ "${CI_PIPELINE_EVENT:-}" = "pull_request" ] || [ "${CI_PIPELINE_EVENT:-}" = "manual" ] || [ "${CI_COMMIT_BRANCH:-}" = "main" ]; then
  # The actual merge gate (PR event), a manual "run the tests" trigger, or a
  # just-merged main push - always full, unscoped, never testmon-selected,
  # for THIS half of the suite. See tests-pytest-app.yml/tests-pytest-tooling.yml's
  # own headers, tiers 2 and 4.
  #
  # -m "not slow" (2026-08-26): excludes two tests
  # (`tests/test_quality_audit.py::test_unit_cost_scanner_is_clean_on_this_repo`,
  # `tests/test_historical_data_backfill.py::test_module_never_reads_deprecated_direction_aliases_directly`)
  # that duplicate checks `.woodpecker/quality-architecture-audit.yml`'s own
  # required job already runs independently - together ~7.6s of this
  # suite's wall time for zero coverage beyond what that dedicated job
  # already enforces (corrected 2026-09-03: two other candidate tests in
  # tests/test_kalshi_census.py were found NOT to be duplicated and were
  # left unmarked - see docs/archive/lane-9-tooling-ci-process-governance/research/2026-09-02-ci-pipeline-audit.md's
  # Tier 1 #3 addendum). That job is
  # unaffected by this change; it doesn't invoke pytest at all.
  # shellcheck disable=SC2086
  exec python -m pytest -n 4 -m "not slow" $TARGET_PATHS
fi

# A push to a non-main branch, not yet a PR - testmon-scoped (tier 3).
# Restore this branch+split's cached baseline if one exists (a brand-new
# branch has none, so pytest-testmon naturally treats everything as
# unstable and runs it all - correct, safe first-push behavior, not a
# special case here). wp-testmon-cache is a Woodpecker-managed named volume
# mounted at /testmon-cache; one subdirectory per branch+split, slashes in
# the branch name stripped since e.g. fix/foo can't be a single path
# segment, split suffix appended so app and tooling never share one
# .testmondata (their file sets, and therefore their fingerprints, are
# disjoint by construction - see classify_pytest_app_vs_tooling.py).
BRANCH_KEY=$(echo "$CI_COMMIT_BRANCH" | tr '/' '_')
CACHE_DIR="/testmon-cache/${BRANCH_KEY}_${PYTEST_SPLIT}"
mkdir -p "$CACHE_DIR"
if [ -f "$CACHE_DIR/.testmondata" ]; then
  cp "$CACHE_DIR/.testmondata" .testmondata
fi
# --testmon-forceselect (2026-09-03): without it, `-m` alone silently
# deactivates testmon's selection entirely (testmon's own configure.py
# checks this flag before its `-m`-triggered deactivation branch) - every
# push since this script shipped 2026-08-26 had been running the full
# suite under testmon for nothing; this restores real per-push selection.
# shellcheck disable=SC2086
python -m pytest --testmon --testmon-forceselect -n 4 -m "not slow" $TARGET_PATHS
STATUS=$?
cp .testmondata "$CACHE_DIR/.testmondata"
exit "$STATUS"
