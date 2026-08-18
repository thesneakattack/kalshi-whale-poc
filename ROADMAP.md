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

- [ ] **#1 priority, direct instruction (2026-08-16, live KXBTC15M stress
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
    - [ ] No entry-side minimum-runway check — `strategy_engine.evaluate()`
          has an upper bound on time-to-close (`close_window_sec`) but
          nothing that refuses a new entry once too little time remains to
          realistically manage a position before close.
          `special_market_min_seconds_to_close`'s grace period looks like
          it would cover this but doesn't — it only applies when
          `can_close_early`/`collateral_return_type`/`mutually_exclusive`
          are set, which plain crypto price-crossing markets like
          KXBTC15M never have.
    - [ ] No time-to-close-aware exit rule — `check_exits()`'s three
          opt-in layers (take-profit/stop-loss/auto-exit) are all purely
          price-driven; nothing forces a decision once a position's
          remaining runway drops below some floor, regardless of where its
          P&L currently sits.

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
- [ ] This runs today only under local `ddev` on one machine — no real host,
      TLS domain, process supervisor, or uptime guarantee beyond ddev's dev
      containers. Decide and build a real deployment target before real
      capital depends on this process staying up.
- [ ] `data/*.db` (bankroll, positions, kill-switch state, signal log) is
      single-file SQLite with no backup/retention policy — fine for a local
      paper POC, not once a lost file means lost real financial state.
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

- [ ] **Switch the whale threshold from dollars to contract count (or add
      one alongside).** Measured 2026-08-17 across 145,785 real captured
      prints: a dollar gate is geometrically biased toward near-certainty,
      because $2,500 buys 125,000 contracts at 2c but only 2,505 at 99.8c.
      Its clear rate climbs monotonically with price (0.00% below 0.50,
      0.85% at >=0.98), and **44 of the 58 prints that clear $2,500 (75.9%)
      sit at unit cost >=0.95** — the band that bleeds. Mean unit cost of
      everything it selects: **0.926**. A `count >= 5,000` selector plus the
      tradeable-range filter lands at mean 0.759 with 27.3% inside the only
      profitable band, against 8.6% today. **This is the single biggest
      measured lever on the 70%/70% target.** Left unshipped deliberately —
      it changes what the app considers a whale, which is a strategy call.
      Full tables and the implementation note are in
      `docs/next-session-pickup-2026-08-17.md`.
- [ ] **Make the diagnostics epoch-aware — blocks trusting any other
      number.** `check_price_band_adherence` and
      `check_threshold_integrity` judge history against *today's* config, so
      a config change 8 hours ago makes them report FAIL on trades that were
      compliant when placed. Real cost: `price_band_adherence` reported 72%
      out-of-band; judged against the band actually live at each trade's
      timestamp it was 4 of 39. `performance_by_epoch` already does this
      correctly via `config_performance.applied_changes` — reuse it.
- [ ] **Find the four-entry gate bypass.** Four real entries at unit costs
      0.97, 1.00, 0.20, 0.97 (08/16 21:26–22:25), one at `conf 0.25` against
      a 0.495 threshold, so they skipped both the price band and the
      confidence gate. Prices in the reason string match the recorded
      prices, and `shadow_mode`/`market_strategy` are ruled out. The
      `01b126c` invariant makes the class unreachable going forward, but a
      gate that *can* be skipped is worse than no gate.
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
- [ ] **Finish breaking up `main.py`.** `services/app_state.py` +
      `routers/diagnostics_routes.py` established the pattern (see
      `status.html` phase 114) and took it 5,415 → 5,124 lines. The
      remaining ~69 routes are mechanical by the same recipe. The genuinely
      hard part is the other half: `trading_loop` (605 lines),
      `_fetch_markets` (258), `_fetch_live_status` (204) — these have real
      entanglement with tick ordering and shared state, and want extracting
      one at a time with the suite green between each, not in a batch.

## P4 — Nice-to-haves

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
- [ ] A real, permanent fix for the close_time-mutability gap
      (`docs/roadmap-archive-2026-08-16.md` has the full incident): Kalshi's
      `market_lifecycle_v2` WebSocket channel (`close_date_updated`,
      `determined`, `settled`, `activated`/`deactivated` events,
      `docs/kalshi/market_lifecycle.md`) would let `market_catalog` learn
      about a status/close_time revision the instant Kalshi emits it,
      instead of only catching it on the next scan or the real-time
      confirmation pass that currently bounds (not eliminates) the
      staleness window. **Half shipped 2026-08-17**: the subscription now
      exists (`KalshiTradeWebSocketClient(subscribe_lifecycle=True)`,
      config-gated via `kalshi.market_lifecycle_stream_enabled`) and
      `close_date_updated` is wired into the **in-memory overlay**
      (`state["markets"]`, via `main._process_stream_lifecycle`) — verified
      live against real messages for every documented `event_type`,
      including one real `close_date_updated` applied with zero exceptions.
      Still open: wiring the same events into `market_catalog`'s SQLite
      rows (this fix only reaches the live tick's in-memory market list,
      not the persistent catalog), plus `determined`/`settled` into the
      real settlement pipeline (`market_history.record_outcome`,
      `candidate_log`/`market_analyst_agent`'s `resolve_from_market_results`,
      `settlement_edge.resolve_window` all still REST-only) — see
      `docs/next-session-pickup-2026-08-17.md`'s "Recommended shape" section
      for the full verification detail and why settlement wiring was
      deliberately left for its own dedicated pass.

## Shipped

Everything else has shipped — P0 (safety gates), the full dashboard/UX
overhaul, active position management, whale-tracking maturity (real trade-
tape provider, composite confidence scoring, advisory/recommendation
engine, market analyst agent, position netting, calibration, per-series
overrides), reliability/engineering hygiene (test suite + CI, official SDK
migration, rate-limit correctness), and dozens of live-reported bugs found
and fixed session by session. `static/status.html` (`/status`) is the
complete, phase-by-phase record — 109 phases and counting. For the detailed
prose version of this file as it stood before each condensing pass, see
`docs/roadmap-archive-2026-08-09.md` and `docs/roadmap-archive-2026-08-16.md`.
