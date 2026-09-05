# Open decisions

One line each: item · next action · who decides · since. Printed by
`.claude/hooks/orient.sh` every session. Remove a line when it is done; never
archive here. A `feedback` memory or "standing guidance" gets its line here
in the same session it is written. This list is the track — no new plan doc
for anything already on it.

2026-09-05: David delegated the whole backlog to a Fable-tier design pass
("review open-decisions and next-action and make the decisions on your own").
41 lines were decided; the reasoning for every one is on the GitHub issue or
comment it points at (issues #606–#616, comments on #322/#326/#331/#364/#366/
#398/#448/#468/#493/#530/#532), and the pass's summary is in the PR that
lands this file. Lines below are what is still genuinely open, plus decided
items whose implementation is tracked on GitHub — remove each of those when
its issue closes.

## Still open — David's call

- `main` branch protection is **off**, not merely unreadable: `branches/main` reports `protected: false`, `enforcement_level: off`; the repo is private on a Free plan and the 2026-08-25 required-status-check gate no longer exists (#615, live-verified). Interim practice is decided (read `commits/<sha>/status` for all six contexts before every merge; `mergeStateStatus: CLEAN` is not evidence). Restoring server-side enforcement needs GitHub Pro (paid) **or** making the repo public (a trading system's config/history) · pick one, or accept the interim practice as permanent · you · 2026-09-05
- `config/settings.yaml`'s working tree carries `whale_watcher_kalshi.min_contracts.KXBTC15M: 2500 → 2000` alongside your two recorded edits (`auto_exit_enabled: false`, `max_daily_loss_pct: 0`); `docs/next-action.md` records the two, not the third, and nothing in git explains it · confirm it is yours (then it stays uncommitted with the others) or say so if it is not · you · 2026-09-05

## Decided 2026-09-05 — implementation tracked on GitHub (remove when closed)

- `yes_ask_dollars == "1.0000"` with `yes_ask_size_fp == "0.00"` is Kalshi's no-resting-ask sentinel (live-verified; 67 of 276 watched markets right now); spread/coverage/ask-based NO valuation must treat it as *absent* at the `services/kalshi/` boundary · #606 · me
- `exit_engine/stale_price_uncorroborated` now has a tracker; `two_consumer_mode` stays a flag (it is the only control for #579's open root cause), permanence revisited when #579 closes · #607 · me
- `tools/soak_analyzer.py`: a queried-but-absent component is 0 → PASS (only a missing `by_component` dict is UNKNOWN); stall severity keys on frequency too; one generic write-fault check for `game_state`/`series_watcher`/`settlement_edge` · #608 · me
- Alerting gets an `error_fault_burst` category, edge-detected on `fault_log.summary(hours=1)` error count at the observability-window cadence, never per tick · #609 · me
- `position_netting` `locked_loss` defaults to hold-to-settlement (fee-free, no software-valued exit); early close only when headroom is measurably binding — measured $152.68 of fees across 13 groups for headroom nobody showed was binding · #610 · me
- `position_netting.normal_volatility` (0.02) is ~p97 of the live 30-min volatility distribution (nonzero median 0.0033), pinning the materiality bar to its 0.25 floor; align to `strategy.auto_exit_normal_volatility` (0.002) or share one key — config change, not touched while your safety edits are in the working tree · #611 · me
- `propagate_milestone_winners`'s event-ticker-into-market-filter defect is accepted non-functional while Sports/Politics are out of the watchlist; fix shape recorded for when they return · #612 · me
- CLAUDE.md: (1) an adversarial-review finding is a new claim and meets the evidence standard before consolidation adopts it — accepted; (2) the dimensional-analysis hook stays session-enforced by decision, not "not yet" · #613 (docs PR, own review cycle) · me
- Edge-gate enablement prerequisites: build spec D1 (banded cost-aware gate diagnostic, after #601's index fix); fail closed-but-counted on missing `P_pre` once `edge_gate_enabled` flips (measure the miss rate first); advisory cost-awareness (spec D5) approved in direction, after the enable decision · #616 · me
- `#532`: GO on write-time sampling of `min_contracts` at 1/100 (PR #604 implements it; still needs its own review cycle before merge); the 29.5M backlog is purged only after #604 merges, via the #578 pattern with its own checkpoint and explicit go · #532 comment · me
- Plugin pilot Task 1: GO for `pr-review-toolkit` + `claude-security`, after tonight's gate work and from one session; `claude-md-management` deferred until those report; `codspeed` declined (no external account) · #322 / #326 comments · me
- Weather-index ingestion: declined for now (ingestion with no consumer, no Climate series watched); design/plan stay valid to reopen · #331 comment; plan doc needs a "Declined 2026-09-05" header at the next board sync · me
- `whale_confidence_weights` re-validation is Task 10 (#364) on post-fix data — not a separate check; `_tied_run_size`'s two precision gaps land before that re-measurement · #364 comment · me
- "Apply suggested weights" gets a server-side 422 guard (contaminated factors / too few survivors) as a standalone change ahead of Task 10 — **until it ships, do not click Apply** (carried in `docs/next-action.md`) · #366 comment · me
- Restore the wiped `whale_confidence_weights` audit note (text in `7b91436`), fix `confidence_scoring.py:118,168`'s dangling pointer, and document the longshot fields as dormant defense-in-depth (dead under the 0.25–0.9 unit-cost band, kept because advisory can widen the band) — all in #493's PR, after the merge fix · #493 comment · me
- PR #441's Tasks 1–6 are coded on the never-pushed branch `fix/tier0-live-incident-remediation` (`26503db`): push + PR first, then Tasks 7/9/10; unplanned Tier 0 items 3/5/6 are #599/#609/#527; `market_history.db` corruption watch = weekly `quick_check` in the storage scan after Tasks 2–6 deploy · #448 comment · me
- `record_variant()`'s per-tick sync write joins #530's sweep; PR #394's non-deterministic race test stays as-is · #530 comment · me
- `feat/frontend-realtime-push` is superseded (plan text only; History push design is on `main`, work is #490): delete branch + close #398; `feat/candlestick-volatility` (5 commits, local-only) gets pushed so it cannot be lost, stays unmerged · #398 comment · coordinator
- Config documentation lives in the pydantic schema's field descriptions (`GET /api/config/schema`, #468), rendered by the Controls panel; YAML keeps only safety-flag comments · #468 comment · me
