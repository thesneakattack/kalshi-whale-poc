# Next action

Verify the settlement-cascade fix live across an hourly boundary (~15 min at
:55->:10): after the settlement-resolver branch merges and the app reloads,
watch `GET /api/health/pipeline` through the :00-:09 window that used to
produce the drops (71 of 81 episodes started there - root cause and evidence
in docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md,
2026-08-29 entry).

Confirm: `schedulers.settlement_resolver` shows recent `last_started_sec_ago`
after settlements occur; `ingest.queue_health.dropped_window` stays 0 through
the boundary; `oldest_message_age_sec` stays near 0. If drops recur at a
boundary anyway, the residual is the queue-split soak question - do NOT raise
queue capacity (CLAUDE.md names that exact anti-move).

Then decide (user call, config change - surface, don't auto-apply): flip
`realtime_data_plane.two_consumer_mode` to true for the paper-mode soak the
P4 gate requires. The flag defaults false; Tasks 18+19a shipped dark.
