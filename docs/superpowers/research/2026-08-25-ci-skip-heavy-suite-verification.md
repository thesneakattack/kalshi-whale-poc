# CI fast-path verification note (perf/ci-skip-heavy-for-docs-only)

This branch adds `scripts/ci-skip-heavy-suite.sh`, used by `.woodpecker/tests-pytest.yml` and
`.woodpecker/quality-browser-e2e.yml` to skip the full pytest suite / browser E2E build+run when a
branch's cumulative diff from `main` touches nothing outside `docs/**`, `.claude/**`, or a short list of
repo-root scaffold files (see the script's own header for the full design rationale, fail-safe rules,
and the two real bugs caught during offline/container verification before this ever touched production
CI: a missing fetch destination refspec, and git's dubious-ownership protection inside the `alpine/git`
container).

This commit is deliberately docs-only (this file plus nothing else), to directly observe in production
Woodpecker whether the SKIP path fires correctly on the follow-up push to this same branch — the first
push (`489eebe`, the fix itself) necessarily touched `.woodpecker/**`/`scripts/**`, which correctly
selects the RUN path and pays the full suite once, proving that side works. This second push is the
other half of the same live proof.
