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
- [ ] `advisory.auto_apply_enabled`/`confidence_calibration.auto_apply_enabled`
      shipped 2026-08-10 (direct request), off/on by default respectively,
      typed-confirmation gated, with their own sample-size floors on top of
      manual-apply's bar (`advisory.auto_apply_min_n`,
      `confidence_calibration.auto_apply_min_resolved_signals`, both
      2026-08-11 hardening) and a full audit trail either way
      (`config_performance.applied_changes`, `source`-tagged). Real-money
      readiness is still an open question independent of the code, though:
      this has only ever run against paper-mode trade history — confirm it
      should stay on (or get a stricter/zero floor) before real capital is
      ever behind the config it's tuning.
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
- [ ] `docs/hardening-and-accuracy-roadmap-2026-08-11.md` — direct request
      after the phase 97 trade-tape incident, covering four areas: (1)
      event-lifecycle awareness (pre-tail/mid-series/post-tail activity
      phases) for watchlist ranking, `series_evaluator`'s verdict window,
      and whale-confidence scoring — a real screenshot (a months-out
      tournament-champion futures market with vol-5 outcomes) confirmed
      the current 24h-cumulative-volume ranking has no idea an event
      hasn't started yet; (2) a "web of expertise" cross-engine audit,
      direct instruction — every pairing checked against the actual code
      (not assumed), confirming 6 real gaps: `candidate_log`'s rejected-
      candidate data never reaches `advisory_engine`, no category-
      conditional tuning exists despite the data already being collected,
      `regime_analytics` is read-only/never fed into live entry gating,
      `series_evaluator` verdicts never reach `advisory_engine` despite
      the disagreement already being surfaced read-only, `market_strategy`
      has zero calibration tooling, and the full-spectrum LLM scan is
      missing two datasets it could cheaply include; (3) resilience
      follow-ups the incident exposed but didn't fix (the same unthrottled
      per-ticker concurrency pattern exists in three other fetch loops, no
      visible slow-tick/rate-limit indicator, other high-frequency write
      paths not yet audited for the same blocking-I/O risk); (4) smaller
      accuracy items (write-only `market_history` columns, no margin-of-
      error framing on calibration auto-apply, no engine ever suggests a
      per-series notional override despite having the data to). Sequencing
      recommendation is in the doc itself.
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
      within seconds of deploy.
      **Gap 4 (series_evaluator × win-rate cross-check) is done** —
      diagnostic-only, no new persistence. `GET /api/series-evaluator/
      status` now joins `series_evaluator.overview()`'s qualifying-rate
      verdict against `signal_log.all_series_stats()`'s real win rate
      (Gap 2's bulk query) inline, attaching `whale_win_rate`/
      `below_winrate_floor` per series — the latter mirrors
      `strategy_engine.py`'s real `min_whale_winrate_pct` gate comparison
      exactly. Surfaced a real disagreement immediately on live data:
      `KXATPCHALLENGERMATCH` is series-evaluator-approved for the
      watchlist but `below_winrate_floor: true` (34.4% win rate, n=2326) —
      the *other* gate is already silently filtering its real trades even
      though this one approved it. Series Evaluator panel now shows win
      rate inline, flagged when below the floor. No new tests (thin
      inline join over two already-tested functions); verified live via
      curl + selenium-chrome. 601 tests passing, unchanged.
      **Gap 6 (calibration-history tracking) is done** — new
      `services/calibration_history.py`, own SQLite file. A cheap
      `due()` MAX()-query check runs every tick; only when a snapshot is
      actually due (default every `confidence_calibration.
      snapshot_interval_sec` = 6h, new config field) does the expensive
      full-table-scan report computation run, then `record_snapshot()`
      persists overall win rate, per-factor gaps, and the live weights.
      New `GET /api/confidence-calibration/history` + a `calibration_
      history` Danger Zone reset flag. History table added to the
      Whale-Signal Calibration panel, hidden until ≥2 snapshots exist (one
      point can't show a trend). 8 new tests, 609 passing. Verified live —
      the very first snapshot recorded automatically within one tick of
      deploy (11,859 resolved signals, 52.6% overall win rate).
      **Gap 7 (cross-strategy comparison) is done** — new
      `services/cross_strategy.py`: `aggregate_comparison()` (both
      strategies' `compute_summary()` side by side, reused as-is) and
      `ticker_overlap()` (every ticker where whale-follow and
      market-native independently opened a position — two fully separate
      capital pools/gates/entry logic — with `agreed`/win/loss per side).
      New `GET /api/cross-strategy/comparison` + a new History-tab panel.
      Real live numbers: whale-follow 50.4% win rate/135 closed vs.
      market-native 13.6%/22 closed — no ticker overlap yet (honest empty
      state, not fabricated). Found and fixed a real double-escaping bug
      live during verification (`esc()` called on an already-HTML-escaped
      label string). 7 new tests, 616 passing. Verified via curl and
      selenium-chrome.
      **Gap 9 (time-of-day regime segmentation) is done, partially** —
      disclosed scope: hour-of-day/day-of-week only, both derivable
      directly from `entry_timestamp`. Category segmentation (the other
      half this gap and deep-scan Finding 7 both name) is NOT attempted —
      `market_catalog.category` is watchlist-scoped and rotates, so it
      would need a new category-snapshot-at-entry-time persistence layer,
      not just an aggregation pass. New `services/regime_analytics.py`:
      `by_hour_of_day()`/`by_day_of_week()`, both reusing
      `trade_analytics.compute_summary()` per bucket. New
      `GET /api/regime/by-hour`/`.../by-day-of-week` + a new "Regime
      Segmentation" History-tab panel (whale-follow only — market-native's
      22 closed positions are too thin to segment further). 8 new tests,
      624 passing. Verified live — real variation across hours (e.g. 18:00
      UTC: 70.0% win rate n=20, vs. 20:00 UTC: 23.1% n=13).
      **Gap 10 (documented sample-size/power convention) is done** — new
      `services/stats_power.py`: `margin_of_error_pts()`/
      `min_n_for_margin()`, the real normal-approximation (Wald) margin-
      of-error math behind "is n big enough to trust this," replacing this
      session's own "eyeballing `sqrt(p(1-p)/n)` by hand." Doesn't rewrite
      any existing ad hoc threshold (`confidence_label`'s 5/15,
      `min_resolved_trades_per_variant`'s 10, etc. — each was chosen for
      its own local reason) — documents them instead:
      `trade_analytics.confidence_label()`'s docstring now states the real
      margin at n=5 (~±44pts) and n=15 (~±25pts) at a 50% base rate, both
      genuinely wide. Wired into one concrete consumer: Gap 4's
      series-evaluator win-rate cross-check now shows each series' real
      margin of error next to its win rate (e.g. "34.4% ±1.9pts"). 11 new
      tests, 635 passing. Verified live via curl (real margins, no
      `Infinity`-in-JSON risk — confirmed structurally unreachable given
      the call site's own gating) and selenium-chrome.
      **This closes all 9 buildable gaps from docs/config-tuning-data-
      gaps-2026-08-10.md** (Gap 5's stop-loss calibration was never on the
      build list — it needs time with the field live, not tooling; Gap 2's
      stateful replay half was explicitly deferred within Gap 2 itself).
      **Part 2 ("web of expertise" cross-engine audit) is done — all 6
      confirmed gaps shipped, plus a real bug found along the way
      (2026-08-11).** Item 6 (full-spectrum LLM context): `_build_full_
      spectrum_context()` now includes `rejected_candidate_gates`
      (`candidate_log.gate_summary()`, reused from an already-computed
      local var — no duplicate DB call) and `regime_by_category`/
      `regime_by_hour` (`regime_analytics.by_category`/`by_hour_of_day`).
      Item 1 (rejected-candidate counterfactuals → `advisory_engine`): new
      `_rejected_candidate_recommendations()` — a `(strategy, gate_name)`
      →`(config_path, direction)` map covers 9 real gates; suggests
      loosening a threshold when ≥5 rejected candidates would have won.
      Item 4 (`series_evaluator` verdicts → `advisory_engine`): new
      `_series_evaluator_recommendations()` suggests adding a series to
      `strategy.excluded_series` when its win rate sits below the floor
      with real sample size — confirmed live against a real
      `KXMLBGAME` exclusion suggestion. Item 2 (category-conditional
      tuning): new `strategy.entry_threshold_by_category` override dict
      (mirrors the existing `min_notional_usd_by_series` precedent),
      `FollowTheWhaleStrategy.evaluate()` now takes an optional `category`
      param read from `state["event_titles"]`, and
      `_category_conditional_recommendations()` suggests per-category
      overrides off ≥5-sample category win-rate gaps. Item 3 (regime-aware
      *live entry gating*, as opposed to Item 2's threshold-only wiring) is
      deliberately scoped out of this pass and left open — `regime_
      analytics` stays advisory-only for now, disclosed rather than
      silently dropped, given the genuine plumbing complexity and the
      direct instruction to not risk the live trading path twice in one
      pass. Item 5 (`market_strategy` calibration parity): new `services/
      market_strategy_calibration.py` — confidence-band calibration only
      (not per-factor discrimination, disclosed in the module's own
      docstring: market_strategy has no per-candidate factor persistence
      like `signal_log.factors_json`, only a placed trade's blended score
      via `trade_analytics.build_trade_history()`'s `entry_confidence`
      field), new `market_strategy_calibration.{enabled, min_resolved_
      trades}` config, `GET /api/market-strategy-calibration/status`/
      `.../report`. Verified live against real data (n=80): a genuinely
      useful finding on its first run — every confidence band's observed
      win rate sits far below its predicted midpoint (e.g. 90-100%
      predicted 95%, observed 26.7%), meaning market_strategy's composite
      confidence score is currently a poor predictor of its own outcomes —
      flagged here, not acted on, since this tool's job is exposing that
      gap, not auto-correcting it. **Real bug found and fixed in the same
      pass, direct report** ("apply button gives the same suggestion
      again immediately"): every advisory suggestion function recomputed
      from full trade history on every call with no awareness a
      config_path had just been changed, so clicking Apply repeatedly with
      no new trades in between kept re-suggesting the same nudge off
      stale evidence. Fixed with `_drop_stale_recommendations()`, reusing
      the existing `entry_timestamp`/`applied_at` before/after convention
      from `change_effect()` rather than inventing a new one — a
      suggestion is now dropped if `config_performance.
      all_last_applied_by_path()` shows its `config_path` was changed more
      recently than the newest trade behind the suggestion. 86 new tests,
      722 passing. Verified live end-to-end via curl (real recommendations
      including rejected-candidate and series-evaluator suggestions
      appearing in `GET /api/advisory/recommendations`) and
      `selenium-chrome` against the Whale Watch terminal specifically, per
      direct instruction — 20 real market cards, whale signal breakdowns,
      and trade tape all rendering correctly, confirming the trade-tape/
      SQLite hardening from phase 97 wasn't disturbed by this pass.
- [x] **Daily-loss kill switch never actually rolled over daily — three
      real, connected bugs found and fixed in one investigation (2026-08-
      10)**, triggered by a direct report ("market-native strategy seems
      to have stalled"). Root cause: `reset_day()` was only ever called
      manually (`POST /api/reset`, or a dashboard halt/resume click) —
      nothing rolled the baseline over at a real day boundary, so once a
      kill switch tripped it stayed tripped indefinitely.
      `market_strategy.py`'s own risk manager had tripped
      (`-43.1%`) and sat halted for 55+ hours with zero recovery path — it
      wasn't stalled, it was correctly, silently obeying a kill switch
      nothing had ever cleared. `services/risk_manager.py`'s
      `check_daily_loss()` now runs an automatic rollover first (new
      `day_start_date` column, once per real UTC calendar-date change, not
      once per tick) — every existing call site gets this for free.
      `services/shadow_mode.py` had the exact same bug, independently
      confirmed live (`-99.7%`, dormant only because `mode` was `paper` at
      the time) and fixed the same way, per direct follow-up ("i think the
      same or similar problem is happening in shadow mode"). New
      `POST /api/market-risk/halt`/`.../resume` and
      `POST /api/shadow-risk/resume` (neither had any route before this),
      plus a `market_native` `POST /api/reset` flag. All three trackers
      manually un-halted live as part of this fix; market-native confirmed
      evaluating real candidates again within a minute. Separately found
      and fixed while investigating: the Portfolio Trade Log (both Simple
      and Advanced views) never showed a closed position's real outcome —
      every row rendered identically whether still open or already
      settled, showing "cost to enter"/"payout if right" even for an
      already-won-or-lost trade (direct report: "not seeing the results of
      the positions in the trade log"). New `main.py`
      `_enrich_recent_trades()` re-derives `close_type`/`realized_pnl`/
      `won` via the existing `trade_analytics.build_trade_history()` over
      the full trade log (not just the displayed tail-25, so pairing stays
      correct) and merges it onto both brokers' `recent_trades`; the
      Advanced table's P&L column also stopped using a mark-to-market
      formula that was meaningless for a closed trade. 14 new tests (649
      passing). Also cleaned up 9 stray `TICK-A` test-fixture rows a
      pre-isolation-fixture pytest run had written into the real
      `data/candidate_log.db` earlier this same session.
- [x] **History tab panels never auto-refreshed while the tab stayed
      open (2026-08-10)** — direct report: "the advisory recommendations
      seem out-of-date, and should update." Root cause:
      `loadTradingHistory()` was deliberately only ever called once, on
      tab-open, to protect the paginated Trading History table's own
      paging/sort state from a 5s poll reset — but that meant *every*
      panel on the tab (Advisory, Change History, Calibration, Cross-
      Strategy, Regime Segmentation, Rejected Candidates, Backtest
      Sweeps, Series Evaluator, Market Analyst) inherited the same
      staleness even though none of them have any pagination of their
      own to lose. New `refreshHistoryInsightsIfActive()`, called from
      the existing `refresh()` 5s poll, re-fetches just those panels —
      the paginated trade-log table is untouched, still tab-open-only.
      Verified live: intercepted `fetch()` and confirmed
      `/api/advisory/recommendations` refetches every poll cycle while
      the tab is open, and confirmed Trading History's own
      `historyFilter.offset` (paging position) survives an 11s wait
      completely unchanged.
- [x] **Dedicated Market-Native tab (2026-08-10, direct request)** — its
      own view, entirely separate from the whale-follow Portfolio tab:
      status/bankroll/equity/unrealized-P&L stat cards, a halt banner
      with a one-click resume when its kill switch is tripped, open
      positions, a trade log (real won/lost results via the same
      `_enrich_recent_trades()` the Portfolio fix above added), and its
      own decision feed. That last one closed a real gap found while
      building this: `market_strategy.evaluate_all()`/`check_exits()`'s
      return values were computed every tick and silently discarded —
      new `state["market_decision_feed"]` (deliberately kept separate
      from whale-follow's own `decision_feed`, matching the existing
      "each strategy's performance independently measurable" principle).
      `GET /api/market-strategy/state` extended with `decision_feed`/
      `market_titles`/`latest_prices`. Single-density, no Simple/Advanced
      toggle — a disclosed smaller scope than Portfolio's own depth,
      matching this strategy's much lower trade volume. Verified live:
      confirmed the halt banner renders with the real live reason
      (market-native's kill switch had actually tripped *again* since
      being un-halted a few minutes earlier, at `-21.3%` this time — a
      fresh, genuine trip consistent with its known ~13.6% win rate, not
      a recurrence of the rollover bug), positions/trade-log render
      correctly against real data, and double-checked what looked like a
      P&L discrepancy (Unrealized P&L showing $0.00 next to a $423
      equity-bankroll gap) — confirmed correct, not a bug:
      `equity() = bankroll + cost_basis + unrealized_pnl` by design, and
      the $423 gap was exactly the two open positions' cost basis, with
      `unrealized_pnl` legitimately at 0 since no live price is currently
      cached for those tickers (falls back to entry price, same
      documented convention as everywhere else in this app).
- [x] **Auto-apply for whale-signal calibration weights (2026-08-10,
      direct request), plus a real bug found and fixed along the way** —
      `advisory.auto_apply_enabled` was already protected from generic
      config edits with an error message pointing at `POST /api/advisory/
      auto-apply/enable`/`.../disable`, but those routes never existed
      and nothing anywhere read `auto_apply_min_confidence`/
      `auto_apply_cooldown_sec` either — the feature was reachable from
      no path at all. Fixed alongside building the equivalent for
      `confidence_calibration`. Both now use the same typed-confirmation-
      phrase gate as real trading (asymmetric — disabling needs no
      phrase). New `services/confidence_calibration.blended_weights_
      for_auto_apply()` automates the exact blend a human did by hand
      earlier this session (redistribute only factors with real
      discrimination data, leave data-less factors completely untouched,
      renormalize the whole set to sum to 1.0) — this reverses that
      module's own earlier-documented "read-only, a human must apply
      this by hand" decision, deliberately, at direct request, with the
      same opt-in/confirmation-gated/cooldown safety rails as everything
      else. New `config_performance.last_applied_at(source)` for the
      cooldown check (reuses `applied_changes`' own timestamps, no new
      tracker). Both auto-apply blocks wired into the trading loop
      (advisory: applies the single highest-priority recommendation
      clearing `auto_apply_min_confidence` per cooldown window, not a
      burst of every qualifying one). 14 new tests, 656 passing.
      Verified live end-to-end, not just unit-tested: enabled calibration
      auto-apply via the real route (wrong phrase rejected, right phrase
      accepted), then forced an immediate cycle by briefly dropping
      `snapshot_interval_sec` to 1s — confirmed a real
      `calibration-auto-apply`-sourced entry landed in the audit trail,
      `whale_confidence_weights` updated to real, correctly-renormalized
      values (sum ≈ 1.0), and the tick loop kept running with no error.
      Restored `snapshot_interval_sec` to 21600 afterward. Left
      **calibration** auto-apply enabled live (directly requested,
      already past its data-sample gate) but left **advisory** auto-apply
      at its default off — fixing the broken mechanism wasn't the same as
      being asked to turn it on, and it's a materially broader blast
      radius (any qualifying `strategy.*`/`market_strategy.*` field, not
      one well-scoped config section).
- [x] **Click-to-apply on the Backtest Sweeps panel (2026-08-10, direct
      request: "click to apply buttons throughout... where suggestions
      are made so i dont have to switch to the config tab")** — every
      non-current row in both sweeps (`strategy.entry_threshold`,
      `strategy.min_whale_winrate_pct`) now has an Apply button, gated
      behind a `confirm()` dialog that honestly discloses this is a raw
      sweep value, not a hedged Advisory recommendation (the stateless
      replay it's based on doesn't account for cooldowns/concentration
      limits/other fields changing at the same time — Gap 2's own
      disclosed limitation). Other new panels this session (Rejected
      Candidates, Regime Segmentation, Series Evaluator cross-check,
      Cross-Strategy Comparison) were deliberately left without an apply
      button — none of them resolve to one clean config value the way a
      sweep row or an Advisory recommendation does. Verified live via
      selenium-chrome.
- [x] **Category segmentation - the deferred half of Gap 9 (2026-08-10,
      direct follow-up request: "add those things, and have them auto-
      enable... once there *is* enough data")** — regime segmentation
      could only ever cover hour-of-day/day-of-week when it first
      shipped; category needed a real new persistence layer first, since
      `market_catalog.category` is watchlist-scoped and rotates, so a
      historical trade couldn't be reliably joined back to its category
      after the fact (exactly the limitation the doc originally
      disclosed, not worked around with a guess). New
      `services/trade_category.py` — records `ticker -> category` once
      per position OPEN, looked up from `state["market_titles"]`/
      `state["event_titles"]` (already cached every tick, zero new API
      calls) at the exact moment either strategy places a trade. New
      `regime_analytics.by_category()` joins onto it, same shape as the
      other two bucket functions. New `GET /api/regime/by-category` + a
      `trade_category` Danger Zone reset flag; the existing Regime
      Segmentation panel gains a third table. Naturally "auto-enables"
      the same way every other real-data-gated panel in this app does —
      empty until enough trades placed *after this shipped* have a
      recorded category, not backfilled with a guess for older ones.
      28 new tests, 668 passing. Verified live: confirmed the app
      imported and the trading loop kept running with zero tick errors
      after deploy — but no NEW whale-follow position had opened in the
      few minutes since deploy by the time this was checked (entry rate
      depends on a real signal actually clearing the threshold, not every
      tick), so an actual captured category row is disclosed as pending
      the next real trade, not fabricated as already confirmed.
- [x] **Graph views for the History tab (2026-08-10, direct request: "i
      want to add useful graph views to the history tab")** — three new
      charts, reusing the existing `renderEquityChart()` SVG line-chart
      renderer (already generic enough — no new charting mechanism, no
      external library) rather than building bar charts from scratch:
      calibration-history win-rate trend (is calibration improving as
      data accumulates, or stuck?), the entry-threshold backtest sweep
      curve (makes the real U-shaped finding from earlier this session
      visible at a glance instead of scanning a 20-row table for it), and
      the hour-of-day win-rate curve. `renderEquityChart()` gained an
      optional `opts` param (`{emptyMessage, valueFormatter}`) so these
      percent-based charts don't get dollar-formatted like every
      pre-existing equity chart — fully backward compatible, every
      existing call site untouched. Verified live via selenium-chrome:
      all three render a real `<svg>` with zero console errors.
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
- [x] Finish migrating the rest of the render sites (Advanced fills/orders
      tables, the screener table) onto the child-label/price-aware display
      pattern shipped 2026-08-10 for positions/market cards/the
      market-detail modal — those three call the older `marketLabel()`
      alone and still don't show `yes_sub_title`/per-leg combo data.
      **Done (2026-08-11), plus two real bugs found along the way** —
      direct reports: "Cleveland vs Detroit Winner? YES but not the
      winner...semantically it doesnt even make sense", rows "extremely
      wide" from cramming series + child market + side onto one line, and
      the market-detail modal's own Recent Trades panel showing the same
      ambiguous "Taker bought no" with no indication of what "no" meant.
      Root causes: (1) a real Kalshi API data quirk, not a caching bug —
      confirmed directly against `KalshiClient.get_market()` that for many
      simple 2-way matchup markets Kalshi's own `yes_sub_title`/
      `no_sub_title` come back identical (both say the same team name);
      `marketContext()`/`marketCardHTML()` (`static/index.html`) now detect
      that degenerate case and show an honest "not {yes_sub_title}" for the
      NO side instead of the misleading duplicate. (2) `main.py`'s
      `_relevant_tickers()` only ever scoped the whale-follow broker's
      positions/trade log for title-resolution data, never `market_broker`'s
      — so `GET /api/market-strategy/state`'s own lookups were scoped wrong
      for its own strategy's tickers; a fresh page load showed raw ticker
      IDs on the Market-Native tab, worse than before context lines were
      even added, because earlier testing had been masked by the browser's
      stale cached titles. Fixed by adding `market_broker.positions`/
      `trade_log[-25:]`/`market_decision_feed` tickers into the scoped set.
      For the width complaint: instead of a full resolver migration, added
      a smaller-font "Betting: {what this side means}" second line
      (`contextLineHTML()`/inline equivalents) below the market name at
      every remaining site that shows a ticker+side — Signal History,
      Possible Accumulation, the live Signal Feed, Trade Tape, the Portfolio
      Trade Log and Decision Feed table, real-account Positions/Fills/Orders
      (Simple and Advanced), Trading History, Market-Native positions/
      trades/decisions, Shadow Mode's trade log, the Market Analyst's
      track-record/single-analysis panels, and the market-detail modal's
      Recent Trades. `.decision-row` had never actually been styled (only
      `.position-row`/`.trade-row` were) — added to the shared row rule.
      Deliberately left the Advanced screener table alone — its dense
      multi-column layout is a different, already-settled design boundary
      (see Item 4/`status.html` phase 62), not an oversight. 668 tests
      passing (unchanged — display-only plus the one backend scope fix, no
      new persisted state). Verified live via `selenium-chrome`: the
      Jodar-vs-Fils modal now shows "Taker bought NO / Betting: not Rafael
      Jodar"; Signal History/Possible Accumulation show correct sub-lines
      across real live rows including several genuinely degenerate-case
      tickers; zero new console errors.
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
- **2026-08-11 session — auto-apply hardening**, four direct reports acted
  on together: (1) "the apply button should only appear next to config
  change options the system agrees with" — Advisory Recommendations now
  hides the manual "Apply to config" button for `confidence_label ==
  'low'` suggestions (still shown, just without a one-click action - the
  system itself is hedging on n<5, so it shouldn't offer a one-click way
  to act on its own low-confidence read). (2) "auto apply should wait for
  a significant dataset... and predict how those changes may improve (or
  worsen) before applying" — both auto-apply paths get a dedicated,
  stricter-than-manual sample-size floor on top of what already existed
  (`advisory.auto_apply_min_n`, default 25, on top of the existing
  confidence-tier check; `confidence_calibration.
  auto_apply_min_resolved_signals`, default 150, vs. the report's own
  50-signal display floor) - both new, Config-tab-editable, and neither
  affects manual Apply clicks. Calibration auto-apply's logged rationale
  now cites the specific calibration gap (factor + pts) the reweighting
  was derived to address, rather than a fabricated forward win-rate
  number this app has no way to honestly back before the new weights have
  scored anything. (3) "make sure the suggested values arent stale" — a
  real gap found: the series-analyst/full-spectrum-analyst apply routes
  (`POST /api/market-analyst/series/apply`,
  `.../full-spectrum/apply`) applied a suggestion's `suggested_value`
  using its analysis-time `current_value` with no check that the live
  config still matched - unlike the rule-based Advisory apply route
  (which recomputes fresh every time and already 404s on drift), a config
  change between analysis and apply (a manual edit, another analysis,
  auto-apply) would silently overwrite based on a stale premise and log a
  fabricated "before" value. New `_config_value_at_path()` helper backs a
  check that now 409s with a clear message if the live value has moved.
  4 new tests. (4)
  "the config change log shows [Object object]" — real bug: multi-value
  auto-applied changes (calibration replaces the whole
  `whale_confidence_weights` dict in one shot) hit a bare `String(value)`
  call, which just invokes an object's default `toString()`. New shared
  `formatConfigValue()` (JSON.stringify for objects, plain string
  otherwise) used everywhere a config value renders — Change History,
  Advisory Recommendations, series/full-spectrum suggestion cards.
  (History-list length was already capped at 20 via the existing
  `?limit=20` fetch - confirmed, not changed.) New Config-tab "Whale-Signal
  Calibration" section (previously had zero Config-tab presence at all,
  despite being a live, auto-applying feature) plus one new field on the
  existing Advisory Engine section. 672 tests (was 668). Verified live via
  curl (a real pre-existing calibration-auto-apply row's dict value
  confirmed rendering as JSON, not `[object Object]`) and
  `selenium-chrome` (both new Config-tab fields, zero console errors).
  Then a real live incident, start to finish, triggered by fixing a real
  gap the wrong way at first: investigating "very few whale prints for
  baseball despite a low threshold" found `main.py`'s `_fetch_trade_tape()`
  capping the platform-wide trade tape at 100 items and fetching only the
  last 10 trades per ticker with no `min_ts`/cursor - both silently
  dropped real trades before whale detection ever saw them. Direct
  instruction to make it genuinely unbounded ("i want trade tape to be
  unlimited, never capped") shipped a **second, more severe incident**:
  `series_evaluator.record_trade_observed()`/`candidate_log.
  record_rejection()` each open a fresh SQLite connection per individual
  raw trade, and removing the cap multiplied per-tick trade volume
  10-30x - thousands of blocking synchronous DB round trips froze the
  single-threaded event loop for several minutes (confirmed via nginx
  "upstream timed out" + an internal request timing out against
  `localhost:8000` from inside the same container). User proposed
  migrating off SQLite to a real DB server; recommended against it
  (`AskUserQuestion`, agreed) since the actual bug was blocking I/O on an
  async event loop, not a SQLite capacity problem. Fixed properly instead:
  `KalshiClient.get_trades()` gained real `min_ts`/`cursor` params (SDK-
  confirmed, already supported by Kalshi, never wired up); `_fetch_trade_
  tape()` now pages every ticker to completion via a new `_fetch_trades_
  for_ticker()`, deliberately skipping pagination when there's no
  watermark yet (a second bug caught mid-fix - unpaginated cold-start
  would walk every watched ticker's entire history at once); a new
  `state["trade_tape_last_fetch_ts"]` watermark makes every later tick
  incremental; the UI panel stays capped at 100 for display, decoupled
  from detection's now-uncapped input. `kalshi_trade_tape.fetch_signals()`
  restructured so its entire per-trade loop (every blocking DB call it
  makes) runs via `asyncio.to_thread()`, never on the event loop.
  `series_evaluator.record_trades_observed_bulk()` collapses what used to
  be one connection per trade into one per tick. WAL mode
  (`PRAGMA journal_mode=WAL`) added to all 14 `services/*.py` modules
  sharing the `_connect()` idiom - readers no longer block behind a
  writer. A real editing mistake happened and was caught before it ever
  reached the live server: a scripted WAL-mode rollout had an unescaped
  `\3` in a non-raw Python string, silently interpreted as the octal
  escape `\x03` instead of a regex backreference, deleting a line from
  all 14 files - caught via `ast.parse` failing on every one, fixed with
  a corrected script, reverified before restarting. 15 new tests, 687
  passing (was 672). Verified live after a full restart: the first
  cold-start tick (513 markets) took ~74s but the app stayed fully
  responsive the entire time (the actual fix, not just "it didn't crash
  this time") - confirmed via `selenium-chrome` that the market-detail
  modal still opens quickly with accurate data. See `static/status.html`
  phase 97 for the full incident writeup.
  Then a real, root-caused fix for "the watchlist groupings is broken" —
  the user's own diagnosis ("likely a result of the active removal of
  watchlist items") was exactly right: `main.py`'s `_fetch_markets()`
  appends an open position that rotated off `round_robin_select`'s own
  selection (`extra_tickers`) to the *end* of the markets list regardless
  of series, but `renderMarketCards()` assumes same-series markets are
  always consecutive — true of `round_robin_select`'s own output, not of
  the post-append result. One series could render as two separate,
  non-adjacent sections. Fixed on both sides: the backend re-groups by
  series after the append (first-occurrence order preserved, not an
  alphabetical sort, so `round_robin_select`'s volume-priority ordering
  survives — also now covers the manually-pinned watchlist branch, whose
  order was never guaranteed grouped at all); the frontend's own
  `seriesRuns` builder switched from an adjacent-only scan to a
  `Map`-keyed merge, belt-and-suspenders on top of the backend fix. 1 new
  test. 673 tests (was 672). Verified live: queried the real DOM after
  the fix and confirmed zero duplicate series sections across the
  actual, currently-live watchlist. Two other reports investigated in the
  same pass — "price fluctuations arent showing" and "very few whale
  prints for baseball despite a low $500 threshold" — turned out **not**
  to be code bugs: `signal_log` showed 302 real MLB whale signals in a
  single recent 6-hour window (all above threshold), and the price-update/
  live-badge mechanisms both checked out correctly end-to-end once the
  trading loop was running undisturbed. The live Signal Feed panel's
  existing 50-item cap (shared across every concurrently-active sport,
  not baseball-specific) is the more likely source of the "few prints"
  impression — no code change made for either, since nothing was actually
  broken.
