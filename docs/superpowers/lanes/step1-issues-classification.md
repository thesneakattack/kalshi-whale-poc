# Migration step 1 — open-issue lane classification

Date: 2026-09-06
Applies: `docs/superpowers/specs/2026-09-06-planning-lanes-design.md` (branch
`docs/planning-lanes-design`, merged as PR #640 — read from `origin/main`
where present, from the design branch where a fix-list recheck round is
newer). §3 lane table, §2 straddler clauses (a)-(d), §5 initiative-granularity
rule, §6 step 1. Cross-referenced against
`docs/superpowers/lanes/step1-plans-classification.md` (the sibling
plans-slice table) for shared vocabulary per the coordinator's cross-session
rulings (below).

Scope: all 147 currently-open GitHub issues (`gh issue list --state open`,
verified exact match against a `--jq '.[].number'` re-query — no title-embedded
newline corruption, no gap, no duplicate). No issue was labeled, closed, or
edited on GitHub. No file was moved. This table's own row order is issue
number ascending, not lane order.

Work split across 5 independently-dispatched subagents, each re-reading the
design doc directly from source (not this session's summary of it, to avoid
transcription drift) rather than trusting a shared paraphrase: batch 48-245
(36 issues), 249-367 (28), 368-470 (28), 471-542 (28), 546-641 (27). Every
subagent verified module homes via `grep`/direct source reads before citing a
lane, not from title/filename guesses — see each batch's own "files
consulted" list, preserved in the per-row reasoning below where non-obvious.

## Shared rulings applied (coordinator cross-session messages, 2026-09-06)

1. **Six-bucket status vocabulary** (`done`/`active`/`stalled`/`never-started`/
   `declined`/`superseded`) — **not applicable to this table.** Every row here
   is, by construction, a currently-open issue; a status column describing
   whether its underlying work shipped would be redundant with "open" and is
   the plans/research/specs slices' concern, not this one's. Noted here so an
   adversarial reviewer doesn't read its absence as a gap.
2. **Subject beats doc genre** (Lane 4's "research" is populated by package —
   `services/research/` — not by investigation/audit *genre*) — applied
   throughout; see #61 (Kalshi-misunderstanding retrospective → Lane 1, not
   Lane 4) and #65 (regime analytics → Lane 4 by package, not by "research"
   framing) for the clearest worked examples.
3. **First ruling on repo-structure/hygiene work, RETRACTED the same session.**
   An initial cross-session message said "cross-cutting hygiene work → Lane 9."
   That was wrong and was retracted before being applied past a draft stage:
   Lane 9 is `tools/`, `.claude/`, `.github/workflows/`, `.woodpecker/`,
   `scripts/`, `tests/`, `bench/`, `ui_samples/`, non-Kalshi top-level docs, and
   this lane system's own upkeep — nothing about `services/`. An issue moving
   or refactoring application code is application work regardless of whether
   its title uses the word "hygiene," and per §3 a multi-lane initiative with
   independent tasks splits at the boundary rather than defaulting to Lane 9.
   **Net effect on this table:** #56 (moves `paper_broker.py`/
   `strategy_engine.py` into `services/position/` et al.) is flagged RULE-GAP,
   not Lane 9 — it is a `services/`-code reorg bundled as one un-split issue,
   the same shape as the plans-slice's own G3
   (`backend-services-modularization`). #491 and #496 (DRY-fix task bundles
   spanning unrelated files in different lanes) and #530 (a repo-wide sweep
   with no anchor file) were briefly drafted as Lane 9 under the retracted
   ruling and are corrected back to RULE-GAP here, matching what the
   originating subagents found *before* the retracted ruling reached them.
   Every other Lane 9 row in this table was independently verified against
   real source (grep, direct file reads) to sit inside Lane 9's own literal
   package list or be a genuine tooling/CI/process-governance subject (audit
   *reports*, `.claude/settings.json`-only plugin-pilot tasks, `tools/`
   scripts, CI config, `tests/`-only defects) — none is an application-code
   reorg mislabeled as hygiene.

## How each column was decided

- **lane** — §2's straddler rule (base: the file/module's own declared
  purpose) plus clauses (a)-(d), or §5's initiative-primary-subject rule for
  multi-file/multi-task issues and plan-tracking issues. Never a
  file-reference count (§5 explicitly disallows that tiebreak).
- **concern** — `hotpath` where the issue's own text describes fixing,
  introducing, or measuring a sync-on-event-loop / blocking-I/O-on-a-hot-path
  defect (CLAUDE.md's data-plane HARD RULE class), applied in addition to
  exactly one lane per §3. Left blank rather than guessed where a scheduler/
  REST/dispatch touch was confirmed already-async — several rows note this
  check explicitly rather than assuming "touches I/O ⇒ hotpath."
- **reason** — the quoted or paraphrased subject phrase, source file, or
  clause citation that decided the row; left blank only where the title alone
  is unambiguous (a single explicitly-listed file, no other candidate).
- **flag** — `RULE-GAP` where no straddler clause, §5 rule, or shared ruling
  decides the row on the evidence in the issue body. A close call that *was*
  resolved by a reasoned pick (not a rule gap) is noted in the reason column
  instead (see #293) and does not carry the flag.

---

## Table (147 rows)

| Issue | Title (short) | Lane | Concern | Reason (if non-obvious) |
|---|---|---|---|---|
| 48 | shadow_mode.py never run for real evaluation | 3 | | `shadow_mode.py` explicit in Lane 3's list |
| 49 | No real deployment target beyond local ddev | RULE-GAP | | Names no file, module, or docstring at all (host/TLS/process-supervisor infra isn't represented in any of the 9 lanes' package lists). Clause (b) (shared plumbing with no single owner -> Lane 5) is the nearest structural analogy but requires an actual module whose own docstring disclaims single ownership -- there is no deployment-infra module in the codebase for that clause to read from |
| 50 | Auth model (Google OAuth) confirmation before real money | 5 | | `auth.py` explicit in Lane 5's list |
| 51 | advisory/confidence_calibration auto_apply_enabled governance | RULE-GAP | | Names two flags with equal weight and no stated primary: `confidence_calibration.auto_apply_enabled` (Lane 2) and `advisory.auto_apply_enabled` (Lane 4), owned by two entirely different modules. Clause (d)'s ordering tiebreak is defined over one module's own docstring describing two purposes of itself, not two flags owned by two different modules in two different lanes |
| 52 | Real position-size/kill-switch numbers (max_daily_loss_pct) | 3 | | `services/risk_manager.py:162` explicit in Lane 3 |
| 53 | Sports-category legal risk — no category-level awareness | 3 | | Names no code module (feature doesn't exist yet); the ask is a trading-risk policy gate, matching Lane 3's charter |
| 54 | Entry gates select worse subset than pool (pricing/edge gap) | 4 | | Title/history frame this as entry-gate (Lane 3), but the issue's own current, actionable scope is the banded-EV diagnostic on `candidate_log.py` (D1) — Lane 4 |
| 55 | Move analytics/advisory computation out of live tick loop | 4 | hotpath | `main.py`'s tick loop is unowned; tagged by what's being moved off it (analytics/advisory computation → Lane 4). Sync-on-event-loop defect |
| 56 | Move stable flat files into their concern's folders | RULE-GAP | | Application-code reorg (`paper_broker.py`→Lane 3, `strategy_engine.py`→Lane 3, `config_store.py` et al.→Lane 7), not tooling/process governance — the coordinator's corrected ruling (2026-09-06) says this splits per destination lane, but the issue is filed as one bundled, un-split unit; same shape as the plans-slice's own G3 (`backend-services-modularization`). No single-lane answer without a GitHub-side split, which is out of step-1 scope. |
| 57 | Flatten the config surface | 7 | | Subject is the config schema/surface itself, matching Lane 7's charter directly |
| 58 | Separate frontend from backend completely | 8 | | |
| 59 | Per-module data-consumption audit + report | 9 | | Goal names no single technical domain — repo-wide audit/report deliverable, matching Lane 9's process-governance charter |
| 60 | Use all available relevant data before trimming | 2 | | Concrete open item is `services/signal_log.py`'s `resolved_signals_with_factors()` — explicit in Lane 2 |
| 61 | Retrospective sweep — Kalshi API misunderstandings | 1 | | Domain explicitly named (Kalshi API/streaming-REST split) — matches Lane 1's contract-correctness charter directly, not Lane 4's "research" genre |
| 62 | Dedicated charts/graphs module | 8 | | |
| 63 | Time-til-close exit factor + mutual-exclusivity/sentiment merge | 3 | | Verified consumer for both halves is `exits/exit_engine.py`/`strategy_engine.py` — both Lane 3 |
| 64 | Wash-trading detection | 3 | | |
| 65 | Regime-aware live entry gating | 4 | | `services/history/regime_analytics.py` — Lane 4. Clause (a): module's declared advisory-only purpose keeps its lane |
| 66 | Revisit 5s dashboard polling model | 8 | | Subject is the frontend's poll cadence/push-vs-poll tradeoff |
| 67 | Notifications (trades/kill-switch/whale threshold) | 6 | | `alerting/` explicit in Lane 6's list |
| 69 | Finalize app name | RULE-GAP | | Pure branding/naming decision ("Nessie" vs "Operation Deepscan") -- no file, module, or docstring exists to anchor any straddler clause |
| 70 | Click logged position/signal to see win/loss outcome | 8 | | UI interaction; new signal→trade→outcome logic is a cross-lane dependency (Lanes 2/3/4) |
| 73 | Runway-exhausted exit mix costing real wins? | 3 | | `exits/` explicit in Lane 3 |
| 74 | Autonomous Quality Coordination (workflow-health) | 9 | | Repo workflow/CI-lifecycle tooling; `services/auth.py` change is a declared cross-lane dependent |
| 75 | Track A — Realtime data plane (lead track) | 1 | hotpath | Track A's canonical doc is the realtime-data-plane-remediation design, which §5 assigns Lane 1 (event-loop stalls on the WS/REST plane) |
| 76 | Track B — Economic strategy validity | 4 | | Concrete candidates (D1/D2) center on `candidate_log.py`/`diagnostics/` — Lane 4, not `strategy_engine.py` itself |
| 77 | Track C — Downstream production programs | 3 | | Multi-lane initiative; Programs 3R/3/4/6/8 (live-execution/shadow/capital readiness) and the initiative's own framing point to Lane 3 as primary |
| 81 | Plan: autonomous-engineering-mode.md | 9 | | Spec's own §1: workflow-automation mechanism for engineering process |
| 89 | Plan: frontend-modularization.md | 8 | | Explicit in design §5/§3 |
| 134 | Task 21: Keep queue across reconnects, generation-stamped | 1 | | Plan doc: modifies `services/kalshi/websocket.py` |
| 135 | Task 22: Critical-first waiter queues w/ background aging | 5 | | Plan doc: modifies `services/http_client.py` (`_TokenBucketRateLimiter`) |
| 136 | Task 23: Global 429 brake | 5 | | Plan doc: modifies `services/http_client.py` |
| 138 | Task 25: Shared milestone/live-data cache | 1 | | `milestone_cache.py` doesn't exist yet; per the plan, its consumers (`market_watch/`/`market_events/`) are both Lane 1 — intra-lane cache, not Lane 5 |
| 139 | Task 26: Classify streaming-off REST tape poll | 1 | | Modifies `main.py`'s call site to `_fetch_trade_tape`, home module `services/whale_stream/whale_stream_handlers.py` — Lane 1 |
| 140 | Task 27: Trigger reconciliation on reconnect/error-25 | 6 | | Primary file `services/diagnostics/trade_capture_reconciliation.py` → Lane 6; `kalshi/websocket.py`'s reconnect callback is a Lane 1 cross-lane dependent |
| 245 | series_watcher.prune() full-scans book_snapshots on event loop | 1 | hotpath | `services/series_watcher.py` explicit in Lane 1; sync-on-event-loop/blocking-SQLite defect class |
| 249 | candidate_log.DB_PATH test-isolation race | 9 | | Two `tests/` files reassign `DB_PATH` at import time; issue's own text confirms no `services/` production code involved — pure test-infra bug |
| 259 | Kalshi order auto-routing needs a real-trading decision | 1 | | `services/kalshi/orders.py` |
| 289 | Task 1: `WhaleSignal.event_ticker`, populated by real provider | 2 | | Modifies `confidence_scoring.py` + `kalshi_trade_tape.py` |
| 290 | Task 2: `Position.event_ticker` — additive column | 3 | | Modifies `paper_broker.py` (Position) + `strategy_engine.py` call site |
| 291 | Task 3: The gate — replace `me_complement` | 3 | | ~40 new lines land in `strategy_engine.py`'s `evaluate()`; `whale_stream/decision_bridge.py` (Lane 2) only loses a 2-line computation — a cross-lane trim, not new logic |
| 292 | Task 4: ME-flag on-demand ensure in the resolve path | 2 | | All new code lands in `whalewatchers/kalshi_trade_tape.py`'s `_resolve_unknown_markets` |
| 293 | Task 5: Real-pair regression, observability, docs | 3 | | Close call: split between `strategy_engine.py` (Lane 3, `me_gate_snapshot()`) and `observability/observability.py` (Lane 6, flattening). Picked Lane 3 — the produced artifact (`me_gate_unknown_total`, the pinning regression test) is the gate's own state; observability.py is just flattening already-computed Lane-3 state the way it already does for every other lane's counters |
| 315 | Worktree: `feat/candlestick-volatility` | 1 | | New `services/candlestick_volatility.py`'s own docstring: "Candlestick-derived counterpart to `market_history.volatility()`" — same Kalshi-market-data-capture purpose as `market_history.py` (Lane 1). `market_analyst_agent`/`ml_feed` (Lane 4) consumption is a declared cross-lane dependent |
| 320 | Plan: whale-confidence-scoring-remediation | 2 | | Title names the subsystem directly |
| 321 | Plan: claudesuperpower plugin pilot | 9 | | Edits only `.claude/settings.json`'s `enabledPlugins`; plan's own constraints state "`services/`, `main.py`, `config/settings.yaml` are untouched by every task" |
| 323 | Task 2: Install/pilot `pr-review-toolkit` | 9 | | Same as #321 |
| 324 | Task 3: Install/pilot `claude-security` | 9 | | Same as #321 |
| 325 | Task 4: Install/pilot `claude-md-management` | 9 | | Same as #321 |
| 327 | Task 6: Install/pilot `codspeed` | 9 | | Same as #321 |
| 328 | Task 7: Consolidate pilot results | 9 | | Same as #321 |
| 331 | Plan: weather-index-ingestion | 1 | | New `services/weather_index/` package; plan's own text: "not an extension of `services/index_feed/`... different mechanism, same conceptual role" — same Lane-1 role as `index_feed/ingestion.py` |
| 332 | Task 1: Live-verify `city` path-parameter spelling | 1 | | Verifies a Kalshi weather-index endpoint contract detail via `services/kalshi/public.py` |
| 333 | Task 2: Re-confirm city starting list vs fresh catalog data | 1 | | Queries `market_catalog.db` (Lane 1) |
| 334 | Task 3: `services/weather_index/ingestion.py` — schema/poll/upsert | 1 | | Title names the file directly |
| 335 | Task 4: Overlapping-window and revision correctness | 1 | | Test-only hardening of Task 3's `weather_index/ingestion.py` upsert |
| 336 | Task 5: Error handling | 1 | | Fault-log wrapper around `weather_index/ingestion.py`'s `poll_city` |
| 337 | Task 6: Measure real per-city REST cost | 1 | | Rate-limit/cost measurement to set the weather-index poller's interval |
| 338 | Task 7: Wire the poller into the scheduler | 1 | | Registers `_maybe_poll_weather_index` in `main.py`'s scheduler list |
| 339 | Task 8: Live verification under ddev | 1 | | Live-verifies the weather-index poller |
| 364 | Task 10: Confirm honest, tie-safe measurement is clean | 2 | | Reads `signal_log.db` / `confidence_calibration.generate_calibration_report` |
| 365 | Task 11: Config schema split + accuracy/edge rename | 2 | | Primary substance is `confidence_scoring.py`'s weight-schema split; `config/settings.yaml`/`research/research.py` touches are mechanical renames following it |
| 366 | Task 12: `measurement_is_valid` gate wired at both write paths | 2 | | New function in `whale_calibration/confidence_calibration.py`, wired into `main.py`'s auto-apply loop and `whale_calibration/routes.py`'s manual apply route |
| 367 | Task 13: `edge_score` persistence | 2 | | Additive column in `services/signal_log.py`, populated from `whale_stream/decision_bridge.py`'s `log_signal` call |
| 368 | Task 14: `stats_power.brier_score` | 4 | | |
| 369 | Task 15: `_bucket_mean_edge` | 2 | | Touches `signal_log.py` + `whale_calibration/confidence_calibration.py` — no straddle |
| 370 | Task 16: `generate_calibration_report` split | 2 | | Title names `confidence_calibration.py`'s own function; `main.py`, `research/research.py` (4), `advisory-calibration.js` (8) are cross-lane caller updates, not the primary subject |
| 386 | kanban_sync: `get_issue()` 404 mislabeling | 9 | | `tools/kanban_sync/` |
| 411 | CI discrepancy: push vs pr tests-pytest | 9 | | `.woodpecker/` |
| 412 | WS reconnect churn + slow background REST calls contributing to residual tick slowness | RULE-GAP | hotpath | Names two candidate root-cause mechanisms in different lanes (WS reconnect → Lane 1; slow REST in `background_resolution` → split `market_events/event_schedule.py` Lane 1 / `settlement_resolver.py` Lane 3) and its own "Next action" is explicitly to investigate which is real. Clause (d) is the nearest mechanism (multiple candidates, no primary) but it presumes a settled purpose stated out of order, not an open, undiagnosed question — applying it here would pick a lane off which sentence came first, not off any confirmed fact |
| 416 | `capture_writer.flush_now()` reachable sync | 4 | hotpath | Verified in source: `candidate_log.py`'s `gate_summary`/`count_range`/`clear_range`/`clear_all` call `capture_writer.flush_now()` inline with no `to_thread` offload; the analogous fix (PR #414 precedent) lands in `candidate_log.py` (Lane 4) — `capture_writer.py` (Lane 5) is the resource being called, not the fix locus |
| 447 | Worktree: chore/woodpecker-manual-trigger-workflow | 9 | | `.woodpecker/` |
| 448 | Plan: tier0-live-incident-remediation | 6 | | Multi-lane initiative; Goal names 3 Tier-0 items without marking one primary — clause (d)'s first-stated tiebreak picks "`/api/health/pipeline` hanging" → Lane 6, reinforced by Task 1 (#449) fixing exactly that route |
| 449 | Task 1: bound `/api/health/pipeline` probes | 6 | | Already offloaded via `asyncio.to_thread`; fix adds a `wait_for` timeout for route resilience, not a sync-on-event-loop fix |
| 450 | Task 2: `market_history.py` `_connect()` closes | 1 | | |
| 451 | Task 3: `title_cache.py` `_connect()` closes | 1 | | |
| 452 | Task 4: `market_catalog.py` `_connect()` closes | 1 | | |
| 453 | Task 5: `signal_log.py` `_connect()` closes | 2 | | |
| 454 | Task 6: `fault_log.py` `_connect()` closes | 5 | | |
| 455 | Task 7: `GET /api/health/faults` stops blocking loop | 6 | hotpath | Plan confirms `get_faults()` is `async def` calling `fl.summary()`/`fl.recent()` directly with no `to_thread` and no timeout — sync-on-event-loop class |
| 457 | Task 9: FD visibility + early-warning fault | 6 | | Single file (`diagnostics/routes.py`); a visibility/instrumentation add, not a blocking-I/O fix |
| 458 | Task 10: full regression suite + live validation | 6 | | "Not a code task"; Step 3 explicitly re-measures "both routes this plan exists to fix" (the Lane 6 diagnostics routes) — narrowly tied back to this plan's own single primary subject, unlike the generic `tests/`→Lane 9 default |
| 459 | pytest-xdist+testmon worker collection mismatch | 9 | | `tests/` infra |
| 462 | Task 1: T0 — Research/spec/plan/orchestrator skill | 9 | | Deliverables are planning/process artifacts (`.claude/skills/`, `docs/superpowers/*`) — none of this task's files are frontend code |
| 463 | Task 2: T1a — Guards first, stale docs | 9 | | Title states "Python/CI only; zero JS change"; files are `tools/quality_audit/`, `tests/`, CI config |
| 464 | Task 3: T1b — Runtime foundation | 8 | | |
| 465 | Task 4: T1c — Mechanical move to `legacy/` | 8 | | |
| 466 | Task 5: T2 — Bridge + pilot panel: System Health | 8 | | |
| 467 | Task 6: T3 — Tabs, poll loop, WebSocket into `core/` | 8 | | |
| 468 | Task 7: T4a — Backend `GET /api/config/schema` | 7 | | |
| 469 | Task 8: T4b — Backend `POST /api/config` validation | 7 | | |
| 470 | Task 9: T5a — Config panel: schema-driven fields | 8 | | Frontend UI panel consuming Lane 7's schema endpoint; the panel itself is Lane 8 |
| 471 | T5b — Config panel: overrides/watchlist/reset | 8 | | Plan's own file list: `panels/config/{overrides,watchlist,reset}.js`, `legacy/config-panel.js` deletion — frontend UI, not `services/config/` despite the title |
| 472 | T6 — Charts module | 8 | | |
| 473 | T7a — History core | 8 | | |
| 474 | T7b — Advisory + calibration | 8 | | |
| 475 | T7c — Regime, candidate log, backtest sweeps, series evaluator, market analyst | 8 | | Title names Lane 4 backend module names, but the task builds `panels/<name>/` frontend display panels for them |
| 476 | T8a — Header, account toggle, banners, status badges | 8 | | |
| 477 | T8b — Portfolio | 8 | | |
| 478 | T8c — Terminal | 8 | | |
| 479 | T8d — Markets + Whale Watch | 8 | | |
| 480 | T8e — Market-detail + Help modals | 8 | | |
| 481 | T9 — Cleanup, strict guards, docs sync | 8 | | Includes a ROADMAP/CLAUDE.md docs-sync step, but that's reporting the frontend initiative's own completion, not a separate concern |
| 489 | Stack-capture stall attribution in `loop_watchdog.py` | 5 | hotpath | Touches `services/loop_watchdog.py` + `fault_log.py` (both Lane 5); dispatches the fault write via `asyncio.to_thread` so the new diagnostic can't itself block the loop |
| 490 | De-poll History-tab loaders + Terminal-tab `/api/quality/summary` | 8 | | Modifies only `frontend/src/js/main.js` and `polling-and-websocket.js` — the backend route named in the title is untouched |
| 491 | Task 3: Three safety-adjacent DRY fixes | RULE-GAP | | Bundles three independently-scoped sub-fixes with no marked primary: 3a `advisory_engine.generate_recommendations()` call sites in `main.py`'s auto-apply loop + `analytics/market_analyst_orchestrator.py` (Lane 4), 3b `RiskManager.check_daily_loss`'s zero-bankroll guard in `risk_manager.py` (Lane 3), 3c canonical DDL constants shared across `capture_writer.py` (5), `series_watcher.py` (1), `candidate_log.py` (4). Clause (d) is the nearest mechanism but is scoped to one file's own docstring sentence order, not a plan's task-enumeration order across 3 unrelated files in 3 lanes — no rule decides this |
| 492 | `record_snapshot_from_ticker` off the event loop | 1 | hotpath | Edits `services/whale_stream/whale_stream_handlers.py`'s ticker handler to dispatch a sync DB write off-loop |
| 493 | `config_store.update()` stops destroying comments | 7 | | |
| 494 | One `paginate()` dependency + TTL cache for population-gate reads | 5 | | Spans 8 route files across Lanes 1/2/4/6, but the task's flagship new artifact is `services/pagination.py` (Lane 5 per §3); route call sites are cross-lane dependents |
| 495 | `event_live_data` throttle; `bump_generation()` coarsened | 5 | | `main.py`'s `_build_state_body` + `services/app_state.py`'s `bump_generation` — runtime-infra state-serving/caching |
| 496 | Task 8: `alerting.py`'s 3 discarded task handles; `http_client.py`'s timeout/limits | RULE-GAP | | Two co-equal, unrelated fixes joined by a semicolon in the title with no marked primary: `services/alerting/alerting.py` (Lane 6) and `services/http_client.py` (Lane 5, explicit in Lane 5's list). Clause (d)'s ordering tiebreak is defined over one file's own docstring text, not a task title bundling two structurally unrelated files in two lanes — no rule decides this |
| 497 | Full regression suite + live validation | 9 | | "Not a code task" — runs `pytest tests/`, `import main`, frontend build, and live before/after re-measurement across the *whole* tier1-backend-hygiene plan (a heterogeneous 8-task bundle spanning Lanes 1/3/5/6/7/8 with no single primary subject, unlike #458's narrowly-scoped equivalent) — matches Lane 9's `tests/` scope by the generic default, not overridden here |
| 513 | uvicorn --reload watcher burns CPU polling 47k files (worktrees) | 9 | | Dev-container/`ddev` file-watcher + worktree-hygiene finding; no `services/` package applies |
| 525 | Order-dependent test failures (lifecycle-resolver tests) | 9 | | Pytest test-isolation/state-leak defect across `tests/*.py`; root app-side cause not yet known, but the finding and fix target are `tests/` |
| 527 | loop_watchdog's stall sampler runs on the loop it monitors | 5 | hotpath | `services/loop_watchdog.py` |
| 528 | `_process_stream_ticker`'s market-match is a linear scan, unthrottled | 1 | hotpath | `services/whale_stream/whale_stream_handlers.py`; sync CPU on every message with no yield point |
| 530 | Systematic sweep: async routes calling sync DB with no dispatch | RULE-GAP | hotpath | Issue's own scope is explicitly repo-wide ("every route file... system-wide") and its two cited examples (`reset/routes.py`, `observability/routes.py`, both Lane 6) are explicitly disclaimed as accidental discoveries, not the named subject — the sweep hasn't been run yet, so no anchor file exists for any straddler clause to read a purpose from. Not forced to Lane 6 off the two current examples since the issue's own text says that isn't yet the real scope |
| 532 | `rejection_events` at 22.6M rows — shared root cause | 4 | | All three cited findings (`count_range()`, `population_gate_summary()`, leaking `_connect()`) are `candidate_log.py`/`candidate_log.db` — Lane 4 |
| 539 | capture_writer lock-fault rate on raw_trades ~2.1x baseline | 5 | | `services/capture_writer.py`, explicit in Lane 5 |
| 542 | trade-handler tail: 13-19s stall from awaited REST resolve in single consumer | 1 | hotpath | Root mechanism is `services/kalshi/websocket.py:1156`'s single sequential `_consume_from` loop (Lane 1), named first/most prominently in the issue's own "Mechanism" section; `_resolve_unknown_markets` (Lane 2) is a declared cross-lane dependent |
| 546 | `kalshi_trade_tape.py` dedupe-ring race (WS stream vs candidate-retry, shared `_scoring_pool`) | 2 | | Race condition in an unlocked in-memory set/deque across two thread-pool workers — a concurrency/correctness defect, not sync-on-event-loop or blocking I/O (both callers already off the event loop) |
| 549 | `fault_log._connect()` WAL-mode cold-start race silently drops a write | 5 | | SQLite WAL-conversion exclusivity race; both real callers already dispatch via `asyncio.to_thread` — a completeness defect (dropped write), not event-loop blocking |
| 563 | `_scoring_pool.py`: WS-trade-scoring and candidate-retry share one thread pool (design needed) | 2 | | Thread-pool contention/sizing question, not a sync-call-on-event-loop shape |
| 579 | Trade-class ingest-queue drops permanently lose gate-surviving whale prints | 1 | | `asyncio.QueueFull` backpressure/capacity loss — a completeness violation |
| 580 | `settlement_resolver` pending backlog grew 22x overnight | 3 | | Backlog/throughput tied to a shared 2-worker `tick_executor` pool (contention hypothesis) |
| 584 | Kill switch non-functional: daily-loss baseline re-based to negative bankroll | 3 | | `risk_manager.py` |
| 589 | `market_history.snapshots`: `price_source` provenance column deferred | 1 | | About the capture-path (`record_snapshots`), not the `compute_hypothetical_trades()` secondary use |
| 590 | `price_fabrication` CI guard misses ternary/intermediate-variable shapes | 9 | | `tools/quality_audit/price_fabrication.py` |
| 591 | Unexplained YES-side auto-exit profit: headline figure wrong ($60,276 not $68,589) | 3 | | Spans `paper_broker.py` and `exits/exit_engine.py`, both Lane 3 |
| 599 | `/api/health/faults`: legacy fault signature's all-time count leaks into narrow windows | 5 | | Straddles `fault_log.py`'s own aggregation logic (Lane 5) and its exposure via `diagnostics/routes.py` (Lane 6). Assigned Lane 5: the defect is in how a fault's `count` is aggregated by window — `fault_log.py`'s own domain (corroborated by #608's neighboring aggregation bug in the same file); the route is a thin pass-through |
| 605 | Live event-loop stall escalation observed 2026-09-05 (up to 9.6s) | 5 | hotpath | Direct live measurement of the sync-on-event-loop symptom via `loop_watchdog.py` |
| 606 | `yes_ask_dollars "1.0000"` is Kalshi's no-resting-ask sentinel | 1 | | |
| 607 | `exit_engine` `stale_price_uncorroborated` fault has no tracking issue; `two_consumer_mode` flag stays | 3 | | Bundles three decisions (Lane 1 flag, Lane 3 `exit_engine` fault, Lane 9 `soak_analyzer` gate); assigned Lane 3 because decision 2 explicitly states "this issue tracks" the `exit_engine` fault — its own stated tracking purpose |
| 608 | `tools/soak_analyzer.py`: zero-fault components read as UNKNOWN | 9 | | `tools/soak_analyzer.py` |
| 609 | `alerting.check_and_alert` never reads `fault_log` | 6 | | Explicitly designs the new alert to avoid a per-tick synchronous `fault_log.summary()` call — a preventive design note, not a fix for an existing hotpath defect |
| 610 | `position_netting` `locked_loss`: default to hold-to-settlement | 3 | | |
| 611 | `position_netting.normal_volatility` (0.02) misaligned with `auto_exit_normal_volatility` (0.002) | 3 | | Both consumers live in `services/exits/`; `market_history.volatility()` (Lane 1) is only the shared read |
| 612 | `propagate_milestone_winners` feeds EVENT tickers to a MARKET-ticker filter | 1 | | |
| 613 | CLAUDE.md: adversarial-review findings are claims; dimensional-analysis hook stays session-enforced | 9 | | CLAUDE.md process-rule decision record |
| 615 | `main` branch protection is OFF (Free-plan private repo) | 9 | | CI/branch-protection process decision record |
| 616 | Edge-gate enablement prerequisites (D1 banded diagnostic, fail-closed P_pre, D5 advisory cost-awareness) | 3 | | Multi-lane initiative (`strategy_engine.py` Lane 3, `candidate_log.py`/`advisory_engine.py` Lane 4 dependents); primary subject named in the title is edge-gate enablement itself → `strategy_engine.py` |
| 619 | `market_analyst_agent/per_market.py`: identical unfiltered-scan bug as #601 | 4 | | Unfiltered/N+1 query-shape bug already dispatched off the event loop via `tick_executor`'s worker thread — an efficiency bug, not the undispatched-sync-call class #626/#639 show |
| 621 | `series_evaluator.record_trades_observed_bulk()` never called | 1 | | |
| 626 | Backtest entry-threshold route: `entry_threshold_sweep()` itself still un-offloaded (~1.1s) | 4 | hotpath | Same PR/route as #624 (Lane 4); this is the un-offloaded remainder of that exact fix |
| 634 | Event-loop stall: FastAPI `jsonable_encoder` recursion, route unidentified | RULE-GAP | hotpath | Captured stack trace is pure FastAPI/Starlette framework internals with "no application-level frames"; the one named candidate (`GET /api/state`, in `main.py`) is explicitly disclaimed as "an untested hypothesis, not a finding." No confirmed file exists for any straddler clause to read a purpose from — a genuine information gap, not a judgment call between two known candidates |
| 639 | `candidate_log.gate_summary()` called synchronously from other async/hot-path contexts | 4 | hotpath | Multi-file (`advisory/routes.py`, `analytics/market_analyst_orchestrator.py`, `main.py`'s auto-apply loop), but every call site resolves to Lane 4 |
| 641 | `feat/candlestick-volatility`: re-implement against main, don't rebase | 4 | | Issue self-declares "Lane: 4 (analytics/advisory/research)" with Lane 1/Lane 2 as cross-lane dependents |

---

## Summary

### Counts per lane (138 lane-bearing rows + 9 RULE-GAP)

| Lane | Name | Count |
|---|---|---|
| 1 | Kalshi & index data ingestion | **28** |
| 2 | Whale signal detection & calibration | **13** |
| 3 | Strategy, risk & execution | **17** |
| 4 | Analytics, advisory & research | **11** |
| 5 | Runtime infrastructure | **12** |
| 6 | Observability, quality & safety infra | **8** |
| 7 | Config & control plane | **4** |
| 8 | Frontend & dashboard | **22** |
| 9 | Tooling, CI & process governance | **23** |
| — | RULE-GAP (no lane assigned) | **9** |
| | **Total** | **147** |

Lane 8 (22) and Lane 9 (23) together are almost a third of all open issues —
Lane 8's count is dominated by the frontend-modularization plan's per-task
issues (#464-#481, #490), and Lane 9's by CI/tooling/test-infra findings and
the plugin-pilot's per-plugin tasks (#321-#328). Lane 7 has the fewest (4).

### `concern:hotpath` — 16 of 147 issues (10.9%)

#55, #75, #245, #412, #416, #455, #489, #492, #527, #528, #530, #542, #605,
#626, #634, #639. Every row's own reasoning states explicitly why the tag was
or was not applied where the call was non-obvious (several REST/scheduler
touches were confirmed already-async and left untagged rather than assumed).

### RULE-GAP rows: **9**

| Issue | Gap |
|---|---|
| #49 | No deployment-infra module exists anywhere in the codebase for any straddler clause to read a purpose from; clause (b)'s shared-plumbing analogy needs an actual module and has none here. |
| #51 | Two flags, two owning modules, two lanes (2 and 4), no marked primary — clause (d)'s ordering tiebreak only resolves two purposes *within one module's own docstring*, not two flags owned by different modules. |
| #56 | `services/`-code reorg (`paper_broker.py`/`strategy_engine.py`→Lane 3, `config_store.py`→Lane 7) bundled as one un-split issue. Per the corrected shared ruling this splits per destination lane, but no GitHub-side split exists yet — same shape as the plans-slice's own G3. |
| #69 | Pure branding/naming decision with no file, module, or docstring to anchor any clause. |
| #412 | Two hypothesized root-cause mechanisms (Lane 1 WS reconnect vs. Lane 1/3 REST latency) for an explicitly not-yet-diagnosed symptom; the issue's own "Next action" is to determine which is real. Clause (d) needs a *settled* purpose stated out of order, not an open question. |
| #491 | Three independently-scoped DRY sub-fixes spanning Lanes 3/4/5 with no marked primary; clause (d)'s tiebreak doesn't reach across three unrelated files in three lanes. |
| #496 | Two unrelated fixes (Lane 5, Lane 6) joined by a title semicolon with no marked primary; same shortfall as #491. |
| #530 | Explicitly repo-wide, not-yet-run sweep; its two cited examples are disclaimed in the issue's own text as accidental, not the real scope — no anchor file exists for any clause. |
| #634 | Stack trace is pure framework internals with zero application frames; the one named candidate route is explicitly disclaimed as an untested hypothesis. A genuine information gap, not a two-candidate judgment call. |

### Close calls resolved by reasoned pick (not RULE-GAP)

- **#293** — splits fairly evenly between Lane 3 (`strategy_engine.py`) and
  Lane 6 (`observability/observability.py`); resolved to Lane 3 because the
  produced artifact is the gate's own state and observability.py is only
  flattening it, per the same pattern every other lane's counters already
  follow. Flagged transparently in its own row rather than forced silently.
- **#458 vs. #497** — both are "full regression suite" plan-closeout tasks,
  classified differently (Lane 6, Lane 9) on purpose, not by inconsistency:
  #458's own text ties its re-verification narrowly back to "both routes this
  plan exists to fix" (a single-lane plan), while #497's text explicitly
  covers "the whole plan" (an 8-task, 6-lane heterogeneous bundle with no
  single subject to re-verify against) — the generic `tests/`→Lane 9 default
  applies to #497 and is overridden for #458.

### Issues that could not be classified at all

**None.** All 147 rows carry a lane or the RULE-GAP flag, and every
`concern:hotpath` call is stated with its own reasoning. Nine rows (6.1%)
carry RULE-GAP; the rest were decided by the base rule, a straddler clause, or
§5's initiative-subject rule, each cited per-row above.

### Note for the coordinator's own plans-slice table

Two of this table's RULE-GAP rows (#56, and by extension #491/#496's shape)
are the same defect class as the plans-slice's own G3
(`2026-08-27-backend-services-modularization.md`, "no lane owns repo
structure") and G4 (`2026-09-03-tier1-backend-hygiene.md`, "clause (d)
resolves it by audit-list ordering, i.e. by accident") — both landed on
`main` under the now-retracted Lane-9 framing's predecessor reasoning (Lane 5
via clause (d), not Lane 9, so they were not directly affected by the
retraction, but the same underlying gap is visible in both tables). Worth a
joint look before step 2 sizes any Lane 9 or Lane 5 labeling batch, since both
tables independently found the design has no mechanism for "one bundled
initiative, several inseparable application-code lanes, no named primary."

Self-review is its own companion document:
`docs/superpowers/lanes/step1-issues-classification-self-review.md`.
