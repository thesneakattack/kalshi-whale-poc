# Documentation consolidation — 2026-08-27

**Executed 2026-09-06 — see PR #TBD.**

One-time investigation, requested directly: consolidate the loose, ad hoc
`docs/*.md` files that accumulated at the top level of `docs/` — session
pickups, findings docs, research write-ups, plans — none of which have ever
been reviewed as a batch against current code. This doc is the review. It
does **not** move, delete, or edit any source file, and does **not** edit
`ROADMAP.md` — those are deliberate follow-up actions for the user/
orchestrating session to take after reviewing this.

**Explicitly out of scope, left entirely alone per direct instruction:**
`docs/kalshi/`, `docs/superpowers/`, `docs/status-src/` (doesn't currently
exist), `docs/roadmap-archive-*.md` (three snapshots: `2026-08-09`,
`2026-08-16`, `2026-08-23` — already-deliberate frozen archives per
`CLAUDE.md`), `docs/status-archive-2026-08-26.html`. Also excluded, per the
same instruction and confirmed live/current below: `docs/woodpecker-ci.md`,
`docs/kalshi-personal-production-execution-program-2026-08-26.md`,
`docs/prediction-markets-research-reference.md`.

**Also explicitly separate:** anything about application-wide REST-vs-
WebSocket architecture belongs to a parallel investigation
(`docs/superpowers/research/2026-08-25-realtime-data-plane-known-findings.md`'s
Hypothesis H12, and a new inventory doc at
`docs/superpowers/research/2026-08-27-application-wide-rest-vs-ws-inventory.md`
— **did not exist at the time this doc was written**, confirmed via `ls`).
Findings of that shape are pulled out into their own section below and
explicitly **not** folded into the general consolidation or the draft
ROADMAP bullets.

## Methodology

Every file below was read in full, then cross-checked against current code
(`grep`/`git log -S`) rather than trusted at face value — this repo's own
`CLAUDE.md` explicitly warns against assuming a doc's self-description is
still accurate. Given the volume (20 files, ~6,300 lines, dozens of
individual findings), verification depth is proportional to how load-
bearing and how recent each claim looked: every *headline* finding and
every claim of "still open" was checked against real code; some very minor
findings (a single unused field, a UI-copy nit) are reported with the
verification the source doc itself already did, not independently
re-derived a second time.

**Big-picture result up front:** the overwhelming majority of what these
20 docs describe has since shipped. This repo has been under continuous,
heavy development since these docs were written (2026-08-08 through
2026-08-24), including two full backend modularization passes and the
realtime-data-plane remediation program — most "still open" claims from
even the most recent of these docs (2026-08-24) turned out to already be
resolved by 2026-08-27. `ROADMAP.md`'s own P4 section already independently
captured several of the same open items these docs describe (wash-trading
detection, regime-aware entry gating, the two `todo-2026-08-14` deferrals),
which this review confirms rather than duplicates.

---

## Per-file findings

### `docs/advisory-engine-plan.md`
**Covered:** the rule-based config-tuning advisory engine design (config-
variant fingerprinting, `advisory_engine.py`, three-layer safety gating,
manual-apply path, `ml_feed.py` ML scaffolding).
**Classification: 1 — fully shipped.** Every piece is live and extensively
referenced by later docs and by current code: `services/config/` (fingerprint/
`config_performance.applied_changes`), `services/advisory/advisory_engine.py`,
`/api/advisory/*` routes, `services/market_analyst_agent/` (Part 3's agent).
The one deliberate non-goal — an actual ML model consuming `ml_feed.py`'s
scaffolding — remains correctly un-built, matching this doc's own explicit
"not until the project is already finished" framing, which CLAUDE.md's
current objective still endorses.
**Recommendation:** archive/remove — nothing left to track.

### `docs/comprehensive-development-plan-2026-08-15.md`
**Covered:** a synthesis of 10 other research/planning docs (most of the
ones in this same batch), reconciled against real code as of 2026-08-15,
organized into housekeeping/small/medium/large buckets plus a sequencing
recommendation.
**Classification: 1 — fully shipped, with the synthesis method itself still
useful.** Nearly every item this doc listed as open has since shipped —
verified directly below in each item's own originating-doc row rather than
repeated here. This doc's real ongoing value was as a *reconciliation pass*
against 9 other docs; that reconciliation is now itself 12 days stale,
which is exactly what this new consolidation redoes at a further remove.
**Recommendation:** archive/remove — its findings are re-verified fresh in
this doc's own per-file rows below, so nothing is lost.

### `docs/config-tuning-data-gaps-2026-08-10.md`
**Covered:** 10 "gaps" blocking data-driven tuning of gate-shaped config
fields (rejected-candidate logging, backtest replay, config-fingerprint
fragmentation, per-series granularity, calibration-history tracking, raw
signal-field logging, etc.), each with a proposed mechanism.
**Classification: 1 — ~90% shipped.**
- Gap 1 (rejected-candidate logging) — shipped: `services/candidate_log.py`,
  `gate_summary()`, `hypothetical_win_rate`, wired into `advisory_engine.py`.
- Gap 2, stateless half — shipped: `services/backtest/backtest.py`'s
  `entry_threshold_sweep()` cites this exact gap in its own docstring.
  Gap 2's stateful half remains explicitly, deliberately deferred (as this
  doc itself recommended) — not worth a ROADMAP item on its own.
- Gap 3 (per-field windowed before/after) — shipped:
  `services/advisory/advisory_engine.py::change_effect_windowed()`.
- Gap 4 (per-series granularity) — substantially addressed via
  `config_overrides.py`'s `by_series` mechanism (real, populated entries:
  `KXBTC15M`, `KXMLBSPREAD`, `KXBTCD`); the specific "join series_evaluator
  vs win-rate" diagnostic view this gap also proposed was not independently
  verified as its own feature — low priority, not chased further.
- Gap 5 (market_strategy.stop_loss_pct calibration) — **moot**: the whole
  Market-Native strategy (`market_strategy.py`) was removed entirely,
  commit `e2dcf33`, 2026-08-23 (`ROADMAP.md` P4).
- Gap 6 (calibration-history tracking over time) — shipped:
  `services/whale_calibration/calibration_history.py`.
- Gap 7 (cross-strategy comparison) — **moot**, same reason as Gap 5.
- Gap 8 (raw signal fields) — shipped: `kalshi_trade_tape.py`'s
  `raw_notional_usd`/`raw_context`.
- Gap 9 (time-of-day/category/regime segmentation) — covered by the
  already-tracked `ROADMAP.md` P4 item "Regime-aware live entry gating."
- Gap 10 (formal power-analysis convention) — genuinely still open, but
  explicitly low-priority housekeeping in the source doc itself ("do
  whenever touching the sample-size gates for another reason"). Not worth
  its own ROADMAP bullet.
**Recommendation:** archive/remove.

### `docs/diagnostic-findings-2026-08-17.md`
**Covered:** four independent problems presenting as "bad whale signals" —
coverage (watchlist-scoped trade WS, ~2% of exchange seen), profitability
(price-band gate), runway (entries with no time to manage), and signal
density (exits can't sample-size-gate on sparse per-ticker flow); plus a
correction that signal *accuracy* itself was never broken.
**Classification: 1 — dominant findings resolved.**
- Coverage (§1, "the real fix... subscribing without market_tickers streams
  the entire exchange") — shipped:
  `config/settings.yaml`'s `trade_stream_exchange_wide: true`,
  `services/kalshi/websocket.py`'s `exchange_wide_trades` param. Commit
  `3c3ff67` in the following day's pickup doc ("exchange-wide trade
  subscription + on-demand market resolution").
- Profitability (§2, the 0.5/0.8 unit-cost band vs. live 0.3/0.95) —
  superseded by the very next day's finding (a dollar-denominated whale
  gate is itself geometrically biased toward near-certainty prices,
  `docs/next-session-pickup-2026-08-17.md`) and the subsequent
  `min_notional_usd` → `min_contracts` switch, `ROADMAP.md`'s "Whale
  threshold switched from dollars to contract count," shipped 2026-08-23.
- Runway (§3) — shipped, `strategy.min_seconds_to_close`/
  `exit_min_seconds_to_close`, already in `ROADMAP.md`'s Path-to-production
  as `[x]`.
- Density (§4) — a downstream consequence of coverage; now structurally
  improved by the exchange-wide subscription fix, not independently
  re-measured since.
- Data-integrity note (mixed-config-epoch signal_log rows) — the epoch-
  blindness half is fixed (`ROADMAP.md`'s "Make the diagnostics epoch-aware"
  item, confirmed shipped in `services/diagnostics/diagnostics.py`'s own
  comments); the *frontend* half of the fix this note points at (Danger
  Zone time-range clearing UI) is still missing — see the
  `position-management-findings-2026-08-17.md` row below, same gap.
**Recommendation:** archive/remove.

### `docs/hardening-and-accuracy-roadmap-2026-08-11.md`
**Covered:** Part 1, event-lifecycle (pre-tail/mid-series/post-tail)
activity phases; Part 2, seven "web of expertise" cross-engine wiring gaps;
Part 3, resilience follow-ups from the phase-97 incident; Part 4, general
accuracy-hardening items.
**Classification: 1 — nearly all shipped.**
- Part 1 (event-lifecycle phases) — shipped in full:
  `services/market_events/event_lifecycle.py` (`PRE_TAIL`/`MID_SERIES`/
  `POST_TAIL`, `classify_phase()`, `phase_ranked()`), wired into
  `services/market_watch/discovery_cache.py` (ranking) and `main.py`
  (`is_live` gating via `event_phase`).
- Part 2 items 1, 2, 4, 6 (candidate_log→advisory, category-conditional
  tuning, series_evaluator↔advisory, full-spectrum-context gap) — all
  shipped: `services/advisory/advisory_engine.py` has
  `_category_conditional_recommendations`/`_series_evaluator_recommendations`,
  both `candidate_log` and `regime_analytics` are read by
  `services/advisory/routes.py` and `market_analyst_orchestrator.py`'s
  full-spectrum context.
- Part 2 item 3 (regime-aware live entry gating) and item 7 (wash-trading
  detector) — genuinely still open, but **already tracked verbatim in
  `ROADMAP.md` P4** ("Regime-aware live entry gating", "Wash-trading
  detection"). No new bullet needed.
- Part 2 item 5 (market_strategy calibration parity) — **moot**,
  `market_strategy.py` removed entirely 2026-08-23.
- Part 3 (unthrottled concurrency, tick-duration/rate-limit visibility,
  `call_with_backoff` retune, WAL-mode audit) — all shipped: `tick_phase_timings`
  now exists on `/api/state`, `market_history.record_snapshots`/
  `trade_category.record_category` were both audited and fixed per later
  docs.
- Part 4 item 1 (`market_history.snapshots`' write-only columns) — status
  unchanged, but **already tracked in `ROADMAP.md`'s "Per-module
  data-consumption audit"** item, which explicitly discusses this exact
  finding and classifies it as "disclosed forward capture, not dead-code
  waste — the real gap is no consumer was ever built."
- Part 4 item 2 (margin-of-error framing on calibration auto-apply) —
  shipped (`comprehensive-development-plan-2026-08-15.md`'s own
  housekeeping list confirms this).
- Part 4 item 3 (engine-suggested per-series notional overrides) — not
  independently verified either way; low priority, not chased further.
**Recommendation:** archive/remove.

### `docs/kalshi-whale-provider-and-strategy-porting-plan.md`
**Covered:** Part 1, a real Kalshi whale-watcher provider off the public
trade tape; Part 2, a config-driven "many strategies, config not code"
framework.
**Classification: 1 for Part 1 (fully shipped), still genuinely open for
Part 2.** Part 1: `services/whalewatchers/kalshi_trade_tape.py` is now the
**default** `WHALE_WATCHER_PROVIDER` (confirmed in
`services/whalewatchers/__init__.py` and `.env.example`). Part 2
(`signal_sources.py`/`signal_aggregation.py`/`strategy_gates.py`/
`configurable_strategy.py`) is confirmed **fully unbuilt** — none of those
files exist. It's real, large, and speculative (no concrete strategy is
currently blocked on it existing), matching this doc's own "Large items"
framing 12 days later in `comprehensive-development-plan-2026-08-15.md`.
**Recommendation:** archive the file (Part 1's content is done); the Part 2
idea is worth a single low-priority ROADMAP pointer — see draft bullet #4
below — rather than resurrecting the whole doc.

### `docs/next-session-pickup-2026-08-17.md`
**Covered:** the largest file (873 lines) — the dollar-vs-contract-count
whale-threshold finding, a `fetchJSON` HTTP-status bug with real safety-UI
blast radius, a no-side-wins-display bug, a stop-loss-on-fabricated-price
incident (root-caused and fixed), a REST-vs-websocket architecture
breakdown, tick-phase timing instrumentation, a config-store atomicity
race, and a long "do these next" list.
**Classification: 1 — nearly everything shipped**, several items **already
tracked in `ROADMAP.md`**:
- Dollar→contract-count whale threshold — shipped (`ROADMAP.md`, checked
  off, "Whale threshold switched from dollars to contract count").
- `fetchJSON`/no-side-display/stale-price-stop-loss bugs — all fixed same
  session, confirmed live in the doc's own text.
- `market_lifecycle_v2` (`determined`/`settled` wiring) — shipped and
  *further corrected* 2026-08-23 (see `services/whale_stream/
  whale_stream_handlers.py::_process_stream_lifecycle`'s own docstring,
  which documents a real dispute/amendment correction on top of the
  original fix).
- Tick-phase timing (`tick_phase_timings`) — shipped, live on `/api/state`.
- Config-store atomic-write race — shipped (`services/config_store.py`).
- 4-entry gate bypass ("not yet explained" as of this doc) — root-caused
  and fixed 2026-08-22, `ROADMAP.md` checked off.
- Settlement-projection verdict — landed and acted on, `ROADMAP.md`
  checked off ("Settlement projection is a real edge, now acted on").
- Diagnostics epoch-awareness — shipped (see above).
- `GET /api/markets/search` decision-market granularity — shipped
  separately (see the `next-steps-2026-08-15-pt2.md` row below).
- "Empty feed must not look identical to a dead one" (show the rate, not
  history) — **not independently re-verified against the current dashboard
  UI**; flagged here as unconfirmed rather than asserted either way.
- Live sports game-state polling decoupled from the shared 6s cadence —
  still genuinely open, but see the REST-vs-WS section below (this is
  squarely that investigation's territory, not this one's).
- Per-category watchlist cap — still genuinely open, low priority (see
  draft bullet #6 below).
**Recommendation:** archive/remove.

### `docs/next-session-pickup-2026-08-22.md`
**Covered:** modularization Phase 7 (the `main.py` "market watch/
discovery" extraction) — the largest single remaining extraction in a
7-phase plan, plus known test fix-ups and follow-ups.
**Classification: 1 — fully shipped.** `services/market_watch/` exists as
a full package (`discovery_cache.py`, `catalog_scan.py`, `live_status.py`,
`event_metadata.py`, its own `CHEATSHEET.md`), and CLAUDE.md's own "Quick
file map" now lists `market_watch` among the Kalshi-boundary packages —
confirms this shipped and has itself since been further refactored.
**Recommendation:** archive/remove.

### `docs/next-session-pickup-2026-08-24.md`
**Covered:** the most recent doc in this batch — an in-progress close-time/
`is_live` fix (event/milestone-based occurrence resolution), plus two
"approved, not started" plans: real-account WS fill/position bugs +
trade-channel CPU quantification, and confidence-calibration series-level
scoping.
**Classification: 1 for the close-time fix and the WS message-parsing
bugs (both shipped); already tracked for the calibration item; genuinely
open for the CPU/architecture item (handed to REST-vs-WS below).**
- Close-time fix (`effective_close_time()`, `event_schedule.py` wiring) —
  shipped: `ROADMAP.md` P4 confirms "`event_schedule.py`'s resolver is now
  wired in (resolved 2026-08-24)."
- WS fill/position bugs (`market_position` singular vs. `market_positions`
  channel name; `fill_id` vs. real `trade_id` field) — **both fixed**,
  confirmed via `services/kalshi/websocket.py`'s own comments: "confirmed
  2026-08-24 building tests/test_kalshi_contracts.py (QCP Task 13)."
- Trade-channel CPU → discovery-windowed funneling + WS sharding — **still
  not done**. Instrumentation shipped (`8b227fe`) but the doc's own
  "quantify it first" instruction was never completed with a real sample,
  and the decided fix direction was never implemented. This is squarely
  REST-vs-WS/data-plane territory — see below, not folded into this
  consolidation's ROADMAP drafts.
- The larger "flip real-account state from wholesale-REST-every-tick to
  WS-primary, REST-confirm-only" request — **still not done**. Confirmed
  live: `services/position/account_positions.py::_fetch_account_snapshot`
  is still a plain interval-cached REST poll (`_ACCOUNT_SNAPSHOT_REFRESH_SEC
  = 20`), not WS-primary. The WS message-*parsing* bugs above are fixed,
  but the architectural sourcing decision itself was never revisited. This
  is REST-vs-WS territory too — see below.
- Confidence-calibration series-level scoping — **already tracked
  verbatim in `ROADMAP.md`** P4 ("`services/signal_log.py`'s
  `resolved_signals_with_factors()` still silently drops the already-
  stored `series` column").
**Recommendation:** archive/remove; the two open architectural items are
handed to the REST-vs-WS investigation, not re-added here.

### `docs/next-steps-2026-08-15-pt2.md`
**Covered:** a real live incident (signal-resolution head-of-line
blocking, a wrong rate-limit model, a resulting discovery slowdown) fully
fixed, plus three "open items" and a lower-priority carryover.
**Classification: 1 — the incident fix and two of three open items
shipped; one item is REST-vs-WS territory.**
- The incident itself (background-task decoupling, `market_catalog.
  open_candidates()`, account-snapshot interval caching) — shipped, this
  doc's own text confirms it live-verified.
- Open item 1, WS-based position/fill streaming ("the open positions
  should feed from the websocket stream...") — the three-way decision this
  doc asked for was never explicitly closed out; the underlying WS
  parsing bugs it worried about got fixed later (2026-08-24, see above),
  but the actual REST-vs-WS sourcing decision is still open. Handed to the
  REST-vs-WS investigation below.
- Open item 2, `event_schedule.py` wired-not-built — shipped, resolved
  2026-08-24 (see the `next-session-pickup-2026-08-24.md` row above).
- Open item 3, `GET /api/markets/search` decision-market granularity —
  shipped: `services/market_catalog/routes.py::search_markets`'s own
  docstring confirms it was rebuilt from a flat per-market browse into a
  series-based search specifically to fix this ("an early version...
  browsed individual markets directly... that turned out fundamentally
  unreliable").
- Carryover, `event_lifecycle.py`'s remaining 2 of 4 integration points —
  shipped, see the `hardening-and-accuracy-roadmap-2026-08-11.md` row
  above (`phase_ranked`/`classify_phase` both now wired in).
**Recommendation:** archive/remove; open item 1 handed to REST-vs-WS.

### `docs/next-steps-2026-08-15-pt3.md`
**Covered:** the tick_duration root-cause investigation (four independent
uncached/uncapped REST call sites, all fixed) plus a full Kalshi API-
documentation audit (wrong base URL, wrong endpoint for sports live data,
three batching opportunities, an overly conservative rate limiter,
informational field-deprecation notes).
**Classification: 1 — the incident and most audit findings shipped; two
audit findings are REST-vs-WS/data-plane territory.**
- tick_duration root cause (4 fixes) — shipped, this doc's own text
  confirms live verification.
- B1 (wrong base URL) — shipped.
- B2 (`get_event_live_data` wrong for Sports) — shipped:
  `services/market_watch/event_metadata.py`'s
  `_EVENT_LIVE_DATA_EXCLUDED_CATEGORIES = {"Sports"}`.
- B3, two of three batching opportunities (`get_events`, `get_live_datas`)
  — shipped: `services/kalshi/public.py` has real batched methods, used
  throughout `event_metadata.py`/`live_status.py`/`catalog_scan.py`. The
  third, larger one (`get_milestones(category=X, min_updated_ts=Y)` bulk
  sync into a local map, replacing the app's remaining per-event milestone
  round-trips) was **not found built** — no caller uses the bulk form.
  This is REST-call-volume architecture — handed to REST-vs-WS below.
- B4 (rate limiter far more conservative than the account's real budget)
  — **partially acted on, not resolved**: `services/http_client.py`'s
  `_KALSHI_READ_RATE_PER_SEC` is now `8.0` (raised from the `3.0` this doc
  found), but the doc's own measured, account-specific sustainable ceiling
  was ~20 req/sec. Whether to raise it further was never revisited with
  fresh evidence. REST-vs-WS/rate-limit territory — handed below.
- B5 (informational: `event.category` deprecation, unused Market fields) —
  no action needed, informational only.
**Recommendation:** archive/remove; B3's third item and B4 handed to
REST-vs-WS.

### `docs/next-steps-2026-08-15.md`
**Covered:** what shipped in an earlier pass (`position_netting.py`,
`config_overrides.py`, a real Kalshi fee-model gap fix) plus six
"immediate next actions."
**Classification: 1 — superseded by its own named successor doc**
(`next-steps-2026-08-15-pt2.md`'s own opening line: "That earlier doc is
now fully superseded — everything in it shipped"), confirmed independently
for the two items whose resolution actually mattered:
- Item 1, whether to enable `position_netting.enabled` — resolved:
  `config/settings.yaml` now has `position_netting.enabled: true`.
- Item 6, `market_strategy_overrides` — **moot**, `market_strategy.py`
  removed entirely 2026-08-23.
- Item 5, Kalshi's real per-series metadata (`additional_prohibitions`,
  `settlement_sources`, `contract_url`) — **confirmed still unconsumed**,
  `additional_prohibitions` in particular (a real, per-series list of who's
  restricted from trading a given contract — a genuinely different kind of
  restriction than the already-tracked sports-category legal dispute).
  Worth folding into the existing `ROADMAP.md` legal-risk item as an
  addendum rather than a new bullet — see draft addendum below.
- Items 2-4 (double-down sizing, KXMLBGAME bimodal band, periodic
  sigma-vetted re-analysis) — all explicitly deferred/contingent in the
  source doc itself, no evidence either way of being picked up since; low
  priority, not chased further given the doc's own superseded status.
**Recommendation:** archive/remove; fold the `additional_prohibitions`
finding into the existing ROADMAP legal-risk item's text (see below).

### `docs/platform-deep-scan-findings-2026-08-10.md`
**Covered:** seven gaps where an input the app already computes stops one
step short of the decision it could improve — position sizing never uses
confidence, no concentration/correlation risk check, exit logic never
consults the market analyst, calibration bands have no consumer, no
wash-trading detector, `market_strategy` has no calibration tooling, no
time-of-day/liquidity-regime awareness.
**Classification: 1 — 5 of 7 shipped, 1 moot, 1 already tracked.**
- Finding 1 (Kelly-scaled sizing) — shipped: `kelly_scaled_max_size()`,
  `strategy.kelly_fraction_of_cap` live-tuned (confirmed in
  `profit-maximization-assessment-2026-08-15.md`'s own Resolution section).
- Finding 2 (concentration/correlation risk) — shipped:
  `risk.max_open_positions_per_series` exists in `config/settings.yaml`.
- Finding 3 (exit-side analyst-divergence factor) — shipped:
  `services/exits/exit_engine.py` has a real `analyst_divergence` factor,
  its own docstring citing this exact finding by date.
- Finding 4 (calibration bands feed sizing) — not independently
  re-verified; `confidence_calibration`'s bands exist but a direct
  "band feeds Kelly sizing" consumer wasn't confirmed either way. Low
  priority (gated on real sample size per the source doc itself).
- Finding 5 (wash-trading/alternating-side detector) — genuinely still
  open, **already tracked in `ROADMAP.md` P4**.
- Finding 6 (market_strategy calibration parity) — **moot**,
  `market_strategy.py` removed entirely.
- Finding 7 (time-of-day/liquidity-regime awareness) — covered by the
  already-tracked "Regime-aware live entry gating" `ROADMAP.md` item.
**Recommendation:** archive/remove.

### `docs/position-management-findings-2026-08-17.md`
**Covered:** the volatility factor being a constant instead of a
discriminator (fixed), unused candlestick data, a four-exit-path
consolidation question, a Config-UI decimal-limit report that couldn't be
reproduced, and a long "asked for, not delivered" list.
**Classification: mixed — 1 for most, genuinely still open for two real
items.**
- Volatility-constant bug — fixed same session, confirmed in the doc's own
  text.
- **Candlestick-derived volatility — confirmed still open.**
  `services/market_watch/event_metadata.py` captures real OHLC/candlestick
  data (per this doc's own recommendation to persist it), but
  `services/market_history.py::volatility()` still computes only the
  original snapshot-delta proxy — no consumer of the candlestick data for
  volatility exists. Real, well-specified, not yet built. Draft ROADMAP
  bullet #2 below.
- **`position_netting.normal_volatility` — confirmed still untraced.**
  Still the placeholder `0.02` in `services/position/position_netting.py`
  (line ~268), never retuned against real data the way
  `auto_exit_normal_volatility` was (same session, same finding class).
  Draft ROADMAP bullet #3 below.
- Four-exit-paths consolidation — a design task, not a bug; lower urgency
  today than when written, since `take_profit_pct`/`stop_loss_pct` are
  both `null` (off) in the live config, so the "auto_exit's pnl factor
  is redundant with the hard rules" concern this doc raised doesn't
  currently apply. Not worth a ROADMAP bullet on its own.
- Config-UI decimal-limit report — could not reproduce at the time, no
  evidence of a real bug; no action needed.
- "Asked for, not delivered" list:
  - Backtesting arsenal — **partially built since**: `services/backtest/
    backtest.py` now exists with a real stateless replay function (see
    `config-tuning-data-gaps-2026-08-10.md`'s Gap 2 above), but it's a
    94-line single-function module, not the broader "arsenal" originally
    requested. Worth noting as partial progress, not worth a fresh
    standalone bullet given the scope was always large/open-ended.
  - Ignored-vs-oversensitive trade comparison — no evidence of being
    built; low priority, narrow research request, not chased further.
  - **Danger Zone frontend UI — confirmed still missing.** Backend
    (`services/reset_log.py`, `GET /api/reset/preview`, `GET /api/reset/
    history`, `range_start`/`range_end`) shipped 2026-08-16 (commit
    `f4021fd`), whose own commit message says "Backend only... frontend UI
    (scope picker, preview-before-confirm step, recent-reset-history
    display) is the next piece, not yet built." Confirmed still true today
    — `static/index.html`'s Danger Zone panel is still an all-or-nothing
    per-domain checkbox list with no time-range inputs, no preview call,
    no history display. Real, well-specified, backend-ready gap. Draft
    ROADMAP bullet #1 below.
  - `market_lifecycle_v2`/`orderbook_delta` WS channels — `market_lifecycle_v2`
    shipped (see above); `orderbook_delta` confirmed still unsubscribed
    anywhere. REST-vs-WS/data-plane territory — handed below.
- "Investigated, then left unfixed": live-status window bounds (low
  priority, not independently re-checked), four-entry gate bypass (fixed,
  see above), `position_netting.normal_volatility` (see above), advisory
  suggestion validity re-check (minor, not chased further).
- "Known unread Kalshi data" (`fee_waiver_expiration_time`,
  `settlement_timer_seconds`, `early_close_condition`,
  `latest_expiration_time`/`expiration_value`) — not independently
  re-verified; flagged here as still-plausible small gaps rather than
  confirmed, not worth a bullet without a fresh check.
**Recommendation:** archive/remove; the two confirmed-open items become
ROADMAP bullets #1-#3 below; `orderbook_delta` is handed to REST-vs-WS.

### `docs/prediction-market-bot-research.md`
**Covered:** foundational research from before this app's whale-detection
/ risk-sizing / safety architecture existed — venue comparison, Kelly
sizing formula, engineering safety requirements, a premortem.
**Classification: 1 — superseded by the built system.** Every "engineering
safety requirement" this doc lists (kill switch, position reconciliation,
paper-trade-everything, advisory-only-first) is now live and exceeded by
what actually shipped (P0 safety gates, `services/risk_manager.py`,
shadow mode). The premortem's failure modes are the same ones CLAUDE.md's
current standing goal/gaps section already tracks by name (signal
overfitting, sizing independent of confidence — resolved via Kelly
scaling, backtest-fill realism). Nothing here is wrong, it's just fully
absorbed into what got built.
**Recommendation:** archive/remove — genuinely superseded-by-completion,
not by contradiction.

### `docs/prediction-market-strategy-alignment-plan.md`
**Covered:** aligning algorithms with prediction-market research findings
(stealth-trading/clustering, fee-awareness, favorite-longshot bias,
manipulation/trend-consistency, calibration-vs-hit-rate, long-horizon
discount) plus the market-analyst-agent design (Part 3).
**Classification: 1 — ~90% shipped.** Confirmed via later docs' own
verification (`platform-deep-scan-findings-2026-08-10.md`'s header:
"Most of the alignment plan's own Part 2 findings... are already shipped
and working as designed"): cluster_factor, trend_factor, analyst_factor,
fee modeling, FLB-tiered entry threshold, `min_notional_usd`/`min_contracts`
tuning, and the market-analyst agent (Part 3) are all live. Two residual
items, both explicitly low-priority in the source doc itself:
- Maker-style real order support (§2.3) — confirmed **not** built for the
  real-account path (`kalshi_account_client.py` has no `post_only`/
  `time_in_force` handling); it *was* built for the paper broker
  (`profit-maximization-assessment-2026-08-15.md`'s Resolution section).
  Explicitly scoped as "P2 follow-up once real trading is closer" in the
  source doc — given CLAUDE.md's current paper-mode focus, still correctly
  not urgent. No new bullet.
- Long-horizon/time-value-of-money discount (§2.6) — the doc's own lowest
  priority item; no evidence of being built, no urgency change since.
**Recommendation:** archive/remove.

### `docs/profit-maximization-assessment-2026-08-15.md`
**Covered:** a real logic-hole/bug audit of the money-math/gating files,
producing four confirmed bugs (a `None`-guard crash, a real-account P&L
header scope bug, an unsignificance-tested advisory comparison, a latent
frontend no-side fallback) plus profit-maximization findings (fees eating
~60% of gross profit, Kelly sizing inert, a `market_native` confound).
**Classification: 1 — fully resolved within the same document.** This doc
contains its own "Resolution" section, written the same session, showing
all four bugs fixed and tested, all config changes applied
(`min_whale_winrate_pct`, `take_profit_pct`, `kelly_fraction_of_cap`), and
a full maker/limit-order path built for the paper broker (confirmed live
today: `frontend/src/js/config-panel.js` has a real `use_limit_orders`/
`limit_order_timeout_sec` Config-tab UI panel — the one item this doc's own
to-do list marked undone, "No dashboard Config-tab panel," has since also
shipped). The one remaining to-do item ("zero `auto_exit_pnl_weight`") is
now moot under the current live config: `take_profit_pct`/`stop_loss_pct`
are both `null` (off), so the redundancy this item worried about doesn't
currently apply — worth re-surfacing only if those hard rules are ever
re-enabled, not a standing gap today.
**Recommendation:** archive/remove.

### `docs/session-2026-08-11-whale-exit-stale-price-bug.md`
**Covered:** a session-scoped incident note — whale-follow trades
instantly stop-lossing on stale same-tick prices; fix written and
unit-tested but explicitly "not yet committed" as of the doc's own text.
**Classification: 1 — fully shipped.** `services/strategy_engine.py`'s
`check_exits()` has the `opened_since` parameter this doc describes, live
and called from `main.py` at the exact call site the doc names. This doc's
own successor (`comprehensive-development-plan-2026-08-15.md`) already
confirmed this and fixed a stale `ROADMAP.md` checkbox for it.
**Recommendation:** archive/remove.

### `docs/simmer-integration-research.md`
**Covered:** whether Simmer/agentskills.io/OpenClaw offer real leverage
for this project — concludes the "real whale data" angle is Polymarket-
specific and structurally doesn't exist for Kalshi, recommends Option A
(study only, no dependency) now, defers Options B/C.
**Classification: 1 — decision effectively made and followed.** No
evidence of `simmer-sdk` ever being added as a dependency (Option B/C never
pursued), consistent with the doc's own recommendation. The "study only"
recommendation was implicitly followed — this project's own
`agreement_factor`/`cluster_factor` concepts, cited in this doc as ideas
worth mining, are exactly the kind of pattern already live in
`whale_simulator.py`. `comprehensive-development-plan-2026-08-15.md`
already folded this doc's conclusion into its own "Explicitly deferred"
list.
**Recommendation:** archive/remove — the underlying question (should this
app become multi-venue / pursue real Polymarket whale data) remains a
real, undecided strategic question, but it's not a code gap or a stale
finding; it's a standing "not yet" that doesn't need a doc to track it
(nothing currently depends on it).

### `docs/todo-2026-08-14-heuristics-audit-and-exit-tuning.md`
**Covered:** an 8-angle audit of the heuristics/advisory subsystem plus an
exit-strategy deep dive — already self-condensed once (2026-08-23) from
405 to ~155 lines.
**Classification: 1 — fully accounted for.** The two items this doc itself
flags as still-deferred (a time-til-close exit factor; folding a
mutually-exclusive complement ticker's whale prints into sentiment) are
**already tracked verbatim in `ROADMAP.md` P4**: "Two deferred next-steps
from `docs/todo-2026-08-14-heuristics-audit-and-exit-tuning.md`: a
time-til-close exit factor, and folding mutually-exclusive-pair order flow
into sentiment analysis." Every other finding in the doc is explicitly
marked fixed, moot (Market-Native removed), or already superseded within
the doc's own condensed text.
**Recommendation:** archive/remove — this doc is now a strictly-worse
pointer to the same two items `ROADMAP.md` already names directly.

---

## Files confirmed still current — left untouched, no findings extracted

- **`docs/woodpecker-ci.md`** — active operational reference, linked from
  `CLAUDE.md`'s Quick file map. Confirmed current.
- **`docs/kalshi-personal-production-execution-program-2026-08-26.md`** —
  `CLAUDE.md`'s own current program-level sequencing document, referenced
  extensively (Standing goal section, Path-to-production section) as still
  live. Confirmed current.
- **`docs/prediction-markets-research-reference.md`** — `CLAUDE.md`'s
  "Sports-category contracts carry unresolved multi-state legal exposure"
  line and `ROADMAP.md`'s own Path-to-production section both cite this
  doc by name as the current source for that finding. Confirmed current.

---

## Handed to the REST-vs-WS / data-plane investigation, NOT folded in here

Per the user's explicit "separate... unless they apply" instruction, these
findings are real and still open, but their subject is squarely
application-wide REST-vs-WebSocket architecture — the other investigation's
territory (Hypothesis H12 / the pending inventory doc). Listed here so
that investigation doesn't have to re-derive them from scratch; **not**
turned into ROADMAP bullets by this consolidation.

1. **Real-account state (`state["account"]`) is still wholesale REST-polled
   every 20s rather than WS-primary with REST used only to confirm
   decisions.** `services/position/account_positions.py::
   _fetch_account_snapshot` remains a plain interval-cached REST poll. The
   underlying WS message-parsing bugs that were blocking a WS-primary
   design (`market_position` singular/plural, `fill_id` vs. `trade_id`)
   are fixed (2026-08-24), but the actual architectural flip was never
   done. Direct user instruction on record for this
   (`next-session-pickup-2026-08-24.md`): *"i dont need it to be wholesale
   overwritten every 6 seconds... rest api should only be used to confirm
   decisions before theyre made."* Source: `next-steps-2026-08-15-pt2.md`
   Open item 1, `next-session-pickup-2026-08-24.md` item 2.
2. **Trade-channel CPU cost → discovery-windowed funneling + WS sharding,
   decided but not implemented.** Instrumentation shipped (`8b227fe`,
   `state["trade_stream_perf"]`) but never read for a real multi-minute
   sample; the fix direction (discovery-windowed exchange-wide visibility
   + `shard_factor`/`shard_key` sharding) was explicitly decided by direct
   instruction but not built. Source: `next-session-pickup-2026-08-24.md`
   item 1.
3. **Live sports game-state polling still shares the app's general poll
   cadence** — genuinely REST-only (no WS equivalent exists per
   `docs/kalshi/`), but decoupling it from the same cadence as everything
   else, so it doesn't need sub-10s freshness the way trade signals do,
   is still open. Source: `next-session-pickup-2026-08-17.md`'s REST-cadence
   breakdown, item #4.
4. **`orderbook_delta` WS channel never subscribed to** — would replace
   `series_watcher`'s sampled top-of-book capture with true order-book
   depth. Source: `position-management-findings-2026-08-17.md`'s
   "not delivered" list.
5. **`get_milestones(category=X, min_updated_ts=Y)` bulk-sync architecture
   never built** — the third, larger of three batching opportunities found
   2026-08-15; the two smaller ones (`get_events`, `get_live_datas`
   batching) shipped, this one (eliminating the app's remaining per-event
   milestone REST round-trips via a periodic bulk local sync) did not.
   Source: `next-steps-2026-08-15-pt3.md` B3.
6. **Kalshi rate limiter still conservative relative to the account's
   measured real budget.** `services/http_client.py`'s
   `_KALSHI_READ_RATE_PER_SEC` is `8.0` today (raised from the `3.0` this
   finding originally flagged), but the account-specific measured
   sustainable ceiling (`get_account_api_limits()`) was ~20 req/sec with a
   ~3-second burst pool. Whether/how much further to raise it was never
   revisited with fresh evidence. Source: `next-steps-2026-08-15-pt3.md`
   B4.
7. **"Empty feed must not look identical to a dead one" — unverified.**
   `docs/next-session-pickup-2026-08-17.md`'s recommended fix ("show the
   rate, not the history" — surface signals/hr, time-since-last, recent
   rejection counts on an empty feed) was not independently confirmed
   either way against the current dashboard. Not strictly REST-vs-WS, but
   adjacent to real-time data-plane UX; flagged here rather than asserted
   as a ROADMAP gap without a fresh check.

---

## Draft `ROADMAP.md` bullets (case 2 — genuinely open, not yet tracked)

Matching `ROADMAP.md`'s existing P4 terse style. These are drafted for
review/paste, not applied — the user/orchestrating session should edit
`ROADMAP.md` directly.

```markdown
- [ ] **Danger Zone frontend UI was never built for the backend that's
      shipped it since 2026-08-16.** `services/reset_log.py` +
      `GET /api/reset/preview`/`GET /api/reset/history` +
      `range_start`/`range_end` on `POST /api/reset` (commit `f4021fd`)
      support time-range-scoped clearing with a dry-run preview and a real
      audit trail — the Danger Zone panel in `static/index.html` still
      only offers an all-or-nothing per-domain checkbox + "Reset Selected"
      button, no range picker, no preview step, no reset-history display.
      Confirmed still true 2026-08-27
      (`docs/position-management-findings-2026-08-17.md`,
      `docs/diagnostic-findings-2026-08-17.md`).
- [ ] **`market_history.volatility()` still uses only the snapshot-delta
      proxy, never the real candlestick data this app already captures.**
      `services/market_watch/event_metadata.py` persists real OHLC/price-
      timeseries payloads for crypto events (shipped in response to
      `docs/position-management-findings-2026-08-17.md`'s finding), but
      nothing derives volatility from them — the snapshot-delta proxy
      measured to be structurally `0.0` for ~78% of markets (same doc) is
      still the only input to `strategy.auto_exit_*`'s vol-scaling factor.
- [ ] **`position_netting.normal_volatility` (currently `0.02`) has never
      been traced/retuned against the real formula it feeds
      (`services/position/position_netting.py` line ~268)** — the sibling
      finding for `auto_exit_normal_volatility` was fixed 2026-08-17 (moved
      `0.1 → 0.002` against measured data); this config field's own formula
      was explicitly left untraced at the time ("changing it blind would be
      guessing. Trace that formula, then re-measure") and never revisited.
- [ ] Config-driven multi-strategy framework
      (`docs/kalshi-whale-provider-and-strategy-porting-plan.md` Part 2) —
      `signal_sources.py`/`signal_aggregation.py`/`strategy_gates.py`/
      `configurable_strategy.py`, letting a new strategy be a
      `custom_strategies.*` config block instead of a bespoke Python
      module. Confirmed fully unbuilt as of 2026-08-27. Large, speculative
      (no concrete strategy is currently blocked on it) — low priority,
      revisit only if a genuine second-strategy need shows up.
- [ ] Per-category watchlist cap — Sports and Crypto currently share one
      global `kalshi.watchlist_size`/`top_series_per_category`. Real gap
      per direct request ("make that highly configurable"), but
      contingent: only worth building once watchlist growth reproduces
      the 2026-08-17 blowout again, and `tick_phase_timings` (shipped
      since) now makes that easy to confirm before investing in the fix.
```

**Addendum to fold into the existing `ROADMAP.md` sports-category
legal-risk bullet** (Path to production section), rather than a new
bullet — this is a refinement of that item, not a separate finding:

> Kalshi's real per-series `additional_prohibitions` field (who's
> specifically restricted from trading a given contract — league
> employees, players, people with material non-public information, etc.)
> is a genuinely different kind of restriction than the multi-state legal
> dispute this item already tracks, and has never been cross-referenced
> against it. Confirmed still unconsumed anywhere in the codebase as of
> 2026-08-27. Source: `docs/next-steps-2026-08-15.md`.

---

## Summary

**23 files in the original `docs/*.md` top-level listing.** 3 confirmed
still-current and left untouched entirely
(`woodpecker-ci.md`, `kalshi-personal-production-execution-program-2026-08-26.md`,
`prediction-markets-research-reference.md`). **20 files reviewed in full**
against current code for this consolidation.

| Classification | Count | Files |
|---|---:|---|
| 1 — fully shipped/superseded (recommend archive) | 20 | all 20 reviewed files — every one has at least its dominant/headline finding(s) resolved; several are 100% shipped with zero residue |
| 2 — genuinely open, drafted as new ROADMAP bullets | 5 items, from 3 files | Danger Zone UI + candlestick volatility + `position_netting.normal_volatility` (all from `position-management-findings-2026-08-17.md`), multi-strategy framework (`kalshi-whale-provider-and-strategy-porting-plan.md`), per-category watchlist cap (`next-session-pickup-2026-08-17.md`) — plus one addendum to an *existing* ROADMAP bullet (`next-steps-2026-08-15.md`) |
| 3 — handed to REST-vs-WS investigation, not actioned here | 7 items, from 5 files | listed in its own section above |
| 4 — superseded/wrong/abandoned, noted with reason | 0 standalone entries | two cross-cutting supersessions instead (see below) — nothing in these 20 docs was found to be a *wrong* conclusion that needs correcting, only decisions later overtaken by a different one |

Every file lands in classification 1 at the doc level (nothing is 100% wrong
or 100% still-blocking-work), but several contain 1-3 genuinely open
sub-findings pulled out into classifications 2 or 3 above — that's why the
per-file table above states a classification *and* still lists residual
open items where they exist, rather than a single clean verdict per file.

**Two cross-cutting supersessions worth naming explicitly** (findings that
recur across multiple docs and are now uniformly moot for the same reason,
rather than a single doc being "wrong"):
- **The Market-Native strategy (`market_strategy.py`) was removed
  entirely, commit `e2dcf33`, 2026-08-23.** Every finding in this batch
  that was specifically about tuning/calibrating/extending that strategy
  (`config-tuning-data-gaps-2026-08-10.md` Gaps 5 & 7,
  `platform-deep-scan-findings-2026-08-10.md` Finding 6,
  `hardening-and-accuracy-roadmap-2026-08-11.md` Part 2 item 5,
  `next-steps-2026-08-15.md` item 6) is moot for that reason, not
  independently wrong.
- **The dollar-denominated whale threshold (`min_notional_usd`) was
  replaced by a contract-count gate (`min_contracts`), shipped 2026-08-23.**
  Several docs' specific numeric recommendations (the researched $2,500
  default in `kalshi-whale-provider-and-strategy-porting-plan.md`, the
  price-band tuning work in `diagnostic-findings-2026-08-17.md`/
  `next-session-pickup-2026-08-17.md`) are superseded by this switch, not
  wrong at the time they were written — `next-session-pickup-2026-08-17.md`
  itself is the doc that found and drove this supersession.

### Proposed action list, for the user/orchestrating session

1. **Archive or delete all 20 reviewed files.** Every one of them is
   classification 1 at the doc level; their genuinely open residue is
   already captured in this doc's draft ROADMAP bullets or handed to the
   REST-vs-WS investigation, so nothing is lost by removing the source
   files. (This consolidation doc itself is the record of what each one
   contained, per this repo's own established archive pattern —
   `docs/roadmap-archive-*.md`, `docs/status-archive-2026-08-26.html`.)
2. **Add the 5 draft bullets (plus the 1 addendum) above to `ROADMAP.md`**
   — 4 fit naturally in the P4 "Nice-to-haves" section (matching the style
   of existing entries there); the `additional_prohibitions` finding is an
   addendum to the existing Path-to-production sports-legal-risk bullet,
   not a new bullet.
3. **Hand the 7-item REST-vs-WS list above to
   `docs/superpowers/research/2026-08-27-application-wide-rest-vs-ws-inventory.md`**
   once that doc exists, or to whichever session picks up that
   investigation — do not action those items from this consolidation.
4. **Leave the 3 confirmed-current files exactly where they are** —
   `woodpecker-ci.md`, `kalshi-personal-production-execution-program-2026-08-26.md`,
   `prediction-markets-research-reference.md`.
