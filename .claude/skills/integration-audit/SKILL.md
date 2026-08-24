---
name: integration-audit
description: Use after several cross-cutting changes, modularization, new services, route extraction, new background jobs, or before a major checkpoint/release. Checks implemented-vs-wired behavior across routes, schedulers, persistence, config, frontend, runtime diagnostics, and CI/CD ownership.
---

# Integration Audit

This complements `/checkpoint`. `/checkpoint` verifies/commits/pushes/reads CI;
this skill asks whether each capability is actually integrated and permanently
owned at the correct layer.

1. Run the relevant current full-suite/static/frontend checks.
2. Inspect changes since the last integration boundary.
3. Verify:
   - every new `APIRouter` is mounted,
   - every scheduler/background `_maybe_*` path has a real caller,
   - every stateful module is in centralized test isolation,
   - no test reaches live `data/`,
   - new stores have retention/backup consideration,
   - config fields have real consumers,
   - frontend modules are imported and bundle is synced,
   - frontend API calls have matching backend routes,
   - new services are not zero-caller/dead unintentionally,
   - diagnostic/research work is off the hot path.
4. For every new recurring check, verify ownership:
   - deterministic clean-checkout check -> GitHub Actions;
   - runtime-state check -> application diagnostics/observability;
   - network/slow upstream check -> scheduled/manual workflow;
   - mixed failure class -> both where useful.
5. Verify CI jobs genuinely invoke their checker and do not silently skip.
6. Check for duplicate capability beside existing diagnostics, alerting,
   analytics, task-supervisor, test, or workflow mechanisms.
7. Apply the investigation-to-guard rule to any integration gap discovered.
8. Fix confirmed defects in focused commits before moving on.
