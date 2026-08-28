# Domain: realtime (Kalshi WebSocket/REST data plane)

Canonical: `docs/superpowers/plans/2026-08-25-realtime-data-plane-remediation.md` and its
`specs/` and `research/` siblings. In flight as of 2026-08-28 under
`.claude/skills/realtime-data-plane-investigation/`, which stays that plan's orchestrator
until it closes; this file takes over for realtime work started after that.

- `.claude/rules/realtime-data-plane-evidence.md`: no tuning by intuition (queue, consumer, connection, subscription, rate, batch, TTL, retry, poll, thread counts) without a measured bottleneck; hot-path additions are measured for runtime cost.
- Evidence first from what exists: `GET /api/health/pipeline`, `GET /api/observability/summary`, `tools/realtime_pipeline_replay.py`, `tools/rest_scheduler_replay.py`; `observability-performance` skill.
- Coordinate: `ListAgents` and `git worktree list` — another session may be measuring against the shared REST limiter.
