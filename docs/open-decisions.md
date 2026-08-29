# Open decisions

One line each: item · next action · who decides · since. Printed by
`.claude/hooks/orient.sh` every session. Remove a line when it is done; never
archive here. A `feedback` memory or "standing guidance" gets its line here
in the same session it is written. This list is the track — no new plan doc
for anything already on it.

- `tools/kanban_sync` plan-doc classification only runs on demand · run `/kanban-board-sync` at PR-open and plan-close (mechanical sources already run from `/checkpoint`), one writer at a time (`ListAgents` first) · me · 2026-08-28
- The fastapi container has no `git`, so `tests/test_quality_coordination_cleanup_actions.py`, `tests/test_cleanup_worktrees.py`, and the launcher-prelude tests skip or fail locally and only prove out in CI · add git to the image (`Dockerfile`) or keep trusting CI for them · you · 2026-08-28
- `advisory_engine` suggests on win rate alone, never cost/P&L · implement cost-aware suggestion or close · you (design approval) · 2026-08-22
- Banded cost-aware gate EV diagnostic (0.60–0.95 band negative-EV) · approve `docs/superpowers/specs/2026-08-26-economic-strategy-remediation-design.md` or close · you · 2026-08-26
- Shadow-mode evaluation stretch has never run · schedule a dated stretch · you · 2026-08-26
- Path-based CI test selection for code changes · find the original rejection incident, then decide · me → you · 2026-08-26
- `feat/realtime-data-plane-remediation` has zero delta from `main` (its four phases landed in PRs #23, #107, #142, #152) but is kept checked out as the primary's parked branch, so AQC flags it `escalation_eligible` on every run · either record here that it stays parked deliberately, or delete it and park the primary on `main` · you · 2026-08-29
