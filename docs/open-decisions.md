# Open decisions

One line each: item · next action · who decides · since. Printed by
`session_orient.sh` every session. Remove a line when it is done; never
archive here. A `feedback` memory or "standing guidance" gets its line here
in the same session it is written. This list is the track — no new plan doc
for anything already on it.

- Retire `realtime-data-plane-investigation` and `root-cause-debugging` skills and replace `realtime-data-plane-evidence.md`'s Completion rule with the stop rule · only after the realtime session closes `2026-08-25-realtime-data-plane-remediation.md` (multi-day, P8 Task 40 telemetry-gated) and repoints its header to `plan-task` + `domains/realtime.md` · me, on that session's signal · 2026-08-28
- Retire AQC tools or keep one on cron · workflow-remediation Task 7 → decision · you · 2026-08-27
- `tools/kanban_sync` vs github-issues-kanban plugin: one writer · workflow-remediation Task 8 → decision · you · 2026-08-27
- Keep the per-edit test hook at all (now 9s, scoped) · workflow-remediation Task 9 → decision · you · 2026-08-27
- Full suite run 2026-08-28 04:2x against the primary checkout's *uncommitted* state showed 6 failures (test_quality_coordination_cleanup_actions + 5) · trust CI on that branch, not a local run of another session's working tree · other session · 2026-08-28
- `advisory_engine` suggests on win rate alone, never cost/P&L · implement cost-aware suggestion or close · you (design approval) · 2026-08-22
- Banded cost-aware gate EV diagnostic (0.60–0.95 band negative-EV) · approve `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md` or close · you · 2026-08-26
- Shadow-mode evaluation stretch has never run · schedule a dated stretch · you · 2026-08-26
- Path-based CI test selection for code changes · find the original rejection incident, then decide · me → you · 2026-08-26
- Trade-stream consumer liveness watchdog (queue at capacity, no drain) · implement in realtime branch · other session · 2026-08-27
