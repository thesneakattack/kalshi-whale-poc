# Kalshi Integration Authority

This rule applies whenever work reads, writes, parses, normalizes, stores semantically,
or makes decisions from Kalshi REST/WebSocket/SDK data.

## Documentation authority

Do not settle Kalshi contract behavior from model memory when the repository contains the
answer.

Before changing Kalshi-shaped behavior:

1. read `docs/kalshi/CHEATSHEET.md`;
2. read the exact current mirrored official page(s) under `docs/kalshi/`;
3. load/use `.claude/skills/kalshi-contract-review/SKILL.md`;
4. compare the documented contract against current adapter, persistence, consumer, and
   fixture behavior;
5. if local docs may be stale or the task is specifically about drift, verify upstream
   through the repository's existing docs-drift/public-canary mechanisms or current
   official documentation;
6. treat docs/live disagreement as an explicit contract discrepancy rather than guessing.

AI/model memory is never the authoritative source for a Kalshi field, endpoint, channel,
message type, lifecycle state, order schema, fixed-point rule, fee/rate-limit rule, or
REST-vs-WS alias when the documentation can answer it.

## Integration-boundary target

The dual-phase initiative is defined by:

- `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-dual-phase.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md`
- `docs/superpowers/plans/2026-08-24-kalshi-integration-phase-c.md`

When implementing that initiative, drive its plans with `superpowers:executing-plans`
and run `kalshi-contract-review` before every task that touches a Kalshi field.

## Permanent semantic rule

Opaque raw Kalshi payloads may be preserved or transported for diagnostics, archival,
observability, and research.

Vendor-specific semantic interpretation lives in `services/kalshi/` — the migration is
complete (Phase A merged 2026-08-25; Phase C finalized the boundary the same day). Do not
create semantic interpretation outside that boundary; the architecture audit's
`kalshi_boundary` scanner enforces the containment on every push, and
`services/kalshi/CHEATSHEET.md` documents the permanent add/change-an-endpoint workflow.

## Safety

Documentation/refactor work never justifies enabling real trading, weakening account
gates, resetting live data, or adding expensive validation to exchange-wide hot paths
without measurement.
