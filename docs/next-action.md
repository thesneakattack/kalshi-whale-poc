# Next action

> **Caveat added 2026-08-30 — this soak's pass criterion can read a false 0.**
> `oldest_message_age_sec` comes from `_oldest_message_age`
> (`services/kalshi/websocket.py:1169`), which inspects only the three queues
> and never `_ticker_by_market`. A wedged coalescing map with drained queues
> reports exactly 0.0 — perfect health. Fix that one-liner first, or the
> remaining boundary checks below cannot detect the one new failure mode
> `two_consumer_mode` introduced. Full write-up, plus two verified live bugs
> and a prioritized test-gap list:
> `docs/superpowers/research/2026-08-30-test-coverage-audit-handoff.md`
> (read it before picking up any of the deferred Minors named at the end).

Soak `two_consumer_mode` (enabled 2026-08-29 18:49 UTC) across several more
hourly boundaries, then decide whether it stays on permanently (P4 gate).

The 19:00 UTC boundary verification PASSED on all three criteria, under a
real cascade far larger than the ones that used to drop (7,600 lifecycle
events, resolver backlog peaking at 1,885 pending):
`dropped_window` 0 and lifetime drops 0 throughout; queue depth 0-7 with
`oldest_message_age_sec` ~0 at every sample (vs. 20,000/262s in the old
episodes); resolver firing every few seconds, 1,385 tickers resolved by
window end, backlog draining monotonically, 0 dropped; 3,600 ticker updates
coalesced; trades flowed uninterrupted (21k -> 203k processed).

Check the next 2-3 boundaries the same way (~5 min each):
`GET /api/health/pipeline` - `dropped_window` 0, `oldest_message_age_sec`
near 0, `schedulers.settlement_resolver.dropped_total` 0 and pending
falling after each cascade. If all clean for ~24h, record the flag as
permanent in config/settings.yaml's comment and close the P4 gate; also
revisit the three deferred review Minors before/with that decision
(open-position ticker priority in the coalescing pop, pending-map staleness
in _oldest_message_age, resolver draining while paused - PR #198 comment).

Watch item: quality summary shows rate-limit hits in 6/49 recent samples -
expected from the post-reload catch-up burst; if it persists past the soak's
first day, that's the next investigation (via the six diagnostic endpoints,
not sqlite3).
