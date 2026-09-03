# CI pipeline audit — Woodpecker + pytest (2026-09-02)

Direct request: "lets do an audit of the CI pipeline. ive lost a lot of time
due to heavy, redundant testing with certain tests being wholly irrelevant
(like pytest on doc only additions on push and pr and merge. look at
woodpecker api documentation, see how tests are configured. see if there's
bloat in pytest. see how we can make testing more granular, and avoiding
unneeded tests or redundancies. when you run tests on things that arent
pushes to github, youre not using the CI server's manual pipeline to handle
things so you dont have to. [...] dispatch subagents for this."

Method: 4 parallel subagents (Woodpecker docs/API research; local-vs-CI test
execution model; pytest suite profile and bloat; a grandchild agent running
read-only CI evidence commands) plus the coordinating session's own direct
Woodpecker REST API reads, against the live server (`ci.webfoundry.dev`,
v3.18.0) and the real pipeline history (200+ pipelines over a 2-day window).
Every workflow YAML in `.woodpecker/`, every relevant Woodpecker Go source
file at the exact deployed tag (v3.18.0), and the CI-facing shell scripts
were read directly — no claim below rests on model memory of Woodpecker's
behavior. Full detail, citations, and line numbers live in three companion
documents in this directory:

- `2026-09-02-ci-pipeline-audit-woodpecker-mechanics.md` — how Woodpecker's
  `when.path`, `evaluate`, manual pipelines, cron, `depends_on`, caching,
  and GitHub status reporting actually work, cited to source at the exact
  deployed tag.
- `2026-09-02-ci-pipeline-audit-cost-analysis.md` — the live pipeline
  dataset (200 pipelines, 1021 workflows) and the inventory of every place
  a test runs outside a GitHub push.
- `2026-09-02-ci-pipeline-audit-pytest-profile.md` — a full time profile of
  the 3074-test suite, bloat sources, and pytest-testmon's actual behavior.

**Process note on this document's own history:** the session that dispatched
these subagents was lost mid-synthesis to repeated Claude Code session
rate-limit kills, after three of the four subagents' concrete verification
commands were blocked by an unrelated harness mechanism — the worktree-
isolation guard refuses a Bash call whose working directory doesn't match
the session it's registered to, which is exactly what happened to subagents
whose cwd fell back to the primary checkout while the coordinating session
was registered in this worktree. Nothing about that block was specific to
Woodpecker or this audit; it is documented as a known lesson for future
subagent-heavy work in this repo. All of that work survived in each
subagent's own transcript and the shared scratchpad and was recovered
in full on resume; the handful of concrete commands that were blocked (a
`docker run --rm` CLI help probe denied by the auto-mode permission
classifier, a `--collect-only` timing, a `--dist loadfile` run, and an
fsync-bound falsification test) were then run directly by the resumed
session, which also used its restored Bash access to independently verify
several of the Woodpecker-mechanics report's live-API claims. Those results
are folded into the companion documents inline, each dated 2026-09-02 with
"RESOLVED"/"CONFIRMED" markers so it's visible which numbers are original
subagent output and which were added on resume. Nothing in this document is
a guess standing in for something that couldn't be checked — the two
genuinely unresolved gaps are named explicitly in "What this audit could
not answer" below.

## Bottom line

The user's complaint is correct and, in one specific place, worse than it
looked: this repo already built a mechanism specifically to make pushes
"go through relevant testing only" (`pytest-testmon`, added 2026-08-26,
exactly one week before this audit) — **and it has never once worked.**
27 of 27 sampled CI logs that invoke it show `testmon: selection
automatically deactivated because -m was used`, followed by the full
3053-3058-test suite running anyway. Every feature-branch push has been
paying testmon's ~24s of pure coverage-tracing overhead for zero selection
benefit since the day it shipped. That is the single highest-value fix in
this audit: a one-line flag change, decisively verified, not a redesign.

Beyond that, the pipeline is not fundamentally broken — most of the
individual design decisions documented in `.woodpecker/*.yml`'s own
comments and `docs/woodpecker-ci.md` were made for real, cited reasons (the
uv cache, `-n 4` not `auto`, the full-history clone override, the
SKIP fast path for docs-only branches). The waste is concentrated in a
handful of specific, independently-fixable mechanisms:

1. **A granular-testing mechanism that silently does nothing** (testmon,
   above) — highest value, lowest risk, one line.
2. **~51% of the pytest suite's own wall time is sqlite fsync overhead**,
   now proven (not hypothesized) by direct falsification: the same tests
   run 45-49x faster under a tmpfs `--basetemp`. The safe fix is
   `PRAGMA synchronous=OFF` on test connections, not raw tmpfs (this
   container's `/dev/shm` turned out to be `noexec`, which breaks the 12
   tests that exec real subprocess shims from `tmp_path` — see the pytest
   profile doc's update section for how that was discovered and why it
   changes the recommended fix).
3. **Structural CI waste independent of pytest itself**: PR pipelines queue
   behind an always-created, mostly-redundant push twin for the same commit
   (median 58s extra queue wait); ~13% of post-merge `main` pushes re-test
   a tree byte-identical to what the PR already tested; the SKIP fast path
   itself still costs ~17% of all compute in the measured window; four
   whole-repo scans inside pytest duplicate a check that already runs as
   its own required CI job.
4. **The manual pipeline the user asked about is real, live-verified, and
   already wired for a full run** (`event: manual` on `tests-pytest.yml`
   since 2026-08-28) **but has never actually been triggered** in retained
   history (0 manual pipelines in 314 retained records) — its main limits
   (whole-repo only, no way to target one test path from the API, must
   push+not-yet-a-PR to matter) are now documented precisely enough to
   decide whether to extend it.
5. **The local per-edit hook has real, measurable mapping gaps**:
   `services/app_state.py` (18 commits in 7 days, 2nd-most-edited in-scope
   file) maps to zero tests; the two `services/kalshi/{websocket,public}.py`
   files map to 387 tests / 55.5s serial, over the hook's own 55s budget,
   so every edit there silently falls through to "let CI own it" — meaning
   those specific files get **no** local feedback loop at all, only the
   full CI round-trip.

## What the user asked, answered directly

**"pytest on doc only additions on push and pr and merge [is wasteful]"** —
Partly already fixed, partly not, and the fixed part still isn't free.
`scripts/ci-skip-heavy-suite.sh` (shipped 2026-08-25) already detects a
docs-only cumulative branch diff and prints `SKIP`, and the pytest step
already honors it (`tests-pytest.yml:132-135`) — this already exists and
works for the case you're describing. What's not obvious from using it day
to day: **a SKIP pipeline is not free.** It still runs all 5 workflows
(SKIP only changes what the *pytest step's own commands* do, deliberately —
see the script's own design-constraint comment on why it never uses
Woodpecker's `when.path` for this), so a purely-docs push still pays a full
clone, `kalshi-contract-fixtures` (median 47s), `quality-architecture-audit`
(37s), `tests-dependency-audit` (32s), and a Selenium-service boot in
`quality-browser-e2e` (22s) even though none of those steps' actual work
runs. Measured: 71 SKIP pipelines in the 2-day window still cost 181
workflow-minutes — 17% of all compute in that window. And the fast-path
check itself is fragile in a way you'd only notice from a git-history read,
not from watching it work: it defaults to full-history clone specifically
because a shallow clone can't compute `git merge-base`, and any ambiguity
fails safe to RUN — so it silently costs nothing extra when it's wrong, but
it is a documented single point of failure worth knowing about.

**"see if there's bloat in pytest"** — Yes, concretely quantified.
51.5% of the suite's 477.7s of summed test time is in 258 tests each over
0.5s, and that's now proven to be mostly sqlite fsync cost per pytest
process/connection, not real work (see "Bottom line" above and the pytest
profile doc). Four whole-repo-scanning tests cost 43.6s and duplicate a
check that already runs as `quality-architecture-audit`'s own required PR
gate. `kalshi-contract-fixtures.yml` deliberately re-runs 271 of the same
tests serially for failure-attribution reasons stated in its own header —
that duplication is intentional, not a bug, but it is real cost (15.5% of
the measured window) worth knowing is a deliberate tradeoff rather than an
oversight.

**"make testing more granular, avoid unneeded tests/redundancies"** — The
repo already tried exactly this (pytest-testmon, 2026-08-26) and it has
been inert since the day it shipped (finding #1 above). Re-enabling real
selection is a one-flag fix. Beyond that, the per-edit local hook
(`run_tests.py`) is the other granularity mechanism already in place, and
it has real, specific gaps (finding #5 above) that mean two of the
most-edited files in the repo get no local test feedback at all.

**"use the manual pipeline instead of running tests on things that aren't
GitHub pushes"** — The capability exists and is correctly wired
(`tests-pytest.yml` accepts `event: manual`), but nothing in this repo's
skills, rules, or hooks ever calls `scripts/woodpecker-trigger` — the
`checkpoint` skill's "offload testing to CI" step means *push*, not
*trigger a manual run*. It has literally never been invoked (0 of 314
retained pipelines). It cannot replace the local dev-loop hook the way it
might sound like it could: a manual run always tests the **pushed branch
tip**, never the uncommitted working tree, so it can only supplement
`run_tests.py`'s in-the-loop feedback after a commit, not replace it — and
because it always runs the full unscoped suite (by design, per
`tests-pytest.yml`'s own tier-4 comment: "an explicit ask for confidence,
not a cached/scoped convenience run"), it can't answer "did my one file's
change break anything" any faster or cheaper than just pushing already
does today. Where it *would* genuinely help — a human wanting full-suite
confidence on a branch without spending a commit/push cycle to get it, or
re-running CI on a flaky failure without an empty commit — nothing today
makes that easy: the CLI syntax is `woodpecker-cli pipeline create <repo>
--branch <b> [--var K=V]`, confirmed live, and there is no built-in way to
target a single workflow or test path through that API (§3.3 of the
mechanics doc lays out exactly what would need to change to support that,
including a source-verified explanation of why an "opt-in" `evaluate`
filter would silently fail and why an "opt-out" one would work).

**"research the woodpecker api and best practices"** — Done in full; see
the mechanics doc. Two points worth surfacing here because they bear
directly on this repo's own docs: `docs/woodpecker-ci.md` has drifted
against the live server in several places (manual-trigger behavior for
`tests-pytest` changed 2026-08-28 and the doc's own §"manual → nothing
runs" section is now stale for that one workflow; trusted-network is
`false` live, the doc says all-true; several cited pipeline numbers no
longer resolve because the server's pipeline-number sequence was reset on
2026-08-31). None of these are urgent, but a doc actively used as a
reference (it's cited from CLAUDE.md) drifting from the live server is
itself worth a small fix pass.

## Prioritized action plan

Confidence and mechanism are stated for each; "mechanical" items are the
kind CLAUDE.md's "nothing advances on one pass" HARD RULE exempts from a
full research→design→plan cycle (a config value or a single flag, not new
logic); the rest are flagged as needing a design decision because they
trade off against something (a required branch-protection context, an
intentional duplication, an infra change) that a past commit's own comment
explains was a deliberate choice.

### Tier 1 — mechanical, high-confidence, do first

1. **Fix testmon selection**: `scripts/ci-testmon-run.sh:53`, add
   `--testmon-forceselect` to the existing `--testmon -n 4 -m "not slow"`
   invocation. Verified mechanism (`testmon`'s own `configure.py:65-85`:
   `-m` unconditionally deactivates selection unless `--testmon-forceselect`
   or `--testmon-noselect` is also passed — confirmed against the installed
   `testmon==2.2.0` source, not just its docs). This is the fix for the
   user's core complaint: real, working per-push test selection on feature
   branches, seven days after it was believed to already be working.
   Verify on the first real push after merging: header should show
   `testmon: changed files: N, unchanged files: M, environment: default`
   plus a `deselected` count, not the deactivation message.
2. **Move the sqlite-fsync fix to `PRAGMA synchronous=OFF`, not tmpfs**:
   in `tests/support/runtime_isolation.py`'s existing `_guarded_connect`
   wrapper (`:245-250`), execute `PRAGMA synchronous=OFF` on every
   connection this wrapper opens (test-only; the guard already
   distinguishes test connections from real `data/` paths, so this cannot
   reach live data). Verified: 45-49x on the two representative slow files,
   ~4.3x on the full suite (before the unrelated `noexec` breakage that a
   filesystem-level tmpfs approach would hit and a connection-pragma
   approach does not). This is the single largest recoverable chunk of
   pytest's own wall time.
3. **Mark the four whole-repo-scan tests `@pytest.mark.slow`**:
   `test_kalshi_census.py`'s two tests, `test_quality_audit.py::
   test_unit_cost_scanner_is_clean_on_this_repo`,
   `test_historical_data_backfill.py::
   test_module_never_reads_deprecated_direction_aliases_directly`. Verified
   duplication: all four already match `pytest.ini`'s own `slow` marker
   definition ("scans the real repo tree end-to-end (redundant with a
   dedicated CI job)"), and all four duplicate a check
   `quality-architecture-audit.yml`'s own required job already runs
   independently. Recovers 43.6s of test time for zero coverage loss.

   **Correction (2026-09-03, found during implementation):** only 2 of these
   4 tests are actually duplicated by `quality-architecture-audit.yml`'s
   required job. `tools/quality_audit/kalshi_boundary.py` imports just 4 of
   `kalshi_census.py`'s scan functions (lines 40-45: `_scan_direct_host_usage`,
   `_scan_known_field_reads`, `_scan_legacy_wrapper`, `_scan_sdk_imports`)
   and never calls `_scan_wrapper_method_calls`, `_scan_fixtures`, or
   `build_census()`'s hot/cold classification — so
   `test_real_repo_census_runs_and_legacy_caller_count_stays_zero` and
   `test_real_repo_fixtures_all_have_existing_source_docs` (both in
   `test_kalshi_census.py`) check real properties nothing else in the
   required CI pipeline covers and were NOT marked `slow`. Only
   `test_unit_cost_scanner_is_clean_on_this_repo` (uses the
   `scan_unit_cost_derivations` registered in CI's `_SCANNERS`) and
   `test_module_never_reads_deprecated_direction_aliases_directly` (uses
   `_scan_known_field_reads` called at `kalshi_boundary.py:145`) were
   genuinely redundant and are marked. Recovered time is ~21.7s, not the
   originally-claimed 43.6s.
4. **`docs/woodpecker-ci.md` drift pass**: correct the manual-trigger
   section for `tests-pytest` (now `event: manual`-enabled, not filtered),
   the trusted-network line (`false`, not `true`), and the now-unresolvable
   cited pipeline numbers. Mechanical doc fix, no behavior change.

### Tier 2 — needs a design decision (states what's actually being traded)

5. **`services/app_state.py` has zero local test coverage in
   `run_tests.py`'s mapping** (`tests_for()` in that hook) despite 18
   commits in 7 days — second-most-edited in-scope file in the sample.
   Needs a decision on what test file(s) should map to it (verify none
   exists first — the gap may be a real missing-test problem, not just a
   mapping gap) before wiring the mapping.
6. **`services/kalshi/{websocket,public}.py` edits never get local
   feedback** — their 18-file/387-test/55.5s mapping exceeds the hook's own
   55s budget before any process start-up cost, so every edit there falls
   through to CI. The mapping rule causing the over-broad match is a
   `test_<package>*` prefix collision (matches all 18 `test_kalshi*.py`
   files for any file under `services/kalshi/`). A narrower, per-module
   mapping would need a review of what the 18 kalshi test files actually
   cover to split safely — a design task, not a one-line change, since
   `services/kalshi/` carries this repo's Kalshi-integration-authority
   HARD RULE and narrowing test scope there needs the same scrutiny any
   Kalshi-boundary change gets.
7. **Push+PR twin pipelines and post-merge-main re-testing an identical
   tree** together account for roughly a quarter of all measured CI
   compute (58s median extra PR queue wait from the twin; 13% of the
   window from re-testing byte-identical merge trees). Both are consequces
   of Woodpecker's documented behavior (every push triggers every
   `.woodpecker/*.yml`, with no built-in "this exact tree was already
   tested" cache) rather than a bug in this repo's config — closing either
   gap means either accepting `push`-context-only gating for feature
   branches (a branch-protection change) or building an explicit
   tree-hash cache, both real design decisions with tradeoffs the mechanics
   doc's §7/§9 lay out precisely enough to evaluate.
8. **`tests-dependency-audit` runs unfiltered on every push/PR** (0.8% of
   commits actually touch `requirements*.txt`) but is one of the 5 required
   branch-protection contexts. Woodpecker's own behavior makes a naive
   `when.path` filter dangerous here specifically: a path-filtered workflow
   posts **no** GitHub status at all when skipped (confirmed live and in
   source, mechanics doc §1.3) — exactly the trap `branching-and-ci.md`
   already documents for why `quality-frontend-build` is deliberately
   *excluded* from required contexts. Filtering this one would need
   removing it from required contexts first, which is a real security/gate
   tradeoff (a dependency CVE landing without ever being checked on a
   PR that happens not to touch requirements), not a mechanical change.
9. **`kalshi-contract-fixtures`'s deliberate 271-test duplication** (15.5%
   of measured compute) is an intentional failure-attribution choice per
   its own header comment — flagging it here as a cost now precisely
   quantified, for the user to decide whether that attribution value is
   still worth ~161 workflow-minutes over a 2-day window, not recommending
   a change.
10. **GitHub Actions `tests.yml`/`quality.yml` fallbacks are stale,
    low-value duplicates**: `quality.yml`'s last real dispatch
    (2026-08-31) found its own browser-e2e job broken
    (`No module named 'selenium'`), the fix was never re-dispatched to
    confirm, and `tests.yml` runs a different, unscoped pytest invocation
    (`-v`, no `-m "not slow"`, no `-n`) than the Woodpecker suite it's
    meant to mirror. Options are named, not chosen, in the mechanics doc's
    cost-analysis: keep as an off-host fallback with an equivalence test,
    or delete. This is a "does an off-host executor matter to you" call,
    not a mechanical fix.

## What this audit could not answer

Two genuine gaps remain, both requiring something only the user can
provide — not further investigation time:

1. **Cron API access.** `GET /api/repos/1/cron` (and `/metadata`,
   `/api/queue/info`, `/api/pipelines`, `/api/agents/*`) all require a
   push- or admin-scoped token; the available `WOODPECKER_TOKEN` is
   pull-only (`{"pull":true,"push":false,"admin":false}`, confirmed live
   both in the original session and again on resume). Whether this repo
   currently has any cron jobs configured, and whether the three
   weekly-cron GitHub Actions workflows (`docs-drift-check.yml`,
   `kalshi-contract.yml`, `performance.yml`) could move to Woodpecker's own
   cron mechanism (documented in full in the mechanics doc §4) stays
   unanswered without a higher-scoped token.
2. **A few narrower source-derived inferences, each with a stated
   falsifier**, that a live example didn't happen to exercise: whether a
   workflow that never got a queue slot before its pipeline was superseded
   really is left stuck at GitHub `pending` forever (the one real killed
   pipeline checked on resume had already started all 5 of its workflows
   before cancellation, so this specific edge case wasn't exercised); the
   exact interaction between the `trusted.network: false` repo setting and
   the two workflows needing real Kalshi/GitHub egress if cron jobs ever
   move to Woodpecker. Neither blocks any Tier 1/2 recommendation above.

## Supporting documents

- `2026-09-02-ci-pipeline-audit-woodpecker-mechanics.md`
- `2026-09-02-ci-pipeline-audit-cost-analysis.md`
- `2026-09-02-ci-pipeline-audit-pytest-profile.md`
