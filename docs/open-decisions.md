# Open decisions

One line each: item · next action · who decides · since. Printed by
`session_orient.sh` every session. Remove a line when it is done; never
archive here. A `feedback` memory or "standing guidance" gets its line here
in the same session it is written. This list is the track — no new plan doc
for anything already on it.

- Retire `realtime-data-plane-investigation`, `root-cause-debugging`, and `kalshi-integration-refactor` skills (the last is named by `kalshi-integration-authority.md`, frozen while the realtime plan runs) and replace `realtime-data-plane-evidence.md`'s Completion rule with the stop rule · only after the realtime session closes `2026-08-25-realtime-data-plane-remediation.md` (multi-day, P8 Task 40 telemetry-gated) and repoints its header to `plan-task` + `domains/realtime.md` · me, on that session's signal · 2026-08-28
- AQC (`tools/quality_coordination.py`) is never invoked by any session, and its plan-health domain reads `.superpowers/sdd/*/progress.md` — ledgers this repo's workflow never creates — so abandoned plans have no detector · point the ledger domain at `docs/superpowers/plans/*.md` checkbox state + last-commit age; run AQC from `/checkpoint` and print its last result in `session_orient.sh` · me · 2026-08-28
- `tools/kanban_sync` is never run by any session, so the board never reflects branches/plans/PRs · run `/kanban-board-sync` at PR-open and plan-close (checkpoint + plan-task Task 10), one writer at a time (`ListAgents` first) · me · 2026-08-28
- Unused-tool inventory: `quality_ratchet.py`, `project_manifest.py`, `realtime_pipeline_replay.py`, `rest_scheduler_replay.py`, `kalshi_census.py`, `kalshi_rate_limit_probe.py`, `kalshi_docs_drift.py`, `watchlist_scale_stress_test.py` — for each, name the workflow step that should invoke it and wire it there; never propose deleting a user-built tool because it is unused · me · 2026-08-28
- Full suite run 2026-08-28 04:2x against the primary checkout's *uncommitted* state showed 6 failures (test_quality_coordination_cleanup_actions + 5) · trust CI on that branch, not a local run of another session's working tree · other session · 2026-08-28
- `advisory_engine` suggests on win rate alone, never cost/P&L · implement cost-aware suggestion or close · you (design approval) · 2026-08-22
- Banded cost-aware gate EV diagnostic (0.60–0.95 band negative-EV) · approve `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md` or close · you · 2026-08-26
- Shadow-mode evaluation stretch has never run · schedule a dated stretch · you · 2026-08-26
- Path-based CI test selection for code changes · find the original rejection incident, then decide · me → you · 2026-08-26
- Trade-stream consumer liveness watchdog (queue at capacity, no drain) · implement in realtime branch · other session · 2026-08-27
