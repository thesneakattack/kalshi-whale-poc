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
  at all). 11 new tests. Full suite: 442 (was 433). See
  `static/status.html` phases 64-67.
