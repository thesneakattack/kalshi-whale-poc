# Roadmap: comprehensive, dummy-proof paper trading terminal

Guiding principle: someone who has never touched a prediction market or a
trading interface before should be able to open this app and understand
*what they're looking at*, *why it did what it did*, and that *no real money
is ever at risk* unless they deliberately configure it to be. Everything
below is measured against that bar, not against "does it technically work."

This is a living to-do list, not a snapshot — check items off in place and
add new ones as they turn up. **Keep this file short.** Only genuinely open
items live here; the moment something ships, it comes off this list
entirely (a one-line pointer at most) — the narrative belongs in
`static/status.html`, not here. Three other docs carry the "what happened
and why":

- **`static/status.html`** (`/status`) — the actively-maintained
  backward-looking record, phase by phase, with the full "why," verification
  steps, and bugs found along the way. The primary source for "what
  happened and why" for anything shipped from now on.
- **`docs/roadmap-archive-2026-08-09.md`** — a frozen snapshot of this
  file's full detail as it stood on 2026-08-09 (837 lines, pre-condensing).
- **`docs/roadmap-archive-2026-08-16.md`** — a second frozen snapshot,
  covering 2026-08-09 → 2026-08-16: the "Shipped (condensed)" section
  stopped staying condensed and re-grew a full narrative, so this file got
  cut back down a second time. Consult it (or `git log`/`git blame`/`git
  show` on this file, the real history for anything committed) for the
  full session-by-session story behind anything not covered in
  `status.html`.

**When something ships, update `status.html`, not this file's own prose.**
Use the `/sync-status-docs` skill — it checks the item off here (a bare
`[x]`, not a rewrite into a paragraph) and adds the matching timeline
phase to `status.html` in one pass. Resist the urge to leave a factual
trail in this file itself; that's exactly how it got long twice already.

> **Starting a fresh session?** Read
> `docs/next-session-pickup-2026-08-17.md` first. It carries the measured
> findings from 2026-08-17 (the dollar-vs-contract whale-threshold bias, the
> real 77.5% tradeable whale accuracy, the epoch-aware diagnostics blocker)
> in a ready-to-act form, so none of it has to be re-derived.

## Path to production

P0 — the code-level gates around real money (order schema verified against
the official SDK, typed in-app confirmation phrase, restricted CORS, real
account field names, kill switch + bankroll persisting across restarts) —
is **fully shipped**. That's necessary, not sufficient: going from "the
gate works" to "flip it for real" still has these open operational
questions.

- [x] **#1 priority, direct instruction (2026-08-16, live KXBTC15M stress
      test — "otherwise I can't trust any insights whatsoever"):** a
      position can open with almost no runway left before its market's
      close and then just ride to settlement completely unmanaged.
      Confirmed live: an entry on `KXBTC15M-26AUG161645-45` at 830.2s into
      a 900s market life (69.8s of runway) closed 6.6s *after* the
      market's own `close_ts`, having crossed none of
      `take_profit_pct`/`stop_loss_pct`/`auto_exit_threshold` in between.
      Checked and ruled out first, not assumed: this is **not** a signal-
      processing delay. `_process_stream_trade`/`_process_stream_ticker`
      (`main.py`) already call `strategy.check_exits()` on every
      individual websocket trade/price message in real time, not batched
      to `kalshi.poll_interval_sec` — `last_tick_duration_sec` measured
      0.27s under the same load that produced this case, and the position
      was evaluated on the order of hundreds of times during its ~76s
      life. It just never crossed a configured threshold. The real gap is
      two missing gates, not speed:
    - [x] No entry-side minimum-runway check — `strategy_engine.evaluate()`
          has an upper bound on time-to-close (`close_window_sec`) but
          nothing that refuses a new entry once too little time remains to
          realistically manage a position before close.
          `special_market_min_seconds_to_close`'s grace period looks like
          it would cover this but doesn't — it only applies when
          `can_close_early`/`collateral_return_type`/`mutually_exclusive`
          are set, which plain crypto price-crossing markets like
          KXBTC15M never have. Shipped commit `2974e42` (2026-08-16) as
          `strategy.min_seconds_to_close`, checked in `evaluate()` right
          after the existing `close_window_sec` upper-bound check.
    - [x] No time-to-close-aware exit rule — `check_exits()`'s three
          opt-in layers (take-profit/stop-loss/auto-exit) are all purely
          price-driven; nothing forces a decision once a position's
          remaining runway drops below some floor, regardless of where its
          P&L currently sits. Shipped commit `2974e42` (2026-08-16) as
          `strategy.exit_min_seconds_to_close`, an elif in the hard-rule
          exit chain (now `services/exits/exit_engine.py` post-modularization)
          that force-closes once remaining runway drops below the floor,
          independent of P&L.

      Separately, real (not a bug) cost of the current KXBTC15M
      stress-test config itself: three consecutive stop-loss-chased
      re-entries on that same market instance as price whipsawed through
      its volatile final ~200 seconds, net -$590.76 on that one market
      alone — expected given every entry filter was deliberately stripped
      to guarantee coverage, worth remembering before reading too much
      into this config's P&L.
- [ ] `services/shadow_mode.py` exists and logs what the strategy *would*
      trade against real signal data, but hasn't yet been run for a real
      evaluation stretch and reviewed — that review, not the code existing,
      is the actual "trust it" gate before ever flipping `trading_enabled`.
- [x] **Risk enforcement lives inside strategy code, not the execution
      layer.** Found 2026-08-22 via a gap-check against
      `docs/prediction-market-bot-research.md`'s engineering-safety
      checklist. `RiskManager.check_daily_loss()`/`max_trade_size()` are
      only ever consulted from `strategy_engine.py`'s own `evaluate()`
      (`services/strategy_engine.py:255-256,457`) — neither
      `PaperBroker.buy`/`sell` nor
      `services/kalshi_account_client.py`'s `create_order` independently
      re-checks `risk.halted` or the position-size ceiling before
      executing. This is the general shape of the bug the four-entry gate
      bypass above already proved reachable once (a gate skipped because
      it only ran at one call site) — a future bug in any strategy's gate
      logic, not just the one already fixed, has no execution-layer
      backstop. Matches the research doc's premortem #2 almost exactly:
      "sizing, not signal, caused the blowup... risk sizing wasn't
      independent of strategy confidence." Shipped 2026-08-22, commit
      `a8de034`: `PaperBroker.open_position()` now refuses (returns `None`)
      when the wired-in `RiskManager` is halted, or when the new
      portfolio-wide exposure cap below would be exceeded — the same
      choke point both a market-order entry (`strategy_engine.evaluate`)
      and a filled resting limit order (`check_pending_fills`) already
      pass through, so a bug in either caller's own gate logic now has a
      backstop. `KalshiAccountClient.create_order()` gets the same halt
      guard, with an `is_closing_order` escape hatch so a flatten/close
      order still works during a live halt.
- [x] **No "flatten all positions" / emergency-close path exists.** Found
      2026-08-22, same gap-check. Confirmed via direct search
      (`flatten`/`liquidate`/`emergency-close` across `services/` and
      `main.py` turn up nothing but an unrelated historical incident
      comment) — if Kalshi access is cut off suddenly (a regulatory action,
      a state geofencing order — see the sports-legal item below) or the
      kill switch trips on a real account, there is no single function
      that closes every open position at once; today that would be manual,
      per-position action. Named directly in
      `docs/prediction-market-bot-research.md`'s premortem #4 and §5's
      last checklist item. Shipped 2026-08-22, commit `a8de034`:
      `PaperBroker.close_all_positions()` plus
      `KalshiAccountClient.flatten_all()` (side/price mapping verified
      against `docs/kalshi/create-order-v2.md`'s `BookSide` description and
      `get-positions.md`'s `position_fp` sign convention — no bulk-flatten
      endpoint exists on Kalshi's own API, so this iterates and closes each
      position individually), wired into a new `POST
      /api/trading/flatten-all` route gated by the same typed-confirmation-
      phrase mechanism as `/api/trading/enable`.
- [x] **No portfolio-wide exposure cap — only per-trade and (opt-in)
      per-series ones.** Found 2026-08-22, same gap-check.
      `max_position_pct` (`services/risk_manager.py:126-127`) caps a
      single trade; `max_open_positions_per_series`
      (`services/strategy_engine.py:445-452`, off by default) caps
      position *count* within one series. Neither caps total dollar
      exposure summed across concurrently-open positions in *different*
      series — five maxed-out, uncorrelated positions can still add up to
      far more aggregate risk than `max_position_pct` alone implies.
      Shipped 2026-08-22, commit `a8de034`: new
      `RiskManager.check_total_exposure(current_exposure, prospective_cost,
      bankroll)`, stateless like `max_trade_size`, reads a new opt-in
      `risk.max_total_exposure_pct` config field (default `null` = no cap,
      same "ships fully built, opt-in" precedent as other per-series/
      kelly-fraction knobs), enforced at the same `open_position()` choke
      point as the execution-layer item above.
- [ ] This runs today only under local `ddev` on one machine — no real host,
      TLS domain, process supervisor, or uptime guarantee beyond ddev's dev
      containers. Decide and build a real deployment target before real
      capital depends on this process staying up.
- [x] `data/*.db` (bankroll, positions, kill-switch state, signal log) is
      single-file SQLite with no backup/retention policy — fine for a local
      paper POC, not once a lost file means lost real financial state.
      Shipped 2026-08-23: new `services/backup/` package snapshots every
      `data/*.db` file via `sqlite3.Connection.backup()` (safe against a
      torn snapshot regardless of journal mode) into timestamped
      directories under `data/backups/`, prunes by retention count
      (default 14, ~3.5 days at the default 6h interval), and records
      every run for audit. Runs both as a config-gated background task in
      the trading loop (zero setup under `ddev`) and as a standalone CLI
      (`python -m services.backup.backup`) for a real deployment's own
      cron/systemd timer. `GET /api/backup/status`/`history` +
      `POST /api/backup/run` for visibility/manual control. Sizing this
      against real data also surfaced and fixed a real, unrelated 5.7GB
      `game_state.db` bug (crypto candlestick payloads re-stored in full
      on every write, zero consumers ever read them back) — see that
      module's own CHEATSHEET/`status.html` phase 126.
- [ ] No monitoring/alerting beyond watching the dashboard or `ddev logs` —
      a kill-switch trip, crash, or connectivity loss currently notifies no
      one. Worth promoting ahead of the P4 notifications item below,
      specifically for these three cases.
- [ ] Auth is optional, single-operator Google OAuth (`services/auth.py`) —
      fine for "just me," but confirm that's still the model before real
      money sits behind it. No user table, no session-invalidation UI, no 2FA.
- [ ] `advisory.auto_apply_enabled`/`confidence_calibration.auto_apply_enabled`
      (both config-gated, typed-confirmation-phrase protected, with their
      own sample-size floors and a full audit trail) have only ever run
      against paper-mode trade history — confirm they should stay on (or
      get a stricter/zero floor) before real capital is ever behind the
      config they're tuning.
- [ ] Have a human, not a default, set real position-size/kill-switch
      numbers in `config/settings.yaml` before the first live dollar —
      today's defaults were picked for exercising paper-mode logic, not
      sized for real capital.
- [ ] **Sports-category contracts are in genuinely live, multi-state legal
      dispute** (`docs/prediction-markets-research-reference.md` Part 3) —
      Nevada/Massachusetts geofencing, several other states' suits
      unresolved and moving weekly. Election/economics contracts are
      practically settled as tradeable; sports specifically is not. This
      app has zero category-level legal-risk awareness today
      (`kalshi.categories` is a plain volume/topic filter, not a risk one).
      Before real trading is ever enabled: a real answer on category
      selection (and possibly state-of-residence) is needed — see
      `docs/prediction-market-strategy-alignment-plan.md` Part 6. A
      separate, non-blocking risk axis was cross-checked and ruled out as
      *not* a new gate here: Kalshi's own `additional_prohibitions` per
      series is generic eligibility boilerplate (who the account holder
      personally is - a league employee, a campaign staffer, an MNPI
      holder), not a state-of-residence restriction, and not a blocker for
      today's single-operator design unless this ever becomes multi-user.
- [ ] `pip-audit` (CI job, `.github/workflows/tests.yml`) found 20 known
      vulnerabilities across 5 pinned dependencies. Most relevant to real
      trading: `cryptography` (signs every Kalshi API request via the RSA
      private key) is several versions behind fix availability; `starlette`
      needs FastAPI itself bumped to pull a patched version. Deliberately
      not fixed blind — these need their own careful, tested upgrade
      changeset given how central they are, not a version bump made in
      passing. The `dependency-audit` CI job stays red until this is
      resolved — that's accurate signal, not broken CI.
- [ ] **The entry gates select a worse subset than the pool they draw
      from.** Measured 2026-08-17 on KXBTC15M over 24h by
      `services/series_watcher.py` (`GET /api/diagnostics/series/KXBTC15M`):
      whale signals resolved 88.8% correct across 394 settled signals, but
      the 12 the gates actually traded resolved only 58.3% correct — the
      dominant term in the 88.8%-vs-47.1% accuracy/win-rate gap (−30.6pts
      of it, against −11.2pts from exits). Adverse selection, not a bad
      signal source: something in `entry_threshold` / the price band /
      cooldowns / the runway gates is systematically preferring the wrong
      end of the distribution. Not yet root-caused to a specific gate —
      the next step is per-gate accuracy of what each one admits versus
      rejects, which `candidate_log` cannot answer as-is (its
      `rejected_candidates` table is upsert-deduplicated per
      `(ticker, strategy, gate_name)`, so it is unusable for population
      statistics).

- [x] **Switch the whale threshold from dollars to contract count (or add
      one alongside).** Measured 2026-08-17 across 145,785 real captured
      prints: a dollar gate is geometrically biased toward near-certainty,
      because $2,500 buys 125,000 contracts at 2c but only 2,505 at 99.8c.
      Its clear rate climbs monotonically with price (0.00% below 0.50,
      0.85% at >=0.98), and **44 of the 58 prints that clear $2,500 (75.9%)
      sit at unit cost >=0.95** — the band that bleeds. Mean unit cost of
      everything it selects: **0.926**. A `count >= 5,000` selector plus the
      tradeable-range filter lands at mean 0.759 with 27.3% inside the only
      profitable band, against 8.6% today. **This is the single biggest
      measured lever on the 70%/70% target.** Full tables and the
      implementation note are in `docs/next-session-pickup-2026-08-17.md`.
      Shipped 2026-08-23, per direct confirmation to replace (not
      alongside) the dollar gate: `whale_watcher_kalshi.min_notional_usd`/
      `min_notional_usd_by_series` are gone, replaced by `min_contracts`
      (default 5,000, matching the measured recommendation) and
      `min_contracts_by_series` in `services/whalewatchers/
      kalshi_trade_tape.py`. Real dollar notional is still computed and
      recorded for diagnostics — it just no longer gates anything. Every
      downstream consumer of the old dollar gate was updated in the same
      pass, not just the gate itself: `services/diagnostics/diagnostics.py`
      (`check_threshold_integrity` now checks `signals.size`, a NOT NULL
      column populated by every provider, against the new floor;
      `check_coverage` qualifies exchange-wide prints by contract count),
      `services/series_watcher.py`'s `funnel()` (the `whale_sized_prints`
      stage), `services/analytics/market_analyst_orchestrator.py`/
      `services/market_analyst_agent.py` (per-series override shown to the
      series-analyst LLM), and the live dashboard's Controls panel
      (`static/index.html` + `frontend/src/js/config-panel.js`, which
      esbuild rebuilds into `static/js/dashboard.bundle.js` — this field
      really is wired to live config edits, not just documented).
- [x] **New finding, surfaced by the epoch-aware fix above: real
      `min_notional_usd` violations, not stale-config artifacts.** Measured
      2026-08-22 live over 24h post-fix: `check_threshold_integrity` still
      reports 1169/1900 signals (61.5%) below the `min_notional_usd` that
      was genuinely live for their series at `seen_at` — spot-checked
      several evidence rows directly (e.g. a $2,297 print judged against a
      contemporaneous $5,000 floor, no config change involved) to confirm
      this isn't an epoch-attribution bug reappearing in a different shape.
      **Superseded 2026-08-23, not root-caused**: the dollar gate this was
      investigating no longer exists (see the item above) —
      `check_threshold_integrity` now checks contract count, not notional,
      so there is no more `min_notional_usd` floor for a signal to violate.
      If the same *shape* of question (are logged signals ever below the
      gate that was genuinely live for them) needs re-asking against the
      new `min_contracts` floor, it should be re-opened as a fresh item
      against real post-switch data, not resumed from this investigation's
      state.
- [x] **Find the four-entry gate bypass.** Four real entries at unit costs
      0.97, 1.00, 0.20, 0.97 (08/16 21:26–22:25), one at `conf 0.25` against
      a 0.495 threshold, so they skipped both the price band and the
      confidence gate. Prices in the reason string match the recorded
      prices, and `shadow_mode`/`market_strategy` are ruled out. The
      `01b126c` invariant makes the class unreachable going forward, but a
      gate that *can* be skipped is worse than no gate. **Root-caused
      2026-08-22:** `PaperBroker.check_pending_fills()` called
      `open_position()` for a filled limit order using only the fill-time
      price — `entry_threshold`/the tradeable-price-range floor/
      `min_unit_cost`/`max_unit_cost` were only ever checked once, at
      placement time. Fixed by extracting those gates from
      `strategy_engine.evaluate()` into a shared `_validate_entry_price()`,
      called both by `evaluate()` and a new
      `FollowTheWhaleStrategy.validate_pending_fill()`, wired into
      `check_pending_fills` via a `validate_fn` callback — a rejected fill
      now cancels the resting order and records a `"fill_rejected"`
      decision instead of opening a position (commit 6921543).
- [ ] **Decide whether the settlement projection is an edge, then act on
      it or drop it.** `services/settlement_edge.py` is now recording both
      forecasts of the same event at the same instant (the market's yes
      price, and the projection from the partial 60-second index average)
      and Brier-scoring them once the outcome lands —
      `GET /api/diagnostics/settlement-edge`. It reports `insufficient`
      until there's enough resolved data, deliberately. **Nothing trades on
      it yet, and nothing should until that verdict flips.** If the
      projection wins, the follow-on is real: `min_seconds_to_close: 300`
      currently refuses entries in the final five minutes, which is exactly
      the window where settlement is partly *known* rather than guessed —
      that gate was the right fix for a blind system and would need
      revisiting for one that isn't. If the market wins, say so and delete
      the trading ambition, keeping the capture as a diagnostic.
## P4 — Nice-to-haves

- [ ] **Move analytics/advisory computation out of the live tick loop —
      dump the underlying data and let external tooling analyze it.**
      Direct instruction (2026-08-21): "considering we dont care so much
      about analytics as we do performance right now, maybe it would be
      best to add a data dump feature for analytics to run on it outside
      of the system itself." Ties directly to the latency/responsiveness
      problems found the same session (message drops under exchange-wide
      trade load, tick-duration budget overruns, a silently-wedged
      websocket stream) — `trading_loop`'s `calibration_advisory` phase
      (confidence-calibration snapshot-and-auto-apply, advisory_engine
      recommendations) runs in-process every tick and is a candidate
      contributor. Deliberately queued rather than built immediately: the
      main.py modularization in progress the same session (see below) is
      lifting analytics/advisory routes and the market-analyst
      orchestration into their own bounded files first (`routers/
      analytics_routes.py`, `services/market_analyst_orchestrator.py`) —
      once that lands, swapping "compute live" for "dump raw data
      (signal_log/candidate_log/config_performance/trade_analytics) +
      analyze externally" becomes a self-contained change to those files
      instead of main.py surgery. Design the export shape (flat file?
      separate read-only DB copy? on-demand endpoint?) as its own pass once
      the modularization ships.
- [ ] **Consider removing the Market-Native strategy entirely.** Direct
      instruction (2026-08-22): "i think we can remove the entire market
      native strategy stuff for now, its over-complicating things." It's
      already off by default (`market_strategy.enabled: false`,
      `services/app_state.py`'s own comment), but the CODE duplication it
      causes is real and touches nearly everything: a second broker/risk
      pair (`market_broker`/`market_risk`), a second decision feed, a
      `strategy` param on every regime endpoint choosing between the two
      trade logs, `market-strategy-calibration`/`cross-strategy` routes,
      its own trading_loop phase, and its own frontend tab. Queued rather
      than done now - the in-progress main.py modularization already moved
      several of these routes into their new homes
      (`services/position/routes.py`'s `get_market_strategy_state`,
      `services/analytics/routes.py`'s `market-strategy-calibration`/
      `cross-strategy` routes) - a real removal pass should happen as its
      own deliberate step once modularization ships, not mid-phase, so it
      can cleanly touch every one of those files once instead of twice.
- [x] **Split `services/analytics/` further: advisory and whale calibration
      should each be their own module.** Direct instruction (2026-08-22).
      Shipped as Phases 2-3/9 of the same-day continued-modularization
      pass: `services/whale_calibration/` (confidence_calibration.py +
      calibration_history.py, zero cross-imports, cleanest split) and
      `services/advisory/` (advisory_engine.py, one real cross-import -
      `regime_analytics` - kept as a plain shared dependency rather than
      dragged along), each with routes.py + CHEATSHEET.md. What's left in
      `services/analytics/`: regime/candidate-log/cross-strategy/
      market-analyst/series-evaluator/market-strategy-calibration - backtest
      also moved out (Phase 4/9, `services/backtest/`). See
      `services/analytics/CHEATSHEET.md` for the residual scope and a new
      deferred item: `market_analyst_agent.py`/`market_analyst_orchestrator.py`
      are real split candidates too, but genuinely entangled with advisory/
      series-evaluator (checked directly, not assumed) - queued as its own
      future pass rather than rushed here.
- [ ] **Flatten the config surface - too many independent knobs to track
      which ones are actually load-bearing.** Direct instruction
      (2026-08-22): "the config settings, strategies, options, knobs,
      levers, widgets, need to be flattened a bit because there's too much
      variability to track the usefulness of these instruments." The
      3-day config-drift incident earlier this session (reverted, see
      `docs/next-session-pickup-2026-08-22.md`) is direct proof this is a real
      problem, not a hypothetical one: dozens of independently-tunable
      `strategy.*` fields drifted into a combination nobody would have
      chosen deliberately, and it went unnoticed for days precisely
      because there were too many knobs to eyeball at once.
      `config/settings.yaml`'s `strategy` section alone has ~30 fields
      (entry/exit thresholds, sizing, multiple `auto_exit_*_weight` scoring
      blends, longshot handling, runway gates...), doubled by
      `market_strategy`'s near-duplicate set (candidate for removal per
      the Market-Native item above), multiplied again by
      `strategy_overrides.by_category`/`by_series` layering on top, plus
      `whale_confidence_weights`' own 7-factor blend. Needs an audit pass
      (which knobs have actually been touched/mattered vs. which are
      vestigial or redundant with another) before deciding what to cut,
      consolidate, or fold into a computed/derived value instead of a
      free-floating setting - not done now, this is a planning item.
- [ ] **Separate the frontend from the backend completely.** Direct
      instruction (2026-08-22). Already partially true at the *serving*
      level (see CLAUDE.md's "Dev workflow" section): `main.py` is
      API-only, no HTML; `web` (nginx) serves `static/*.html` directly and
      reverse-proxies `/api/`+`/auth/` to `fastapi`, which has no public
      URL of its own. What's NOT separated: `static/`'s pages are each a
      single file of inline HTML/CSS/JS with no build step, no bundler, no
      independent project structure of their own, and no formal API
      contract - the frontend JS just knows the shape of `/api/state`'s
      response by convention, the same coupling-by-convention that's
      caused real bugs this project has already hit (CLAUDE.md's "displayed
      value must match its label" bug pattern is partly a symptom of this).
      Ties into the same "work on modules independently, separate repos or
      at least separate Claude sessions" goal as the backend modularization
      work this session - a real frontend/backend split would mean: the
      frontend becomes its own real project (own directory structure at
      minimum; a real build step/framework and possibly its own repo as
      the fuller version), and a formal, versioned API contract between
      them (FastAPI's own OpenAPI schema generation is free and already
      available, just unused for this today) instead of implicit shape
      agreement. Planning item only - not started, no design decided yet
      (how far to take it, whether a framework gets introduced, whether it
      becomes a separate repo).

      **Concrete direction added (2026-08-22, right after Phase 7 of the
      main.py modularization shipped): "index.html can also be modularized
      - it does not need to be an SPA."** `static/index.html` is 7,716
      lines - one inline HTML/CSS/JS file where a single `showView()`
      function toggles 7 tabs (Portfolio, Markets, Whale Watch, Terminal,
      Market-Native, History, Config), the frontend's own version of the
      exact shape of problem main.py just spent seven phases fixing. The
      cheaper alternative to the framework/build-step question above:
      split the tabs into separate real pages (browser nav instead of
      `showView()`), the same no-build-step pattern `status.html`/
      `login.html`/`accounts.html` already use, just applied to the main
      dashboard's own tabs - no bundler, no framework, just more files.
      One real coupling to design around first (confirmed by reading the
      code, not assumed): all 7 tabs currently render off one shared
      `refresh()` call that fetches `/api/state` once - splitting into
      pages means either each page polling independently (cheap given the
      existing ETag/304 caching already noted elsewhere in this file) or
      slicing `/api/state` itself per page. The multi-page split itself
      (browser nav replacing `showView()`) is still not started.

      **Shipped increment (2026-08-22): `index.html`'s inline `<style>`/
      `<script>` extracted into a real frontend build, not just files.**
      First cut was a pure byte-range split into 12 plain `<script src>`
      files sharing one global scope (no real module boundaries) - direct
      correction from the user mid-session: "no-build-step" was never an
      actual instruction, it was CLAUDE.md's description of the *existing*
      pages, and the frontend-separation item above already says the
      framework/bundler question is undecided. Given the choice between
      adding a bundler (real ES modules, npm libraries, no framework) or a
      full UI-framework rewrite, went with the bundler: **`frontend/`** is
      now a real npm project (`esbuild` + `eslint`) - `frontend/src/js/*.js`
      are the 12 files as genuine ES modules (`import`/`export`, no shared
      global scope), bundled by `npm run build`/`npm run watch`
      (`frontend/package.json`) into `static/js/dashboard.bundle.js`, which
      is the only thing `index.html` now loads. `index.html`: 7,716 -> 1,073
      lines. `.ddev/config.yaml` runs `npm install` on every `ddev start`
      (`hooks.post-start`) and esbuild's own watch mode as a background
      daemon (`web_extra_daemons`) so editing `frontend/src/js/*.js`
      rebuilds automatically - the same "save a file, it just works" loop
      `uvicorn --reload` already gives the backend.

      Converting to real modules surfaced a correctness class classic
      scripts never had to deal with, worked through methodically rather
      than by guessing: (1) inline HTML `onclick=`/`onchange=`/`oninput=`
      handlers (some built indirectly through a function parameter, e.g.
      `renderSuggestionCard(rec, acceptOnclickJS)`) resolve identifiers
      against `window`, not module scope - found the full set (not just the
      obvious top-level ones) via an acorn AST walk over every handler
      string across all 12 files, not by re-grepping the same regex twice
      and assuming completeness. Every top-level function gets a blanket
      `window.fn = fn` exposure per file (cheap, harmless if unused - closes
      the risk of missing one buried in a nested string-template chain);
      the specific state objects handlers mutate by property
      (`decisionFilter`, `screenerState`, `realPositionsState`, ...) get the
      same, individually. (2) Five values (`lastSignals`, `lastTradeTape`,
      `terminalSignalFeed`, `terminalDecisionFeed`, `tradeTapeMinSize`) get
      wholesale-*reassigned* elsewhere, which a one-time `window.x = x`
      would silently go stale after - replaced their inline reads/writes
      with small same-module wrapper functions (`_rerenderSignalsFilter()`
      etc.) instead. (3) The real ES-module-specific one: importers get a
      read-only live view of an imported binding, so a variable can only be
      *reassigned* by the file that declares it - an AST script found every
      case where the plain byte-range split had put a `let` in one file but
      its only reassignment in another (`terminalSignalFeed` and 9 others,
      all actually owned by `polling-and-websocket.js`'s poll loop
      regardless of which file the split happened to leave them in) and
      either relocated the declaration to its real owner or added a setter
      where two different files legitimately mutate it (`accountMode`).
      `eslint`'s `no-undef` (this project's JS equivalent of the backend's
      `pyflakes` check, wired as `npm run lint`) caught what none of this
      manual analysis did: a **genuine pre-existing bug**, unrelated to the
      split - `comboLegsHTML()` read a `state` variable that has never
      existed anywhere in this codebase (`git show` on the last commit
      confirms it predates this session). Its own comment already named the
      intended source ("the same ... latest global `prices` caches"), so
      fixed to read `terminalLatestPrices` instead of guessing new behavior.
      Verified throughout, not just at the end: `diff` against a
      reconstructed concatenation of the original inline script (no code
      lost/reordered) before any conversion; an AST script cross-checked
      every file's real import/export graph; `eslint --no-undef` and
      `node --check` both clean on every generated module; the built
      bundle's watch-mode auto-rebuild verified live (edited a source file,
      confirmed the served bundle changed within seconds, confirmed a
      dangling trailing *comment* genuinely doesn't survive esbuild's
      output - false alarm caught before it became a wrong conclusion).
      Full suite (1,161 tests) green throughout; the one test coupled to
      the old file layout (`tests/test_e2e_terminal_static_and_api.py`,
      asserting a JS helper's name in `index.html`'s own response body)
      fixed to fetch `js/dashboard.bundle.js` instead. `.ddev/nginx/
      kalshi-proxy.conf` gained a `no-store` rule for `*.css`/`*.js`, same
      stale-asset-after-edit risk this project already hit once for
      `.html`.
- [ ] **Per-module data-consumption audit + report.** Direct instruction
      (2026-08-22): "do a deep dive on each module and what data is pulled
      from where... maximize data consumption efficiency and effectiveness.
      i suspect things are using rest api calls where they shouldn't, data
      pulled from where they shouldnt." For each module, trace every data
      source it touches (real Kalshi REST call, real Kalshi websocket
      stream, this app's own SQLite store, in-memory `state`) and flag
      anywhere a cheaper/fresher source should be used instead - exactly
      the "REST call that should've been a websocket, or a websocket
      that's already flowing but re-fetched via REST anyway" shape of bug
      this project has hit before (see CLAUDE.md's REST-vs-websocket
      architecture notes). This becomes tractable specifically because of
      this session's modularization work - a report organized module by
      module needs real module boundaries to organize around, which
      didn't exist before this session.
      **Real groundwork already exists, this isn't starting from zero**:
      (1) each new module's own `CHEATSHEET.md` already documents its
      relevant `docs/kalshi/` pages; (2) a docs-mining pass this session
      already found several concrete instances of exactly this bug shape;
      (3) `docs/next-session-pickup-2026-08-17.md` already has a full
      REST-vs-websocket architecture breakdown (what's already migrated,
      what's deliberately still REST, what's a genuine gap). **The
      systematic, one-organized-report version of this item is still not
      started** - what follows is progress on the four named findings only.

      Of the four originally named, three were real bugs and are fixed
      (2026-08-23, same "module quality" pass - see CLAUDE.md's current
      top section):
      - [x] An uncapped `asyncio.gather` (no semaphore) in `_fetch_markets`'
            `live_markets_only` hydration branch (misnamed "pinned-watchlist"
            in the original finding - it's the live-only elif, not the pin
            branch) fired one `client.get_markets(limit=100, series_ticker=s)`
            per distinct selected series - at `watchlist_size: 150`, up to
            ~50 concurrent calls/tick, each alone able to exceed Kalshi's
            entire 600-token read-burst budget. Replaced with one batched
            `get_markets_by_tickers()` call (chunks to 50/request,
            sequential) - also fixed a real correctness gap for free (no
            status filter, so an already-settled market no longer needs a
            separate fallback fetch).
      - [x] `services/series_evaluator.py`'s `evaluate_pending()` opened a
            fresh SQLite connection per ready series inside its own loop -
            same per-call overhead this file's own `record_trades_observed_
            bulk()` was already written to avoid once before (2026-08-11).
            One connection for the whole call now, with a commit per verdict
            to keep the original per-verdict durability.
      - [x] `regime_analytics.by_category()` was computed twice on identical
            input inside `market_analyst_orchestrator._build_full_spectrum_
            context()`, and `trade_analytics.compute_summary()` twice inside
            `advisory_engine.generate_recommendations()` itself (every real
            caller passes both `gate_summaries` and `category_rows`, so this
            wasn't a rare overlap). Both hoisted to compute once.

      The fourth - `market_history.snapshots`' `spread`/`volume_24h`/
      `time_to_close_sec` columns written every tick, read by nothing -
      investigated and left as-is, not fixed: this is disclosed, deliberate
      forward capture, not dead-code waste. `docs/advisory-engine-plan.md`
      §9 (direct request, 2026-08-08: "feed... market data, historical
      data... to a machine-learning agent... add some scaffolding for that
      but let's not pursue that until the project is already finished")
      names exactly this shape - persist real data ahead of a not-yet-built
      consumer - as the intended design. Removing it would contradict that
      instruction and CLAUDE.md's "accumulated history is a first-class
      asset" rule. The real gap is that no consumer was ever built - worth
      a future item in its own right (a diagnostic or ml_feed.py extension
      that actually reads this data), not a deletion.
- [x] **Real REST rate limiting, direct report (2026-08-23): "happening in
      the history and especially position sections... I've insisted
      multiple times on streams to inform those sections and using REST
      API to only verify and act on decisions, not monitor data and make
      decisions every tick."** Root-caused to the same
      `live_markets_only` hydration branch this session's earlier data-
      consumption-audit pass had already touched once (the uncapped-
      `asyncio.gather` fix, same day) - that pass fixed the *concurrency*
      half (one safe batched `get_markets_by_tickers` call instead of N
      concurrent per-series ones) but missed the *caching* half: it still
      called that batched fetch **unconditionally on every tick**, with no
      TTL, unlike the pinned-watchlist and `extra_tickers` branches, which
      already routed through `_cached_market_fetch` (a 300s structural-
      field cache, price freshness from the WS ticker-channel overlay
      instead - the exact "websocket stream everything you can... leave
      the api calls for things that are absolutely necessary" instruction
      from 2026-08-15). Confirmed via direct instrumentation (wrapping
      the fake client's method, not just reading the code) that the
      uncached call really did fire every `_fetch_markets` invocation.
      Fixed by routing `live_markets_only` hydration through
      `_cached_market_fetch` too, so all three branches now share one
      consistent "REST only when structurally stale, price always from
      the WS stream" pattern. Added a size cap + oldest-first eviction to
      that shared cache (`_MAX_MARKET_OBJECT_CACHE = 2000`, same shape as
      `kalshi_trade_tape.py`'s own `_MAX_MARKET_CACHE`) since it now
      serves a much larger, faster-rotating ticker population than the
      small pinned/open-position set it originally did - unbounded growth
      over a long-running process would be the wrong trade for a cache
      whose whole point is avoiding REST calls. Real REST calls for
      account/position data (`_fetch_account_snapshot`) were already on a
      20s cache from an earlier pass, and a private, authenticated `fill`/
      `market_positions` WebSocket subscription already exists and is
      wired (`services/whale_stream/whale_stream_handlers.py`'s
      `_process_stream_fill`/`_process_stream_position`, subscribed in
      `kalshi_trade_ws.py`'s `run()`) - neither of those needed a change
      here; the gap was specifically the market-hydration path.
- [ ] **Retrospective sweep: which targeted datapoints/logic were built on
      a wrong understanding of the Kalshi API or the streaming/REST
      split.** Direct instruction (2026-08-22): "revisit datapoints we've
      targeted and the flow while maintaining api doc context, streaming
      and rest api data, and see where we made mistakes in
      approach/logic/solution building." Distinct from the data-consumption
      efficiency audit above - that one asks "is this the right *source*
      for this data" (REST vs. WS vs. cache), this one asks "did we
      correctly understand what the data itself *means*" before building
      logic on it. This project's own history already has a real pattern
      of exactly this mistake, caught reactively rather than swept for
      proactively: reading `category_tags` as per-event sport data when
      it's really a fixed facet-filter vocabulary (`docs/kalshi/CHEATSHEET.md`'s
      first entry), the deprecated `taker_side` field defaulting to "no" on
      any unreadable value, the dollar-denominated whale-notional threshold
      being geometrically biased toward near-certain prices (the single
      biggest reframing finding of the 2026-08-17 session - a *targeted
      datapoint* that was wrong, not just inefficiently fetched), the
      no-side cost-math bug (`size * price` instead of
      `size * (1 - price)`), trusting a single uncorroborated websocket
      price tick for a stop-loss decision. CLAUDE.md's "Bug pattern to
      watch for" section and `docs/kalshi/CHEATSHEET.md` both exist because
      of this exact failure shape, but so far every entry was added
      *after* a live incident forced the investigation, never as a
      deliberate sweep. The new work: go through each module's data
      inputs deliberately (not waiting for the next incident to reveal
      one), checking each against `docs/kalshi/` and against whether the
      REST-vs-streaming choice for it was ever actually validated or just
      assumed. Not started - planning item only, and worth doing after (or
      alongside) the data-consumption audit above since they'll cover a lot
      of the same ground from two different angles.
- [ ] **Consider a dedicated charts/graphs module, possibly server-rendered
      via Plotly or Matplotlib.** Direct instruction (2026-08-22): "should
      histographs be their own module as well? i think they should... also
      maybe we should use something like plotly or matplotlib." Right now
      chart-shaped time series (`cumulative_pnl_curve` in
      `/api/trading-history`, `equity_history`/`real_balance_history` in
      `/api/state`) are just raw `{t, value}` arrays computed inline where
      they're used, and rendering happens client-side in `static/`'s plain
      JS (no build step/bundler - see CLAUDE.md's Quick file map). Two
      separable questions worth answering before building anything: (1)
      does the chart-DATA assembly deserve its own module, separate from
      History (`services/history/`) - probably yes if this grows, marginal
      if it stays 2-3 fields; (2) does moving actual rendering server-side
      via Plotly/Matplotlib change anything for the better - it would add a
      real dependency and a departure from the "no build step" static-page
      philosophy, so weigh that against whatever it'd actually buy (SVG/PNG
      export? richer interactivity than hand-rolled JS already gives?)
      before committing to it. Queued rather than built now - the
      in-progress main.py modularization (see below) is the current
      priority; do this as its own deliberate pass once that ships.
- [ ] Two deferred next-steps from `docs/todo-2026-08-14-heuristics-audit-
      and-exit-tuning.md`, never picked back up: a time-til-close exit
      factor (auto-exit scoring currently has no awareness of how close a
      position is to its market's own close_time), and folding
      mutually-exclusive-pair order flow into sentiment analysis (
      `services/mutual_exclusivity.py` detects confirmed ME pairs and gates
      new entries against an already-held complement, but doesn't yet feed
      that signal into the sentiment/exit side).
- [ ] Wash-trading detection (`docs/platform-deep-scan-findings-2026-08-10.md`
      Finding 5) — the one of that doc's 7 cited strategy/risk gaps never
      built. The other 6 (edge-aware position sizing, cross-position
      concentration risk, exit-side analyst signal, calibration-band
      feedback via Advisory, market_strategy calibration parity, time-of-
      day/category regime segmentation) shipped across later sessions —
      see the archive docs for the session-by-session trace if the detail
      is ever needed.
- [ ] Regime-aware **live entry gating** — `services/regime_analytics.py`
      (hour-of-day/day-of-week/category win-rate segmentation) stays
      advisory-only; deliberately not wired into live entry gating yet
      (direct instruction: real plumbing complexity, didn't want to risk
      the live trading path twice in one session). Advisory-surfaced
      suggestions off this data already ship; this is specifically about
      an engine *gating on* the regime automatically.
- [ ] Revisit the 5s dashboard polling model (`setInterval(refresh, 5000)`)
      once any Advanced view needs sub-poll freshness — an ETag/304 pass
      already makes an unchanged poll nearly free, so this is a push-vs-poll
      latency question, not payload waste. Distinct from backend signal
      latency, which is a separate, already-better story: `trade_stream`
      (`services/kalshi_trade_ws.py`) is live-wired into whale-signal
      detection today (`main._process_stream_trade` runs off each streamed
      trade directly, no poll wait) whenever real Kalshi WS credentials are
      configured — confirm that's actually connecting live
      (`GET /api/state`'s `trade_stream_status`) before assuming this item
      needs backend work at all.
- [ ] Notifications (email/push) for real trades, kill-switch triggers, or a
      tracked whale's win rate crossing the avoidance threshold — see the
      "Path to production" section above, which calls this out as worth
      promoting ahead of any real-money flip.
- [ ] Finalize the app name — "Nessie" vs. "Operation Deepscan" is still an
      open decision; once picked, flow it through page titles, headers, and
      the README.
- [ ] Clicking a logged position/signal/decision should also show whether
      that specific position ultimately closed/won/lost, not just the
      market's current state — needs new backend correlation (signal →
      resulting trade → outcome) that doesn't exist today. Trading History
      rows already show this inline (close type + P&L); the signal feed and
      decision feed do not.
- [x] A real, permanent fix for the close_time-mutability gap
      (`docs/roadmap-archive-2026-08-16.md` has the full incident): Kalshi's
      `market_lifecycle_v2` WebSocket channel (`close_date_updated`,
      `determined`, `settled`, `activated`/`deactivated` events,
      `docs/kalshi/market_lifecycle.md`) would let `market_catalog` learn
      about a status/close_time revision the instant Kalshi emits it,
      instead of only catching it on the next scan or the real-time
      confirmation pass that currently bounds (not eliminates) the
      staleness window. Half shipped 2026-08-17: the subscription
      (`KalshiTradeWebSocketClient(subscribe_lifecycle=True)`,
      config-gated via `kalshi.market_lifecycle_stream_enabled`) and
      `close_date_updated` wired into the **in-memory overlay**
      (`state["markets"]`, via `whale_stream_handlers._process_stream_lifecycle`).
      **Second half shipped 2026-08-23**, closing both remaining gaps at
      once, verified against real captured message shapes from `ddev logs`
      first (not just the docs): new `market_catalog.apply_lifecycle_update()`
      applies `close_ts`/`status` straight to the **persisted SQLite
      row** the instant each event arrives (`close_date_updated` ->
      `close_ts`, `determined` -> `status="determined"`, `settled` ->
      `status="finalized"`) — see `services/market_catalog/CHEATSHEET.md`
      for the detail. Real captured shapes confirmed `determined` carries
      `result`/`determination_ts`/`settlement_value` exactly as documented
      and `settled` carries only `settled_ts`, no `result` — so
      `determined` is also now the trigger for
      `market_history.record_outcome`/`settlement_edge.resolve_window`/
      `market_analyst_agent.resolve_from_market_results`/
      `candidate_log.resolve_from_market_results`, in addition to (not
      instead of) the existing REST-tick path — closing the structural gap
      where a ticker that rotates off the live watchlist/discovery scope
      before it settles (routine for short-lived series like KXBTC15M)
      never got resolved via REST at all, since `market_lifecycle_v2` is
      exchange-wide. All four resolution functions are idempotent, so
      firing them from both paths is safe. Verified live post-ship:
      `catalog_updates_applied` incrementing on real traffic with
      `close_time_updates_applied` at 0 for the same events — proof this
      reaches markets the in-memory overlay alone could not.

## Shipped

Everything else has shipped — P0 (safety gates), the full dashboard/UX
overhaul, active position management, whale-tracking maturity (real trade-
tape provider, composite confidence scoring, advisory/recommendation
engine, market analyst agent, position netting, calibration, per-series
overrides), reliability/engineering hygiene (test suite + CI, official SDK
migration, rate-limit correctness), the full main.py modularization
(whale stream / market watch / position / position management / history /
analytics / config — main.py 5,450 → 1,716 lines across 7 phases), and
dozens of live-reported bugs found and fixed session by session.
`static/status.html` (`/status`) is the complete, phase-by-phase record —
115 phases and counting. For the detailed prose version of this file as it
stood before each condensing pass, see `docs/roadmap-archive-2026-08-09.md`
and `docs/roadmap-archive-2026-08-16.md`.
