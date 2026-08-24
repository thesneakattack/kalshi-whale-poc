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
