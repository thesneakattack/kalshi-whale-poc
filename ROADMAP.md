# Roadmap: comprehensive, dummy-proof paper trading terminal

Guiding principle: someone who has never touched a prediction market or a
trading interface before should be able to open this app and understand
*what they're looking at*, *why it did what it did*, and that *no real money
is ever at risk* unless they deliberately configure it to be. Everything
below is measured against that bar, not against "does it technically work."

This is a living to-do list, not a snapshot — check items off in place and
add new ones as they turn up. Three docs now split the "what's next" from
"what happened":

- **This file** — forward-looking, kept short. Only genuinely open items
  live here going forward; shipped work gets a one-line pointer, not a
  narrative.
- **`static/status.html`** (`/status`) — the actively-maintained
  backward-looking record, phase by phase, with the full "why," verification
  steps, and bugs found along the way. The primary source for "what
  happened and why" for anything shipped from now on.
- **`docs/roadmap-archive-2026-08-09.md`** — a frozen, one-time snapshot of
  this file's full detail as it stood on 2026-08-09, before it was condensed
  down to the format above. Not maintained going forward; consult it (or
  `git log`/`git show` on this file) for the full narrative behind anything
  checked off before that date.

## Path to production

P0 (below) is **fully shipped** — the code-level gates around real money are
real and complete. That's necessary, not sufficient: going from "the gate
works" to "flip it for real" still has open operational questions.

- [x] Real trading gate is genuinely load-bearing (verified order schema on
      the official SDK, typed in-app confirmation phrase, restricted CORS,
      verified real account field names, kill switch + bankroll persist
      across restarts) — see P0 below.
- [ ] `services/shadow_mode.py` exists and logs what the strategy *would*
      trade against real signal data, but hasn't yet been run for a real
      evaluation stretch and reviewed — that review, not the code existing,
      is the actual "trust it" gate before ever flipping `trading_enabled`.
- [ ] This runs today only under local `ddev` on one machine — no real host,
      TLS domain, process supervisor, or uptime guarantee beyond ddev's dev
      containers. Decide and build a real deployment target before real
      capital depends on this process staying up.
- [ ] `data/*.db` (bankroll, positions, kill-switch state, signal log) is
      single-file SQLite with no backup/retention policy — fine for a local
      paper POC, not once a lost file means lost real financial state.
- [ ] No monitoring/alerting beyond watching the dashboard or `ddev logs` —
      a kill-switch trip, crash, or connectivity loss currently notifies no
      one. Worth promoting ahead of the P4 notifications item generally,
      specifically for these three cases.
- [ ] Auth is optional, single-operator Google OAuth (`services/auth.py`) —
      fine for "just me," but confirm that's still the model before real
      money sits behind it. No user table, no session-invalidation UI, no 2FA.
- [ ] `advisory.auto_apply_enabled` is deliberately unbuilt — config changes
      only happen via manual apply with an audit trail
      (`POST /api/advisory/recommendations/apply`). Keep it that way until
      there's a real track record; don't build the auto-apply endpoint as a
      drive-by later.
- [ ] Have a human, not a default, set real position-size/kill-switch
      numbers in `config/settings.yaml` before the first live dollar —
      today's defaults were picked for exercising paper-mode logic, not
      sized for real capital.
- [ ] Deep research (2026-08-09, `docs/prediction-markets-research-
      reference.md` Part 3) found **sports-category event contracts are in
      genuinely live, multi-state legal dispute** — Nevada geofencing
      sports/election/entertainment contracts by 2026-08-12, Massachusetts
      blocking sports contracts since January, several other states'
      suits unresolved and moving weekly. Election/economics contracts are
      practically settled as tradeable; sports specifically is not. This
      app has zero category-level legal-risk awareness today
      (`kalshi.categories` is a plain volume/topic filter, not a risk one).
      Before real trading is ever enabled: a real answer on category
      selection (and possibly state-of-residence) is needed, not just a
      confidence/volume filter — see
      `docs/prediction-market-strategy-alignment-plan.md` Part 6.

## P4 — Nice-to-haves

- [ ] `docs/platform-deep-scan-findings-2026-08-10.md` — 7 concrete,
      cited strategy/risk gaps found by re-reading the prediction-market
      research against the actual current engine code (edge-aware
      position sizing, cross-position concentration risk, exit-side
      analyst signal, calibration-band feedback, wash-trading detection,
      market_strategy calibration parity, time-of-day regime awareness).
      Recommended sequencing is in the doc itself — start with the
      exit-side analyst signal (cheapest) and concentration risk
      (highest safety payoff) before touching position sizing.
      **Finding 3 (exit-side analyst signal) is done** — a fourth
      `analyst_divergence` factor in `_exit_confidence()`'s composite
      auto-exit score, new `strategy.auto_exit_analyst_weight` config
      field. 4 new tests, 542 passing.
      **Finding 2 (concentration risk) is done** — shared
      `open_position_count_in_series()` helper, wired into both
      strategies' entry gates; new `strategy.max_open_positions_per_series`/
      `market_strategy.max_open_positions_per_series` config fields
      (null = unlimited). 7 new tests, 549 passing.
      **Finding 1 (edge-aware position sizing) is done** — all 3
      recommended-sequencing items now shipped. New
      `kelly_scaled_max_size()` helper + `kelly_fraction_of_cap` config
      fields (0.0 default, opt-in). 13 new tests, 562 passing. While
      consulting real data for this pass, also fixed a real crash bug in
      `confidence_calibration.py` (would KeyError against real signal
      history — ~9200 resolved signals predate 3 newer factor keys) and
      applied the 3 data-backed Advisory suggestions this app's own real
      trade history already supported. Findings 4-7 still open —
      4 (calibration-band feedback into sizing) is now unblocked, since
      calibration itself is enabled and working. One real finding
      surfaced, not yet acted on: `unusualness_factor`/`agreement_factor`
      both show *negative* discrimination against real outcomes
      (`GET /api/confidence-calibration/report`) — the opposite of their
      current positive hardcoded weights in `whale_simulator.py`. Worth a
      human look before finding 4 (or anything else) leans on those
      weights being right.
      **Acted on, same session** — the weights themselves are no longer
      hardcoded: new `whale_confidence_weights` config section, threaded
      through `composite_confidence_breakdown()` (real provider only, not
      the simulator) via a new `weights` param with partial-override
      fallback semantics. Retuned from the real finding above -
      `unusualness_factor`/`agreement_factor` cut to the 0.05 floor,
      `depth_factor`/`proximity_factor`/`context_factor` raised
      proportionally to their own real discrimination gap;
      `cluster_factor`/`trend_factor`/`analyst_factor` left untouched
      (zero real data exists for any of them yet). New read-only
      "Whale-Signal Calibration" History-tab panel — the report had zero
      UI consumer before this. Applied live via `ddev restart` (0 open
      positions at the time), not just written to disk. 11 new tests, 569
      passing. This closes finding 4 in spirit — calibration findings now
      have a real destination to act on — though editing the weights
      themselves is still config-file/API only, no dashboard form yet.
      Findings 5-7 still fully open.
      **Full `config/settings.yaml` data review, same session (2026-08-10,
      commit `20a334a`)** — went through every tunable field against real
      historical data (`signal_log`, `market_broker`/`paper_broker`
      history, `series_stats()`, the calibration report above). Applied 3
      changes with real backing: `strategy.min_resolved_for_whale_filter`
      1→10 and `advisory.min_resolved_trades_per_variant` 5→10 (both were
      un-hedged against n=1 samples), `market_strategy.stop_loss_pct`
      null→0.2 (disclosed as not yet data-calibrated — no stop_loss closes
      exist for that strategy yet). Deliberately left most other fields
      alone — `advisory_engine`'s own entry-threshold/longshot-bonus
      recommendations return `None` against real data, and most discovery
      filters (`entry_threshold`, `min_notional_usd`, spread/volume/
      momentum gates) turned out to be structurally untunable with what's
      currently tracked, not just "no strong signal yet." That
      distinction, and what would need to be built to close it, is written
      up in `docs/config-tuning-data-gaps-2026-08-10.md` — 10 concrete
      gaps (rejected-candidate/counterfactual logging, a backtest replay
      harness, per-field before/after windowing in `change_effect()`,
      per-series win-rate × qualifying-rate cross-checks, raw-signal-field
      logging, calibration-history tracking, cross-strategy comparison,
      regime segmentation, a documented sample-size convention), with a
      suggested build order.
      **Gap 1 (rejected-candidate/counterfactual logging) is done** — new
      `services/candidate_log.py`, `record_rejection()` called from every
      gate that previously only produced a boolean (`strategy_engine.py`'s
      `entry_threshold`/`min_whale_winrate_pct`, `market_strategy.py`'s
      `min_price`/`max_price`/`max_spread`/`min_volume_24h`/
      `min_seconds_to_close`/`min_momentum_delta`/
      `entry_confidence_threshold`, `kalshi_trade_tape.py`'s
      `min_notional_usd`). Resolved passively off the same `market_results`
      dict already built each tick (zero new API calls, same shape as
      `market_analyst_agent.resolve_from_market_results`). New
      `GET /api/candidate-log/summary`, a `candidate_log` Danger Zone reset
      flag, and a read-only "Rejected Candidates" History-tab panel. 14 new
      tests, 583 passing. Verified live — real rejections already
      accumulating (77 `min_notional_usd`, 11 `entry_threshold`, etc.).
      **Gap 3 (per-field before/after windowing) is done** — new
      `advisory_engine.change_effect_windowed()`, a looser measurement
      alongside the existing `change_effect()`: splits trades by
      `entry_timestamp` before/after a change's `applied_at` instead of
      requiring an exact `config_performance` fingerprint match on both
      sides, so it isn't starved by the fragmentation confirmed live (16
      `strategy.*` fingerprints, most with 0 resolved trades). Also the
      only effect measurement `market_strategy.*`/`risk.*`/etc. changes can
      ever get, since `change_effect()`'s own fingerprinting only covers
      `strategy.*`. Wired into `GET /api/advisory/applied-changes` as a new
      `effect_windowed` field; the Change History panel falls back to it,
      clearly labeled, when the strict `effect` is null. 5 new tests, 588
      passing. Verified live — several real `exit_sentiment_*` rows that
      showed `effect: null` now show a real windowed delta (50.0% n=134
      before → 100.0% n=1 after).
      **Gap 2 (stateless backtest replay) is done** — new
      `services/backtest.py`: `entry_threshold_sweep()` and
      `min_whale_winrate_pct_sweep()`, pure functions replaying a candidate
      gate value against every already-logged resolved signal. Real,
      disclosed scope boundary found while building this: `signal_log`
      never stored price/spread/volume/notional, only
      confidence/side/series/correct, so this can only faithfully cover
      `strategy.entry_threshold`/`min_whale_winrate_pct` — not
      `market_strategy.py`'s price/spread/volume/momentum gates or the
      longshot fields, contrary to this doc's own original claim that
      "signal_log already has the input data." New
      `GET /api/backtest/entry-threshold`/`.../min-whale-winrate` +
      read-only "Backtest Sweeps" History-tab panel. 16 new tests, 598
      passing. Verified live against real history (11.5k-17k resolved
      signals) — genuinely interesting finding: win rate across the
      entry-threshold sweep is roughly U-shaped (52.7% at 0.0, dipping to
      49.7% near 0.30, climbing to 58-65% above 0.45), another data point
      alongside Gap 6's still-open calibration question.
      **Gap 8 (raw signal fields) is done** — new `WhaleSignal.raw_context`
      field, populated by `kalshi_trade_tape.py`'s `fetch_signals()` at the
      exact moment each signal is created (notional/spread/volume were
      previously computed locally then discarded — only the already-
      derived factor scores got persisted). Kept separate from `factors`,
      not folded in, since `confidence_calibration.py`'s bucketing assumes
      every `factors` value is a 0-1 score. Three new nullable
      `signal_log` columns (`raw_notional_usd`/`raw_spread`/
      `raw_volume_24h`); `resolved_signals_with_factors()` now returns
      them alongside `confidence`/`correct`/`factors`. Pure data-capture,
      no new UI — zero historical rows exist under this schema until new
      signals accumulate. 7 new tests, 601 passing. Verified live via
      direct sqlite3 query — real signals already capturing raw context
      within seconds of deploy. Gaps 4-7, 9-10 still open (Gap 2's
      stateful half deliberately not attempted).
- [ ] Revisit the 5s polling model (`setInterval(refresh, 5000)`) once any
      Advanced view needs sub-poll freshness — partially addressed by an
      ETag/304 pass already shipped (an unchanged poll is now nearly free),
      so this is really a push-vs-poll latency question (WebSockets,
      considered and deferred) rather than payload waste.
- [ ] Notifications (email/push) for real trades, kill-switch triggers, or a
      tracked whale's win rate crossing the avoidance threshold — see the
      "Path to production" section above, which calls this out as worth
      promoting ahead of any real-money flip.
- [ ] Finalize the app name — "Nessie" vs. "Operation Deepscan" is still an
      open decision; once picked, flow it through page titles, headers, and
      the README.
- [ ] Sort/filter the signal feed by divergence size (bet vs. market price),
      not just recency or raw whale size — this app's central "Betting is N
      pts more bullish/bearish than the market implies" framing is already
      validated as the right idea (matches WhaleScanr/Upside's core
      approach); this would lean into it further, not replace it.
- [ ] Finish migrating the rest of the render sites (Advanced fills/orders
      tables, the screener table) onto the child-label/price-aware display
      pattern shipped 2026-08-10 for positions/market cards/the
      market-detail modal — those three call the older `marketLabel()`
      alone and still don't show `yes_sub_title`/per-leg combo data.
- [ ] Clicking a logged position/signal/decision should also show whether
      that specific position ultimately closed/won/lost, not just the
      market's current state (direct request, 2026-08-10) — needs new
      backend correlation (signal → resulting trade → outcome) that
      doesn't exist today, not just the click-to-detail wiring already
      shipped. Trading History rows already show this inline (close type +
      P&L); the signal feed and decision feed do not.
- [x] Market analyst agent's `analyze_market()` was swallowing its real
      exception (`except Exception:`, not `as e`) and returned a message
      pointing at "server logs" that didn't exist — fixed to capture the
      real error and print it (`ddev logs -s fastapi`), so that message is
      now true. 1 new test.
- [x] Danger Zone was missing a `market_analyst` reset checkbox (backend
      already supported the flag) and had no wired reset path at all for
      `market_catalog`/`market_history` — the two largest files on disk.
      `market_catalog.clear_all()` already existed unused; added the
      matching `market_history.clear_all()` and wired all three into
      `POST /api/reset` plus new checkboxes. 3 new tests.

## Shipped (condensed — see `static/status.html` and
`docs/roadmap-archive-2026-08-09.md` for full detail)

- **P0 — Safety & correctness**: all 6 gates shipped — verified real
  Kalshi field names + fixed a real 401 bug, verified/rewrote the
  `create_order`/`cancel_order` schema against Kalshi's current docs, added
  the typed in-app confirmation step before real trading can ever enable,
  paper broker + risk manager state both persist across restarts, CORS
  tightened to real origins, shadow mode built.
- **Phase 0.5 — Dashboard & UX overhaul (Kalshi Pro-inspired)**: shared
  Simple/Advanced toggle, real Event/outcome grouping, series-based market
  discovery + round-robin watchlist selection (replacing a fundamentally
  broken flat top-n browse), per-market drill-down modal (orderbook,
  candlesticks, recent trades), scoped full-exchange trade tape, Trade
  Log/Decision Feed Advanced tables, signal feed filters + enrichment,
  browsable Signal History panel, payout display, grouped positions table,
  Config-tab market search/browse, dense sortable screener table, persistent
  price-change indicators, real LIVE badge, connectivity/staleness badge,
  watchlist/pinned-markets UI, self-serve reset with granular flags,
  market-detail modal from Open Positions, `/api/state` ETag efficiency pass
  (43.8KB → 304s on unchanged polls), scrollable panels with smooth
  no-scroll-reset updates, thin scrollbars, Markets-tab search/browse.
- **Active position management & Trading History**: `close_position` +
  `check_exits` (settlement → take-profit → stop-loss → sentiment-reversal →
  auto-exit priority chain), new Trading History tab + `trade_analytics.py`,
  plus two significant bugs found and fixed along the way — a settlement
  double-inversion that silently paid $0 on an actual **no**-side win, and a
  pre-existing no-side cost-basis bug (`size * price` instead of
  `size * (1 - price)`) that had under-charged every no-side entry ever
  opened.
- **P1 — Actually dummy-proof**: Help modal + 11-term glossary, plain-English
  strategy-decision explanations, sticky real-money banner keyed off the
  field that actually gates real orders, Config tab rebuilt as ten
  collapsible plain-English accordions with a data-quality badge legend.
- **P2 — Whale-tracking maturity**: real `kalshi_trade_tape` size-based
  whale provider (now the default), wider live-only market coverage +
  incrementally-scanned market catalog, 8-factor composite confidence
  scoring (depth/context/agreement/cluster/trend/analyst + calibration
  tooling), series/category metadata + manual exclusion list, config-versioned
  performance tracking superseded by a full rule-based advisory/
  recommendation engine, `ml_feed` scaffolding for a future ML agent,
  relative/volume-weighted whale sizing, flow-clustering "Possible
  Accumulation" panel, a second whale-independent `MarketNativeStrategy` +
  real market-data history, series-level watchlist grouping (fixing a
  47-slot watchlist that was 42 golf pairings) reflected in the dashboard,
  redundant-inversion-pair collapsing, a data/presentation deep review, deep
  no-simplification prediction-market research applied across fees
  (`kalshi_fees.py`), confidence, favorite-longshot-bias-aware entry
  thresholds, and a "doctorate-level" LLM market analyst agent (on-demand,
  dual-gated, self-calibrating) — followed by a second pass applying that
  same research rigor to exits (fixed fee-blindness), the kill switch (fixed
  it only checking realized bankroll), and a foundational `equity()`
  under-reporting bug found along the way. Also: several rounds of
  "raw ticker IDs instead of titles" bug fixes (paper trade log, then the
  real connected account, then a regression from that fix pinning stale
  finalized markets into the live watchlist), and whale-notional-threshold
  retuning (including the per-series override capability that's the most
  recently shipped item, phase 63 in `status.html`).
- **P3 — Reliability & engineering hygiene**: automated test suite (400+
  tests) + CI, migration to Kalshi's official SDK, exponential backoff on
  rate limits, exchange open/closed status badge, a structural fix for
  intermittent 403/404s (API-only backend, single public entrypoint),
  mobile/responsive pass, partial accessibility pass.
- **2026-08-10 session**: Whale Watch/Markets card-mosaic page-height fix
  (a missing height bound plus threshold-based series collapsing, direct
  report — one 69-market PGA event was rendering fully expanded); market/
  position labeling fixed for three distinct root causes (child-market
  sub-titles silently discarded at the point they were computed, real
  positions had zero price and no event grouping, combo/MVE markets used a
  fragile comma-heuristic label instead of real per-leg data — the last of
  which was also a live, currently-shipping mislabeling bug, found and
  fixed) plus click-to-detail wired onto the six places that were still
  missing it (real positions, Trading History, paper Trade Log, whale
  signal feed, decision feed); a data-robustness audit fixed a live test
  that had been reading real production data, a catalog-scan bug that
  marked failed series as healthy, a backend tick-failure state that
  existed in the API the whole time with zero UI consumers, a market
  analyst agent exception that was discarded entirely with a caller
  message pointing at server logs that didn't exist, and Danger Zone
  reset gaps for `market_analyst`/`market_catalog`/`market_history` (the
  two largest data files on disk previously had no self-serve reset path
  at all); Config tab overhaul — all 55 fields across the 10 core
  accordions gained a dotted config-path chip (closing the "hint names a
  variable I can't find" gap by reusing the exact chip already shown on
  the History tab's hint panels) and a plain-English impact tooltip, plus
  3 real defects fixed along the way (`"...and"` as a whole label,
  Market-Native Strategy's exit fields far terser than their Exits-tab
  equivalents, the Risk field never using the "kill switch" term the Help
  glossary already does); a series evaluator
  (`services/series_evaluator.py`) judging whether a series is even
  "whale-worthy" before letting it back onto the automatic watchlist — a
  before/after hybrid (a cheap pre-admission backoff check, plus a real
  post-admission verdict on qualifying rate once there's trade-tape data to
  judge), with sticky approval, an escalating doubling backoff on repeated
  rejection, a new Config-tab section, and a History-tab log showing every
  series ever evaluated with a manual re-evaluate action. 33 new tests.
  Full suite: 464 (was 433). Then, first step of unifying the app's
  self-tuning subsystem: merged the old purely-descriptive "Config Tuning
  Hints" panel into Advisory Recommendations — per-field suggestions now
  read the full trade history instead of being gated to the exact current
  config fingerprint (the literal reason changing one field used to reset
  every other field's sample to zero), fixing two real bugs found along the
  way (a dropped `exit_sentiment_min_signals` suggestion, and a
  `min_momentum_delta` suggestion that turned out to be unreachable dead
  code, not just mislabeled, since it was checked against the wrong
  broker's trade log). Suggestion cards now reuse the Config tab's own
  path chips as a clickable jump-to-setting link. 466 tests. Then closed
  a real gap found in that same grounding pass: `config_performance.
  log_applied_change()` only ever fired from the Advisory apply route — a
  plain manual Config-tab save (including the real-trading enable/disable
  toggle) was never logged at all. Now every config-change source logs to
  the same audit trail (tagged by `source`), and a `strategy.*` change
  that created a new config variant gets a real measured before/after
  win-rate + realized-P&L delta once both variants have trades — reusing
  the same variant-comparison machinery cross-variant recommendations
  already use, not a new computation. New "Change History" panel on the
  History tab. 482 tests. Then extended the market analyst agent to
  analyze a whole series, not just one market — a new
  `record_series_analysis` tool schema, a separate `series_analyses`
  table (that table's schema can't share the single-market one's NOT
  NULL columns), scoped deliberately to suggesting `strategy.
  excluded_series` changes only (a per-series notional-threshold
  suggestion would need nested-dict apply logic this app doesn't have
  yet — disclosed, not silently dropped). Suggestions land in the same
  unified pool as Advisory's, tagged `source='series-analyst'`. New
  "🔎 Analyze" button on the Series Evaluator panel. 509 tests. Finally
  shipped "Feed the Analyst" — a full-spectrum scan across all config/
  history/whale data that can suggest a change to *any* config field
  (not a fixed one), so every raw suggestion is validated against the
  live config before it's appliable (must be a real existing field, must
  not be one of the two fields already protected from manual edits, must
  actually differ, must be type-compatible). Confirm()-gated given the
  cost — this is a materially bigger prompt than the other two modes.
  This completes Item 3 (the unified self-tuning subsystem) entirely —
  3A/3B/3C/3D have all shipped. 538 tests. See `static/status.html`
  phases 64-73.
