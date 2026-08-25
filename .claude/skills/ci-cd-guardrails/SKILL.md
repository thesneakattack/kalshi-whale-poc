---
name: ci-cd-guardrails
description: Use when creating, extending, reviewing, or deciding permanent ownership for deterministic recurring quality checks. Applies when a failure can be safely detected in a clean checkout: tests, architecture/wiring checks, frontend build/contracts, generated artifacts, schema fixtures, manifests, dependency checks, or synthetic regression checks.
---

# CI/CD Guardrails

## Ownership decision

Prefer CI/CD as permanent owner when the check:
- does not require live application state,
- is deterministic/reproducible enough to gate or report,
- can run safely from a clean checkout,
- protects against a recurring failure class.

Use runtime diagnostics instead when the truth only exists in the running
system. Use scheduled/manual CI for network-dependent, upstream, expensive, or
canary checks. Use both when synthetic CI coverage and live runtime evidence
catch complementary versions of the same failure.

## Definition of done

A deterministic checker is not complete at "works locally."

For a new or extended guard:
1. implement the checker as testable code rather than burying all logic in YAML
   when reusable Python/JS logic is appropriate;
2. add tests for the checker;
3. prove it catches an isolated deliberate failure;
4. prove it passes current valid repository state;
5. wire it into an appropriate GitHub Actions workflow in the same logical
   task unless there is a documented reason not to;
6. verify the workflow invokes the real checker and does not silently skip;
7. give the job a meaningful name so a red check identifies the failure class;
8. keep unrelated failure domains separate.

## CI topology

Preserve the current separation:
- `tests / pytest` — Python correctness;
- `tests / dependency-audit` — dependency vulnerabilities.

Grow deterministic quality jobs separately, for example:
- `quality / architecture-audit`;
- `quality / frontend-build`;
- `quality / frontend-api-contract`;
- `quality / browser-e2e`;
- `quality / project-manifest`;
- `kalshi-contract / fixture-contracts`.

Use scheduled/manual workflows for:
- Kalshi documentation content drift;
- safe public API canaries;
- synthetic performance regressions until stable enough to gate PRs.

Do not collapse everything into one opaque script/job. A red job should tell
the developer what class of problem occurred.

## Failure semantics

- high-confidence deterministic regression: fail CI;
- heuristic/medium-confidence finding: report artifact/annotation first;
- known existing debt: explicit reviewed baseline, never blind suppression;
- network/upstream instability: isolate from ordinary code-regression jobs.

Do not "fix CI" by baselining a newly discovered real error.

## Safety

CI must:
- use temp/test data only;
- never consume real trading credentials for ordinary quality jobs;
- never enable or place real trades;
- never mutate repository historical `data/*.db`;
- avoid depending on DDEV-only hostnames unless the workflow deliberately
  provisions DDEV.

## Local vs CI verification policy (2026-08-24)

Direct instruction, implemented in full: Woodpecker CI is the authoritative
executor of expensive, exhaustive, repeatable repository validation.
Claude does not routinely run the complete test/build/audit suite on the
interactive development machine before every push.

```text
Claude / local dev            Woodpecker CI                  Merge/release
  targeted verification   ->    exhaustive validation    ->    branch protection
```

**Where it runs.** A shared Woodpecker server + one Docker-backed agent
already run for the whole `portfolio/` workspace, defined at
`portfolio/ci-cd/docker-compose.yml` (two directories up from this repo,
outside its own tree - not duplicated per-project). GitHub integration
(OAuth app, webhook creation on repo activation) is configured at that
shared instance, not per-repo. This repository's own pipeline definitions
live in `.woodpecker/*.yml` here (see that directory's files - one per
named check: `tests-pytest`, `tests-dependency-audit`,
`quality-frontend-build`, `quality-architecture-audit`,
`kalshi-contract-fixtures`). Each file is an independent Woodpecker
workflow, so independent checks run in parallel rather than one serialized
script (bounded today by the shared agent's configured concurrency, not by
anything in these pipeline files - see that skill's own repo for how to
raise it).

### During implementation - Claude owns targeted verification

Run:
- a failing targeted test first for TDD, then the smallest fix, then the
  targeted test green;
- the directly affected test(s) after any change;
- cheap, relevant regression tests when useful;
- syntax/lint checks on changed files;
- inexpensive integration/wiring checks (router mounted, scheduler called,
  persistence registered, config consumed) during implementation.

### Before an ordinary commit/push - do not reflexively run the full suite

Skip re-running every exhaustive repository-wide check before a push when:
- targeted verification for this change is clean, and
- nothing suggests a broader regression, and
- Woodpecker is available and configured to run the broader suite on push.

Still inspect the diff and repository safety state (`git status --short`,
`git diff --check`, no live `data/*.db`/`.env`/credentials staged) before
every push - that is unrelated to which system runs the test suite.

### After push - Woodpecker owns exhaustive validation

Inspect Woodpecker's result before treating a change as fully verified -
do not assume green, and do not fall back to a full local re-run just
because checking feels like friction (that defeats the point of this
policy). Found live 2026-08-25: `quality-architecture-audit` sat red
across three real pushes (`d644034`, `21a303a`, `b28a380` - a stale
`static/project-manifest.json` after files were added without
regenerating it) with nobody noticing, because this step was never
actually exercised.

**Zero-setup check - works in any Claude session, no personal
`WOODPECKER_TOKEN` needed.** Woodpecker posts a commit status back to
GitHub for every workflow, and `gh` is already authenticated:

```bash
gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status
```

Read each entry's `context` (`ci/woodpecker/push/<workflow-name>`) and
`state` independently, the same way the former per-job GitHub Actions
checks were read individually rather than off one aggregate - a
`failure` on any single context means the change is NOT verified,
regardless of the others being green. Each entry's `target_url` points at
the Woodpecker web UI for that run. `scripts/woodpecker-status --pipeline
N --log STEP` (needs a personal `WOODPECKER_TOKEN`, see that script's own
header) is how to pull the actual failing step's log text once `gh api`
has told you which workflow and pipeline number to look at. If a
pipeline fails:
1. inspect the failing workflow/step and its log output;
2. identify the actual failure - do not guess;
3. reproduce locally only as narrowly as necessary for that failure;
4. fix it;
5. run targeted verification locally;
6. commit, push;
7. let Woodpecker rerun exhaustive validation - do not respond to a full-
   suite CI failure by reflexively running the entire suite locally first
   unless that is genuinely necessary to diagnose it.

### Exceptions - exhaustive local verification is still appropriate when

- Woodpecker is unavailable;
- debugging requires reproducing a CI-only failure;
- the change touches CI itself (`.woodpecker/*.yml`,
  `.github/workflows/*.yml`, this skill);
- performing a deliberate integration checkpoint or final initiative/
  release verification (see the `final-verification` skill - its
  requirements are unchanged by this policy);
- the user explicitly asks for it;
- repository safety demands verification before any push regardless of
  what CI will do afterward.

### GitHub Actions' remaining role

`.github/workflows/tests.yml` and `quality.yml` are `workflow_dispatch`-
only now (manual fallback if Woodpecker is down, or a GitHub-native run is
specifically wanted) - kept, not deleted, since they are working,
independently-verified equivalents of the Woodpecker pipelines and cost
nothing while idle. `docs-drift-check.yml` is unchanged: it is a scheduled,
network-dependent check with no live-repository-state dependency, exactly
what stays on GitHub Actions rather than moving to Woodpecker (see "CI
topology" above - "Use scheduled/manual workflows for... Kalshi
documentation content drift"). No GitHub branch protection currently
requires any check by name (`gh api repos/.../branches/main/protection`
returns 404) - if that changes, point required checks at the Woodpecker-
reported context names, not the now-manual-only GitHub Actions ones, and
do not mark a path-filtered Woodpecker workflow (`quality-frontend-build`)
as required, since a skipped workflow posts no status at all and would
block merges on unrelated changes forever.
