---
name: integration-audit
description: Use after several cross-cutting changes, modularization, new services, route extraction, new background jobs, or before a major checkpoint/release when the system needs an integration sweep. Checks implemented-vs-wired behavior across routes, schedulers, persistence, config, frontend, CI, and hot-path impact.
---

# Integration Audit

This complements the existing `/checkpoint` skill. `/checkpoint` verifies,
commits, pushes, and reads CI; this skill asks whether the pieces are actually
integrated.

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
   - CI jobs genuinely run rather than skip,
   - diagnostic/research work is off the hot path.
4. Check for duplicate capability added alongside an existing diagnostics,
   alerting, analytics, or task-supervisor mechanism.
5. Apply the investigation-to-guard rule to any integration gap discovered.
6. Fix confirmed integration defects in focused commits before moving on.
