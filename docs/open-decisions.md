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
- `feat/candlestick-volatility` — **DECIDED 2026-09-06 (David: "you should decide"), tracked as #641.** Not a stale scrap: 13 commits, ~1,055 lines, a complete tested feature (292-line module, 373 lines of tests written against real seeded data). **Keep the branch as a reference implementation, do not rebase, re-implement against current `main` when prioritized.** It is ~800 behind and predates three completed migrations it would regress (its own `_connect()`/`sqlite3.connect` vs `services/db.py`'s `register_schema`; a new background REST scan during the REST/event-loop stabilization; unmeasured prompt load on `market_analyst_agent`, which sits on the whale-scoring hot path). Re-evaluation is forced by #641's trigger (whichever comes first: #611 needing a real volatility measure, or the Lane 4 population pass) so it cannot drift. **AQC will still surface the branch as stale every `/checkpoint` — that signal is correct and stays; this line only suppresses the triage action.** · #641 · me

- `docs/superpowers/plans/2026-08-26-economic-strategy-remediation.md` (Program 2 strategy-economics remediation) is docs-only, explicitly self-gated: "not approved for execution... explicit human review... not a Claude-side call" · sitting untouched 9+ days, surfaced by tonight's 2026-09-06 docs/plans audit · decide execute-or-park explicitly (silent dormancy isn't a decision) · you · 2026-09-06

## Decided 2026-09-05 — implementation tracked on GitHub (remove when closed)

- `yes_ask_dollars == "1.0000"` with `yes_ask_size_fp == "0.00"` is Kalshi's no-resting-ask sentinel (live-verified; 67 of 276 watched markets right now); spread/coverage/ask-based NO valuation must treat it as *absent* at the `services/kalshi/` boundary · #606 · me
- `exit_engine/stale_price_uncorroborated` now has a tracker; `two_consumer_mode` stays a flag (it is the only control for #579's open root cause), permanence revisited when #579 closes · #607 · me
- `tools/soak_analyzer.py`: a queried-but-absent component is 0 → PASS (only a missing `by_component` dict is UNKNOWN); stall severity keys on frequency too; one generic write-fault check for `game_state`/`series_watcher`/`settlement_edge` · #608 · me
- Alerting gets an `error_fault_burst` category, edge-detected on `fault_log.summary(hours=1)` error count at the observability-window cadence, never per tick · #609 · me
- `position_netting` `locked_loss` defaults to hold-to-settlement (fee-free, no software-valued exit); early close only when headroom is measurably binding — measured $152.68 of fees across 13 groups for headroom nobody showed was binding · #610 · me
- `position_netting.normal_volatility` (0.02) is ~p97 of the live 30-min volatility distribution (nonzero median 0.0033), pinning the materiality bar to its 0.25 floor; align to `strategy.auto_exit_normal_volatility` (0.002) or share one key — config change, not touched while your safety edits are in the working tree · #611 · me
- `propagate_milestone_winners`'s event-ticker-into-market-filter defect is accepted non-functional while Sports/Politics are out of the watchlist; fix shape recorded for when they return · #612 · me
- CLAUDE.md: (1) an adversarial-review finding is a new claim and meets the evidence standard before consolidation adopts it — accepted; (2) the dimensional-analysis hook stays session-enforced by decision, not "not yet" · #613 (docs PR, own review cycle) · me
- Edge-gate enablement prerequisites: spec D1 (banded cost-aware gate diagnostic) **shipped** (PR #631 — `population_gate_summary_banded`, `population_gates_banded` on `GET /api/candidate-log/summary`, `check_gate_cost_bands` in `run_offline()`); still open: fail closed-but-counted on missing `P_pre` once `edge_gate_enabled` flips (measure the miss rate first), and advisory cost-awareness (spec D5, after the enable decision) · #616 · me
- `#532`: GO on write-time sampling of `min_contracts` at 1/100 (PR #604 implements it; still needs its own review cycle before merge); the 29.5M backlog is purged only after #604 merges, via the #578 pattern with its own checkpoint and explicit go · #532 comment · me
- Plugin pilot Task 1: GO for `pr-review-toolkit` + `claude-security`, after tonight's gate work and from one session; `claude-md-management` deferred until those report; `codspeed` declined (no external account) · #322 / #326 comments · me
- Weather-index ingestion: declined for now (ingestion with no consumer, no Climate series watched); design/plan stay valid to reopen · #331 comment; plan doc needs a "Declined 2026-09-05" header at the next board sync · me
- `whale_confidence_weights` re-validation is Task 10 (#364) on post-fix data — not a separate check; `_tied_run_size`'s two precision gaps land before that re-measurement · #364 comment · me
- "Apply suggested weights" gets a server-side 422 guard (contaminated factors / too few survivors) as a standalone change ahead of Task 10 — **until it ships, do not click Apply** (carried in `docs/next-action.md`) · #366 comment · me
- Restore the wiped `whale_confidence_weights` audit note (text in `7b91436`), fix `confidence_scoring.py:118,168`'s dangling pointer, and document the longshot fields as dormant defense-in-depth (dead under the 0.25–0.9 unit-cost band, kept because advisory can widen the band) — all in #493's PR, after the merge fix · #493 comment · me
- PR #441 follow-ups (Tasks 1–6 turned out already shipped on `main` — the tier0 branch was pushed as PR #618, proven a strict subset, closed, deleted; correction recorded on #448): **only Task 10 still open** (2026-09-06 correction, found by the lanes step-1 plans classification: Task 7 shipped — `get_faults` is `async def` at `services/diagnostics/routes.py:577-588`; Task 9 shipped — `_current_fd_count` at `:102-106`; both verified against source, the prior "7/9/10" was stale); Tier 0 items 3/5/6 are #599 (done)/#609/#527; `market_history.db` corruption watch = weekly `quick_check` in the storage scan · #448 comment (2026-09-06 correction) · me
- `record_variant()`'s per-tick sync write joins #530's sweep; PR #394's non-deterministic race test stays as-is · #530 comment · me
- Config documentation lives in the pydantic schema's field descriptions (`GET /api/config/schema`, #468), rendered by the Controls panel; YAML keeps only safety-flag comments · #468 comment · me
