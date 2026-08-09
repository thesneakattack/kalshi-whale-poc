# Roadmap: comprehensive, dummy-proof paper trading terminal

Guiding principle: someone who has never touched a prediction market or a
trading interface before should be able to open this app and understand
*what they're looking at*, *why it did what it did*, and that *no real money
is ever at risk* unless they deliberately configure it to be. Everything
below is measured against that bar, not against "does it technically work."

This is a living to-do list, not a snapshot — check items off in place and
add new ones as they turn up. For what's already built and in what order,
see `static/status.html` (`/status`) — this file is forward-looking, that
one is the historical record. Checked-off items here are kept to a short
summary, not a full narrative — the detailed "why," verification steps, and
any bugs found along the way live in `static/status.html`'s matching phase
and in `git log`/`git show` for this file. (2026-08-09: condensed the
back half of this file down to that standard — it had drifted into full
narrative for a long stretch of P2 entries; nothing was lost, it's all
still in git history and `status.html`.)

## Path to production

P0 below is now **fully shipped** — the code-level gates around real money
are real and complete. That's necessary, not sufficient: going from "the
gate works" to "flip it for real" still has open operational questions,
listed here rather than buried in P0's own now-closed checklist.

- [x] Real trading gate is genuinely load-bearing: `create_order`/
      `cancel_order` verified against Kalshi's current order schema and
      migrated to the official `kalshi_python_async` SDK, `trading_enabled`
      defaults `false` and can only flip via a typed in-app confirmation
      phrase (`POST /api/trading/enable`), CORS restricted to real origins,
      balance/position field names verified against a connected account,
      kill switch + bankroll persist across restarts.
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
      one. P4 already has a notifications item queued; worth promoting
      ahead of a real-money flip specifically for these three cases.
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
- [x] Docs drift found while writing this section: `CLAUDE.md`'s Safety
      Invariants section and `README.md`'s "Safety notes for when you go
      live" section both still described CORS as wide-open and the order
      schema as unverified, and the README's "Later" table described shadow
      mode as not yet built — all three had shipped (see P0 below). Fixed
      same day.
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

## P0 — Safety & correctness (before anything else)

- [x] Verified real Kalshi balance/position/fill field names against a
      connected account (`position_fp`, `count_fp`, `yes_price_dollars`, ...
      differ from the original guesses, which silently rendered blank) —
      also found and fixed a real 401 bug (signed message was missing the
      `/trade-api/v2` prefix).
- [x] Verified the `create_order`/`cancel_order` schema against Kalshi's
      current docs and rewrote it to the current `POST
      /portfolio/events/orders` shape, covered by 4 request-shape tests.
- [x] Added an in-app confirmation step before real trading can be enabled —
      `POST /api/config` structurally can't touch `trading_enabled`; only
      `POST /api/trading/enable` can, requiring a connected account plus an
      exact typed confirmation phrase.
- [x] Paper broker state and the risk manager's daily-loss/kill-switch state
      persist across restarts (`data/paper_broker.db`, `data/risk_state.db`),
      verified with a real `ddev restart`.
- [x] Tightened CORS to the DDEV hostname + `localhost:8000`, overridable
      via `ALLOWED_ORIGINS` in `.env`.
- [x] Built shadow mode (`services/shadow_mode.py`) — runs the real
      strategy's gates sized against the real account's actual balance,
      logs to `data/shadow_mode.db`, never calls `create_order`.

## Phase 0.5 — Dashboard & UX overhaul, Kalshi Pro-inspired

Kalshi shipped its own professional terminal, **Kalshi Pro**, as a public
beta on 2026-07-13 — researched directly (launch post, help docs, a
third-party review) as a reference for "robust, informative, easy to
navigate" at this kind of app. Its real feature set: a multi-market
**Canvas** workspace, per-market **order-book depth** with inline order
management, an **Active Markets Screener** (~2,000 markets ranked live), a
continuous **trade tape**, and **TradingView-caliber charting** with
on-chart TP/SL — built for density and speed, not a first-timer. That's a
real tension with this file's own dummy-proof principle. Resolution: not a
tab-level split but a **panel-level** Simple/Advanced toggle, defaulting to
Simple — a first-timer never has to leave Portfolio for a plain-English
read, and someone who wants Kalshi Pro-style density clicks "Advanced" on
just the panel they care about. Also worth remembering: Kalshi Pro's
screener "shows you what's moving, not whether it's mispriced" — this app's
whale-follow divergence signal is a what's-mispriced opinion theirs has none
of; borrow their layout, don't bury the one thing this app does that theirs
doesn't.

- [x] Shared Simple/Advanced toggle pattern (`isAdvanced`/`toggleAdvanced`/
      `advToggleHTML`), persisted per panel via `localStorage`.
- [x] Kalshi-accurate terminology + real Event/outcome grouping — positions/
      trades/fills label contract count/entry/current price explicitly;
      sibling-outcome markets group under one event header via `get_event()`.
      Follow-up: fixed a real `event.get('subtitle')` vs `sub_title` bug that
      had silently rendered `None` since it shipped.
- [x] Multi-outcome event cards rebuilt (`eventGroupCardHTML`) to match
      Kalshi's real pattern — one card, compact outcome rows sorted by
      probability — instead of N duplicate full cards.
- [x] Market discovery was fundamentally broken, not just under-filtered —
      Kalshi's MVE/combo markets vastly outnumber real ones in the API's
      default ordering, so even a 50,000-market flat browse could return
      zero with real volume. Fixed by switching to series-based discovery
      (`get_series_list(include_volume=True)` → `get_markets(series_ticker=...)`
      against top active series); also fixed an SDK gotcha where passing
      `mve_filter=None` explicitly silently changed the result set.
- [x] Follow-up, direct report ("Kalshi itself is showing 50 live markets
      right now" but only one showed here): series-based discovery fixed
      *what* gets browsed, but `get_top_volume_markets` still picked the
      final watchlist via a flat top-n sort with no diversification, so one
      8-market golf tournament could fill the entire watchlist. Shipped
      round-robin selection across distinct events instead of a flat cap —
      self-sizes with no hardcoded number. Bumped several related caps that
      hadn't been revisited since an 8-market watchlist (`watchlist_size`
      8→20, top-series pool 15→30, trade-tape fetch caps, fills page size,
      long-run title caches). Verified live: watchlist went from 1 distinct
      event / 0 live markets to 20 distinct events / 4 confirmed live.
- [x] Per-market drill-down modal — Simple (best bid/ask/spread) and
      Advanced (full depth ladder) via `GET /api/markets/{ticker}/orderbook`.
- [x] Price history in the drill-down (`GET .../candlesticks`) — sparkline
      (Simple) or real OHLC/volume bars (Advanced), with null `price.*`
      periods carried forward from the last known bid/ask midpoint instead
      of plotting a misleading drop to zero.
- [x] Recent trades in the drill-down (`GET .../trades`, last 15).
- [x] Full-exchange trade tape, scoped to the current watchlist —
      `_fetch_trade_tape()` fires one `get_trades()` per watched market
      concurrently. Simple: top-quartile-by-size in plain English. Advanced:
      full tape with a min-size filter.
- [x] Trade Log / Decision Feed Advanced tables — sortable/filterable, with
      unrealized P&L and a `parseConfidence()` helper pulling the raw
      confidence number back out of `Trade.reason`'s formatted text.
- [x] Signal feed filters (time range, buy/sell, sort by recency or
      "impact" = size × confidence, position-grouping toggle), matched
      against Polywhaler/WhaleScanr's proven filter set.
- [x] Signal card enrichment — impact tag, position size in $ and contracts,
      a "N× prints" stealth tag via the grouping toggle. Skipped 24h price
      change on purpose: no per-ticker historical price data exists in bulk
      without fanning out one API call per ticker on every poll.
- [x] Browsable Signal History panel (`signal_log.recent()`/`total_count()`,
      `GET /api/signals/history`) — every flag and how it settled, not just
      a rolled-up win rate. Loads on tab-open/filter change, not every 5s,
      so browsing doesn't reset itself.
- [x] Potential payout shown on every position/trade/fill row
      (`payoutHTML()` — contracts settle to $1/$0).
- [x] `renderPositions` rewritten into a real grouped table (per-event group
      + Total row) plus a whale-confidence badge and 🐋 reason icon.
- [x] Market search/browse in a new Config tab (`GET /api/markets/search`,
      series-based), checkbox-multi-select merges into
      `kalshi.markets_watchlist`.
- [x] Dense, sortable Advanced screener table (`renderScreenerTable`),
      shared across Terminal/Markets/Whale Watch, each with independent
      sort state. Deliberately skipped order-book-depth and true 5-min
      volume columns rather than fake them — not enough data fetched per
      tick to back either honestly; 24h volume shown instead.
- [x] Price-change indicators — after a direct correction ("they shouldn't
      be transient... they should stay visible and update as they change"),
      settled on a persistent per-ticker session baseline: no badge on first
      sighting, then a continuously-visible, continuously-updating ¢/%
      delta rather than a one-tick flash.
- [x] Real "LIVE" badge, not a timestamp proxy — Kalshi's live status comes
      from a separate milestone/live-data system
      (`get_milestones()` → `get_live_data()`, `details.widget_status`).
- [x] Visible staleness/connectivity state — `#connectivity-badge` tracks
      consecutive `refresh()` failures and shows a pulsing "⚠ CONNECTION
      LOST" banner. Also fixed a real gap: the shared `fetchJSON` helper
      didn't check `res.ok`, so a 500 with a valid-JSON error body was
      silently treated as a real update.
- [x] Fixed the header equity strip ignoring the Portfolio account-mode
      toggle, and a real bug found alongside it: `real_balance_history` was
      storing raw cents instead of dollars.
- [x] Real watchlist/pinned-markets UI for the existing
      `kalshi.markets_watchlist` config field (previously YAML-only).
- [x] Self-serve "Reset Paper Account & Logs" button in a new Config-tab
      Danger Zone. Follow-up, direct request: `POST /api/reset` now takes
      `{paper, shadow, signal_log}` flags so shadow/whale-track-record data
      can be wiped independently too.
- [x] Open Positions → full market-detail modal, direct request ("all the
      data on the market landing page on Kalshi itself") — new `GET
      /api/markets/{ticker}/detail` bundles market + event + siblings in
      one call.
- [x] `/api/state` data-efficiency pass, direct request. Measured first:
      `data/*.db` was trivially small; the real cost was `/api/state` at
      43.8KB polled every 5s regardless of whether anything changed. Fixed
      with a generation counter used as an ETag (unchanged poll = a 304, not
      a re-fetch) and response memoization per generation; slimmed
      `account.fills`/`account.positions` to just the fields the UI reads.
      Caught two real bugs along the way: `GET /api/markets/search` wasn't
      bumping the generation counter (fresh titles appeared to "revert"
      until the next real poll), and the market-detail modal's outside-click
      handler could get its mousedown/mouseup retargeted by an in-place
      `innerHTML` swap mid-interaction, silently closing the modal.
- [x] Reassessed Markets vs. Whale Watch — kept as two tabs with genuinely
      distinct jobs (pure discovery vs. whale-annotated + whale-specific
      panels) rather than merging behind a filter toggle.
- [x] Bounded-height, scrollable list panels (`.scroll-panel`) for all five
      feed panels.
- [x] Follow-up, direct report: those panels reset `scrollTop` to 0 on every
      5s poll via full `innerHTML` rebuild. New `renderFeedListSmooth()`
      only inserts genuinely-new prepended items, leaving existing DOM nodes
      (and scroll position) untouched; falls back to a full rebuild when
      that invariant doesn't hold (filter/sort/grouping change).
- [x] Thin, semi-transparent scrollbars everywhere, direct request.
- [x] Search/browse markets directly in the Markets tab, direct report (the
      existing search only lived in the Config tab, scoped to watchlist
      management) — new panel, same endpoint, results click straight into
      the detail modal with a secondary "+ Pin" button.
- [ ] Revisit the 5s polling model (`setInterval(refresh, 5000)`) once any
      Advanced view needs sub-poll freshness — partially addressed by the
      ETag/304 pass above (an unchanged poll is now nearly free), so this is
      really a push-vs-poll latency question (WebSockets, considered and
      deferred) rather than payload waste.

Scope boundary, decided explicitly: this app has zero *manual* trading
anywhere — every position is opened by an automated strategy, never a
person clicking "buy." Kalshi Pro is fundamentally a manual terminal with
automation as an assist, the opposite emphasis; confirmed staying
automated-only for this phase. (Exit management below is still automated
too — config-driven rules decide when a position closes, not a click — so
this boundary still holds even though positions are no longer left open
unmanaged after entry.)

## Active position management & Trading History

Direct request: "once positions are opened they are never monitored/
changed... kalshi lets you set sell prices so if say you open a position at
50c you can set to cash out at 75c instead of 99c and profit." Before this,
`PaperBroker` had no exit mechanism at all — a position sat untouched from
entry until settlement, gains were never locked in, losing positions were
never cut.

- [x] `PaperBroker.close_position(ticker, exit_price, reason)` — sells a
      position back at a given price instead of only holding to $1/$0
      settlement, reusing the existing `trades` table (a close is just a
      trade whose `reason` starts with `"closed: "`).
- [x] `FollowTheWhaleStrategy.check_exits()`, run every tick regardless of
      whether a new signal arrived. Priority order per open position: (1)
      **market settlement** — unconditional, closes at the terminal $1/$0
      price the instant it resolves; (2) **take-profit** (opt-in); (3)
      **stop-loss** (opt-in); (4) **sentiment-reversal exit** (opt-in) —
      closes if whale sentiment on this ticker has flipped decisively
      against the held side; (5) **auto-exit** (opt-in) — a tweakable
      multi-factor "exit confidence" blending unrealized P&L, sentiment
      reversal strength, and staleness into one composite score. 30 new
      tests across `tests/test_paper_broker.py`/`tests/test_strategy_engine.py`.
- [x] `main.py` wiring — `check_exits()` runs once per tick;
      `_fetch_markets` now always includes every open position's ticker
      even if it rotated out of the watchlist, so a held position's price
      updates and exit triggers never go stale.
- [x] New Trading History tab, direct request (graphs, win/loss rates, why
      auto-management closed a position, config metadata to inform tuning).
      New `services/trade_analytics.py` (pure functions, no new
      persistence): classifies each closed trade's `close_type` by parsing
      the reason-string conventions `close_position`/`check_exits` already
      write, pairs each close back to its entry, computes cost basis/cash
      back/hold time/"left on table" (framed explicitly as hypothetical,
      never a claim). New `GET /api/trading-history` (paginated rows,
      aggregate summary, cumulative P&L curve, and sample-size-hedged
      **insights** — explicitly not a recommendation engine, never writes
      config). 18 new tests.
- [x] Found and fixed a real settlement bug: `terminal_price` was computed
      as `1.0 if won else 0.0`, which for a **no** position double-applied
      `close_position`'s own side inversion and silently paid $0 on an
      actual win — had already mis-paid 2 real trades before a failing test
      caught it.
- [x] Found and fixed a significant pre-existing bug while tracing the one
      above: `open_position()` charged `size * price` unconditionally, but
      `price` is always the *yes* price by convention — a **no** position's
      real cost is `size * (1 - price)`. This under-charged every no-side
      entry ever opened and manufactured phantom profit. Fixed in
      `open_position` (and its bankroll-capping math) and the identical bug
      in both strategies' position-sizing. New `PaperBroker.cost_basis()` is
      now the single source of truth. User's explicit call: fix the code and
      reset the paper account rather than retroactively correct history.
- [x] Deep-scan review, requested directly ("make sure you're not missing
      any gaps or errors"), found and fixed four more real bugs: `evaluate()`
      had no check for a ticker with an already-open position (could
      silently overwrite it, discarding cost basis — confirmed this had
      actually happened); no check for a market that had already resolved
      before opening a new position; four separate frontend instances of the
      same no-side dollar-math bug class (Positions panel, ELI5 panel, whale
      signal card, Trade Log), including one sign-flip variant in the
      Advanced Trade Log's per-row P&L; and a whale-print disclosure that
      silently snapped shut on every poll (fixed by capturing/restoring
      expanded state across rebuilds). Full suite: 171 passing (was 127).

## P1 — Actually dummy-proof (a first-timer understands what's happening)

- [x] First-run walkthrough + persistent glossary/"how prediction markets
      work" explainer, combined into one Help modal (`#help-backdrop`) — a
      plain-English primer plus an 11-term glossary written in terms of how
      *this app* uses each one. Auto-opens once on a new browser
      (`localStorage`), always reachable via a "Help" link afterward.
- [x] Extended the plain-English pattern to Strategy Decisions —
      `plainEnglishSkipReason()` turns `strategy_engine.py`'s technical skip
      strings into full sentences for Simple mode; Advanced still shows the
      raw string; falls back to the raw string for anything unrecognized.
- [x] Global, hard-to-miss "not real money" indicator — a full-width sticky
      `#real-money-banner`, keyed off `account.trading_enabled` (the field
      that genuinely gates real orders): calm green "🧪 PAPER TRADING" by
      default, pulsing red "⚠️ REAL TRADING IS ENABLED" only once real
      orders are actually possible.
- [x] Config tab rebuilt as a real multi-section control panel, direct
      report ("hard to make sense of for a like-I'm-5 user") — ten
      collapsible accordions with plain-English subtitles, plus a
      data-quality badge system (🧪 sim data, 📉 needs history, 🆕 new)
      explained once in a legend rather than repeated inline.

## P2 — Whale-tracking maturity

- [x] Added a real named whale-watcher provider beyond the simulator.
      Researched first: Kalshi has no public trader identity or leaderboard
      (anonymous member-to-member trades), so unlike Polymarket's
      wallet-based trackers, a real provider can only be size-based. New
      `services/whalewatchers/kalshi_trade_tape.py` classifies a real trade
      as a whale print once its side-aware notional size clears a
      configurable threshold — zero extra credentials or API calls, reuses
      data already fetched for the UI trade tape. Confidence scoring
      extracted into a shared, noise-free `composite_confidence()`. Direct
      follow-up once verified: made this the default provider instead of
      the simulator ("there's no reason to have the simulator enabled by
      default"). 15 new tests.
- [x] Widened market coverage (`watchlist_size` 20→50, with dependent caps
      scaled in tandem) and added a genuine `kalshi.live_markets_only`
      discovery-time filter, distinct from the two live-only gates that
      already existed at the strategy/simulator level — direct request,
      resolved two real design forks first (shrink the watchlist rather
      than backfill with non-live markets; check live status across the
      *wide* candidate pool, not just the final watchlist). Extended to
      search too. 6 new tests.
- [x] Hardened confidence scoring, two tracks confirmed directly before
      building. Track 1: `composite_confidence`'s depth factor switched from
      a hard cap to exponential saturation, its context factor from a
      single-outlier-fragile ratio to percentile rank, and a new 5th
      "agreement" factor (does the same market's recent history agree with
      this print) — weights rebalanced accordingly. Track 2: re-confirmed
      rule-based-over-ML with real numbers (5,738 signals seen, only 9
      resolved — not enough to fit anything). New
      `services/confidence_calibration.py`, gated inside the function,
      splits resolved real signals into tertiles per factor and reports the
      win-rate gap between them, with a real bug caught by its own tests
      (near-constant factors need ≥3 distinct values before tertile-
      splitting means anything). 24 new tests. Full suite: 297 (was 285).
- [x] Schedule-aware light polling for live-market status, plus a broad,
      incrementally-scanned market catalog — a multi-round fix. Direct
      request: live status was being re-derived from 2 fresh API calls per
      event on *every* tick with no memory of prior results; also found a
      real inverted window-bounds bug along the way. Built a
      `live_status_cache` (5-min re-poll interval, terminal states never
      re-polled) plus a milestone-gated schedule fallback, tightened after
      direct correction not to infer "live" for markets with no confirmed
      milestone. User then found the deeper issue independently (real
      Kalshi showing 72-86 live markets, this app finding ~0) — root cause
      was upstream of live-status entirely: discovery only ever sampled the
      top 40 series by volume, and almost none of that sample was ever live.
      Fix, shaped directly by the user: new `services/market_catalog.py`, an
      incrementally-scanned (40 series/tick, least-recently-scanned first),
      near-term-only (1 week past–3 weeks future) catalog
      (`data/market_catalog.db`) that both live-only discovery and search
      now draw from. Verified live: 0 → 24 confirmed-live real markets.
      26 new tests. Full suite: 323 (was 297).
- [x] Series/category grouping metadata — real titles/categories already
      existed via `get_series_list()`, just weren't surfaced; new
      `_series_meta_map()` exposes a scoped ticker→{title, category, tags}
      lookup (caught and fixed a real bug before shipping: an early version
      dumped the entire ~9,400-entry cache into every `/api/state` response).
- [x] Manual per-series exclusion list (`strategy.excluded_series`) on top
      of the automatic win-rate cutoff — Kalshi's trade tape is anonymous,
      so "series" (the same grouping the automatic filter already uses) is
      the honest, implementable unit to exclude by.
- [x] `strategy.live_markets_only` trading gate — skips/declines any signal
      whose market isn't currently live, reusing the dashboard's own LIVE
      status lookup rather than a second definition of "live."
- [x] Follow-up: `whale_signal.live_markets_only` on the simulator itself —
      decides whether a print gets *generated at all* for a non-live
      market, one level upstream of the trading gate above.
- [x] Config-versioned performance tracking (`services/config_performance.py`)
      — fingerprints `strategy.*`, closed positions inherit the fingerprint
      active at entry. Built as the direct foundation for the advisory
      engine below, which superseded it.
- [x] The advisory/recommendation engine, direct request: "I want a
      recommendation engine/advisory system... keep it disabled until that
      data threshold has been reached." Full design in
      `docs/advisory-engine-plan.md`, then `services/advisory_engine.py` —
      rule-based, not ML; upgrades `compute_insights()`'s hedged prose into
      a concrete suggested value once a variant clears a minimum-resolved-
      trades gate enforced *inside* the function itself, not the route or
      UI. New `advisory.*` config (`enabled` default `false`), routes
      (status/recommendations/apply/applied-changes — apply is manual-click
      only, re-validates fresh, writes an audit row), and a `POST
      /api/config` rejection guard for `auto_apply_enabled` mirroring
      `trading_enabled`'s. Auto-apply's own confirmation-phrase endpoint is
      deliberately not built yet.
- [x] Scaffolding for a future ML agent to work *alongside* the rule-based
      engine, direct request — `services/ml_feed.py`'s
      `build_context_snapshot()` is the shape of a future export, not wired
      into any route yet. Nothing beyond it; revisit once the rest of the
      app is otherwise done.
- [x] Whale-size threshold made relative to each market instead of a flat
      configured range, and market selection weighted by volume instead of
      uniform-random — WhaleScanr's real methodology, researched directly
      ("roughly the size only the top few percent of trades reach, plus an
      absolute floor").
- [x] Composite confidence scoring for the simulator (`_score_confidence()`)
      — from a single size-only factor to a weighted composite (size
      relative to depth, price unusualness, proximity to close, relative
      market busyness) matching Polywhaler's stated "Insider Score" shape.
      12 new tests.
- [x] (Stretch) Persistent flow clustering — `signal_log.find_clusters()`
      groups same-ticker/same-side signals within a time window and a
      size-similarity ratio into probable-same-actor clusters, confidence-
      scored and capped, never claiming verified identity (Kalshi's trade
      tape has no real identity to claim). New "Possible Accumulation" panel.
- [x] Real market-data storage + a second, whale-independent paper strategy,
      direct request: "market data is real, the whale data is fake...
      there's no reason why I shouldn't start storing and analyzing market
      data now." New `services/market_history.py` (real per-market snapshot
      log, zero extra API cost) and `services/market_strategy.py`
      (`MarketNativeStrategy` — momentum continuation, zero whale-signal
      input, off by default). Needed `PaperBroker`/`RiskManager` to both gain
      an optional per-instance `db_path` so the two strategies run
      independent capital pools without colliding. Backend-only for now, no
      dashboard panel — a direct, asked-not-assumed scoping choice. Caught a
      real bug: a `db_path` default bound at function-definition time
      silently broke test-path redirection. Full suite: 262 (was 275→262
      reflects a prior batch; see git log for exact sequencing).
- [x] Fixed the watchlist's parent/child grouping, direct report: "only
      parent markets should count against the watchlist" — confirmed 42 of
      47 slots were individual golf head-to-head pairings from one
      tournament. Two designs tried and rejected before shipping (grouping
      by `event_ticker`, then by Kalshi's own series-level hierarchy) before
      landing on `series` as the watchlist unit: selecting a series pulls in
      its entire tournament's worth of pairings for one slot, optionally
      capped by `kalshi.max_children_per_parent`. 13 tests. Full suite: 326.
- [x] Reflected that series-level grouping in the dashboard itself, direct
      follow-up — Terminal's flat market list and the card views only
      grouped by individual event; new `.series-header`/`.series-section`
      treatment so a whole tournament collapses under one header instead of
      20+ disconnected rows/cards.
- [x] Fixed a real, confirmed-live bug the grouping UI work surfaced: every
      YES/NO price showed exactly 50¢, never moving — direct report. Root
      cause: `market_catalog` rows (the entire candidate source for
      live-only discovery) only ever stored schedule/title/volume metadata,
      no price fields, so every catalog-sourced market silently fell through
      to a `0.5` fallback. Fixed by re-fetching real market objects, batched
      per selected series, after `round_robin_select` picks the final
      watchlist. Full suite: 328.
- [x] Collapsed redundant inversion pairs out of child-market display,
      direct request — for a genuine 2-sibling `mutually_exclusive` event
      (confirmed via `get_event()`'s own field, previously fetched and
      discarded) where the two sides' prices sum to ~1.0, show only the
      higher-probability side's row rather than two rows that are 100%
      derivable from each other. Independent props sharing an event
      (confirmed NOT to sum to ~1.0) and genuine multi-outcome markets stay
      fully expanded. Caught a real staleness gap: already-cached events
      never got backfilled with the new field, fixed to self-heal as each
      event naturally reappears. Full suite: 338.
- [x] Data & presentation review, direct request ("make sure you're using
      all useful data fields... do a deep review... avoid missing
      anything") plus a concrete complaint (Terminal's Controls column
      wasting space, fixed at 260px). Widened positions/fills fields (fees,
      timestamps), added a new Order History panel
      (`GET /api/account/orders`, previously implemented but never called),
      surfaced already-fetched-but-unrendered market-detail fields
      (liquidity, last price, open time, rules), showed
      `product_metadata.competition` on multi-outcome cards, added bid/ask
      spread bands + open-interest delta to the candlestick chart. 16 new
      tests. Full suite: 347.
- [x] Fixed a real coverage gap in Config Tuning Hints, direct report: "the
      config tunings hints section... doesn't seem to give me actual advice
      at all." Confirmed live: 84 of 103 real closed trades (81%) were
      `sentiment_reversal`, with zero heuristic covering it or its
      market-native analog `momentum_reversal` — every other insight
      correctly had nothing to say against this app's actual data shape.
      Added two new insights matching the existing pattern exactly. 4 new
      tests. Full suite: 351.
- [x] Fixed the header strip showing all-time P&L labeled "Unrealized
      P&L," direct report (2026-08-09): "I get values for bankroll, equity,
      and unrealized P&L, but the open positions themselves aren't shown,
      what a lie." Confirmed live: 0 open positions, `equity == bankroll`
      exactly (correct), yet the header showed +$3,806.23 "Unrealized P&L."
      Root cause: `renderHeaderStrip()` computed `equity - starting_bankroll`
      (cumulative all-time P&L) instead of `equity - bankroll` (exactly
      `total_unrealized_pnl` by `PaperBroker.equity()`'s own definition,
      correctly $0 with nothing open). One-line fix, no backend change
      needed — all-time P&L already has its own correct home on the Trading
      History tab. Full suite: 351 (frontend-only).
- [x] Deep, no-simplification research into how prediction markets actually
      work, direct request ("I need you to be a single source of truth on
      how these markets work... don't simplify anything"), then a
      systematic pass comparing this app's existing algorithms against that
      research and adjusting them — a second direct request ("compare these
      strategies and logic and make adjustments to all the relevant
      algorithms... apply this contextual knowledge to confidence, whale
      watching, position management"). Two docs:
      `docs/prediction-markets-research-reference.md` (market-mechanism
      theory + academic evidence, Kalshi-specific mechanics including the
      real taker-fee formula and trade-tape anonymity — both verified live
      against the running app, not just scraped docs — and the regulatory
      landscape) and `docs/prediction-market-strategy-alignment-plan.md`
      (six concrete gaps between the research and this app's live code,
      phased implementation plan). What shipped from it:
      - Real Kalshi taker-fee modeling (`services/kalshi_fees.py`,
        `ceil_$0.0001(0.07 × contracts × price × (1-price))`, verified
        against 3 real fills) wired into `PaperBroker.open_position()`/
        `close_position()` (new `entry_fee`/`fee` columns, netted out of
        realized P&L) and surfaced on Trading History (Fees column, Fees
        Paid stat card).
      - Two new composite-confidence factors reflecting Barclay & Warner's
        stealth-trading research: `cluster_factor` (reuses
        `signal_log.find_clusters()`, defaults to 0.0 — "isolated" is
        itself informative, unlike the other factors' neutral defaults)
        and `trend_factor` (reuses `market_history.momentum()`, defaults
        to neutral 0.5). All 7 factor weights rebalanced,
        `confidence_calibration.py` updated to match.
      - Fixed-width confidence-band calibration
        (`_confidence_calibration_bands()`) alongside the existing
        tertile-split report — reflects the hit-rate-vs-calibration
        distinction (a Brier-score-style concern) the research surfaced.
      - Favorite-longshot-bias-aware entry threshold in
        `strategy_engine.evaluate()` — a configurable longshot price zone
        (`strategy.longshot_price_threshold`, default ≤15¢/≥85¢) now
        requires extra confidence (`strategy.longshot_entry_threshold_bonus`)
        before entering, since FLB is confirmed worse for takers and this
        app's real order path is taker-only.
      - New `services/market_analyst_agent.py` — the requested "doctorate-
        level prediction market trader" agent: an LLM (tool-forced
        structured output via the Anthropic SDK) reads a market's real
        title/rules/price plus this app's own accumulated whale/advisory
        track record and forms an independent probability estimate,
        recorded and later graded against real settlement (hit rate +
        Brier score). Advisory-only — never places a trade or writes
        config — and dual-gated (`market_analyst.enabled` config flag AND
        a real `ANTHROPIC_API_KEY`) so it's fully inert by default. Wires
        the previously-inert `ml_feed.build_context_snapshot()` into
        `main.py` for the first time. New Config-tab section and
        History-tab panel (Analyses/Resolved/Hit Rate/Brier Score), both
        live-verified via `selenium-chrome`. 48 new tests. Full suite: 399
        (was 351).
- [x] Market analyst agent redesigned from an automatic per-tick background
      scan to an on-demand button, same day as it shipped — direct pushback:
      "would it make more sense to let the heuristic analysis engines run
      and then just have an option to weave in the market analyst agent...
      with a button click versus fully automated?" It's the first thing in
      this app that spends real money per call; `main.py`'s
      `_maybe_run_market_analyst()` → `_run_market_analyst_for_ticker()`,
      now only reachable via a new `POST /api/market-analyst/analyze`, only
      called by a new "🔎 Analyze this market" button in the per-market
      detail modal. Removed `market_analyst.max_analyses_per_tick` (no
      longer meaningful). Also: "leverages its own history" — the agent's
      own `stats()` (hit rate, Brier score) now feeds back into its own
      prompt (`build_prompt()`'s new `own_track_record` param) so it can
      self-calibrate. Also: "inform the various engines... without
      consuming AI tokens" — `composite_confidence_breakdown()` gained an
      8th factor, `analyst_factor` (agreement with the agent's own lean via
      a cheap DB read, `analyst_lean()`, never a new API call), wired into
      *both* real callers — confirmed directly against "is this also in the
      real whale watcher, not just the simulator?": `kalshi_trade_tape.py`
      (real, active-by-default provider) and `market_strategy.py`'s
      `_entry_confidence()` (conditionally included only when a fresh
      estimate exists, so ordinary momentum-only entries stay unaffected).
      15 new tests. Full suite: 414 (was 399).

## P3 — Reliability & engineering hygiene

- [x] Automated test suite — 35+ tests (now 351) covering every service
      module, each isolating its own SQLite file via `monkeypatch`.
- [x] Basic CI — `.github/workflows/tests.yml` runs the suite on every
      push/PR to `main`.
- [x] Migrated fully to Kalshi's official `kalshi_python_async` SDK
      (3.27.0) — superseded an earlier same-day "decided against it"
      finding that turned out to be `pip index versions` silently resolving
      to a stale, Python-3.11-only release with real validation bugs since
      fixed upstream.
- [x] Exponential backoff on `429 Too Many Requests`, per Kalshi's
      rate-limit docs — `call_with_backoff()` wraps SDK client calls.
- [x] Exchange open/closed status badge in the header (`GET
      /exchange/status`) — distinguishes "market's closed" from "strategy
      is stuck."
- [x] Fixed intermittent 403/404s on the dashboard — root cause was ddev's
      `web` and the custom `fastapi` service both auto-registering a
      Traefik router for the same hostname, an unstable tie-break. Real
      fix: `main.py` is API-only now, `web` is the sole public entrypoint,
      structurally impossible for the collision to recur.
- [x] Mobile/responsive pass — zero `@media` breakpoints before this. New
      `@media (max-width: 860px)` stacks Terminal's 3-column layout,
      wraps the header/util-bar, gives wide tables their own horizontal
      scroll. Caught two genuine overflow bugs at real narrow viewports
      (a flexbox `min-width: auto` gotcha, one `white-space: nowrap` tag).
- [x] Accessibility pass, partial and honestly scoped — `aria-label`s on
      icon-only controls; colorblind fallback confirmed already fine
      (every yes/no indicator pairs color with text). Full keyboard
      navigation for clickable cards/rows is a real, larger gap still open.
- [x] Follow-up bug, direct report: the 🐋 disclosure inside a clickable
      position row also opened the market-detail modal underneath on
      click (event bubbling) — fixed with `event.stopPropagation()`.

## P4 — Nice-to-haves

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
