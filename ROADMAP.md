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
summary rather than a full narrative — the detailed "why," verification
steps, and any bugs found along the way live in `static/status.html`'s
matching phase and in `git log`/`git show` for this file (every checked item
below was condensed from a longer entry that's still in git history, nothing
was lost, only moved).

## P0 — Safety & correctness (before anything else)

- [x] Verified real Kalshi balance/position/fill field names against a
      connected account (real names like `position_fp`, `count_fp`,
      `yes_price_dollars` differ from the original guesses, which silently
      rendered blank) — also found and fixed a real 401 bug: the signed
      message omitted the `/trade-api/v2` prefix.
- [x] Verified the `create_order`/`cancel_order` schema against Kalshi's
      current docs — the original implementation targeted the legacy
      endpoint/shape; rewritten to `POST /portfolio/events/orders`'s current
      shape, covered by 4 request-shape tests.
- [x] Added an in-app confirmation step before real trading can be enabled —
      `POST /api/config` structurally can't touch `trading_enabled`; only
      `POST /api/trading/enable` can, requiring a connected account plus an
      exact typed confirmation phrase.
- [x] Paper broker state and the risk manager's daily-loss/kill-switch state
      now persist across restarts (`data/paper_broker.db`,
      `data/risk_state.db`), verified with a real `ddev restart`.
- [x] Tightened CORS — defaults to the DDEV hostname + `localhost:8000`,
      overridable via `ALLOWED_ORIGINS` in `.env`.
- [x] Built shadow mode (`services/shadow_mode.py`) — runs the real
      strategy's gates sized against the real account's actual balance,
      logs to `data/shadow_mode.db`, never calls `create_order`.

## Phase 0.5 — Dashboard & UX overhaul, Kalshi Pro-inspired

Kalshi shipped its own professional trading terminal, **Kalshi Pro**, as a
public beta on 2026-07-13 — a genuinely useful reference for what "robust,
informative, easy to navigate" looks like at this exact kind of app (real
Kalshi market data, order books, positions, order management). Researched
directly (news.kalshi.com's launch post, kalshi.com/pro's help docs, and a
detailed third-party review) rather than assumed from memory, since it's a
mid-2026 product and specifics matter here. Its actual feature set:

- **Canvas** — a customizable multi-market workspace. Pull several markets
  onto one screen at once, each with its own order book, chart, and order
  panel; arrange and save as named layouts.
- **Order book depth, per market**, with resting orders manageable directly
  on it — drag to reprice, sortable/filterable order tables, inline amends,
  batch cancel, bulk edit ("the way a trading desk would manage them").
- **Active Markets Screener** — ~2,000 markets ranked live by price,
  spread, depth, and rolling 5-minute volume. The reviewer calls this "a
  genuine discovery engine."
- **Continuous trade tape** — every public trade on the exchange, live,
  filterable for big trades or live events.
- **Per-market charting** (TradingView-caliber for perpetuals) with
  take-profit/stop-loss orderable directly on the chart, reduce-only
  orders, a max-slippage guard, proactive margin-risk alerts.
- Built explicitly for "speed, density, and order-management depth" —
  someone watching a dozen+ markets at once, not a first-time user.

That last point is a real tension with this roadmap's own guiding
principle above (dummy-proof, beginner-first) — Kalshi Pro is deliberately
the opposite of that. Resolution: not a tab-level split, a **panel-level**
one — every relevant panel/section gets its own Simple/Advanced toggle,
defaulting to Simple, rather than segregating beginner vs. professional
users into different tabs. A first-timer never has to leave Portfolio to
get a plain-English read; someone who wants Kalshi Pro-style density clicks
"Advanced" on the one panel they care about (order book, trade tape,
decision feed, ...) without the rest of the app changing under them. Also
worth keeping in mind, per the same review: Kalshi Pro's screener "shows
you what's moving, not whether it's mispriced" — this app's whale-follow
signal *is* a what's-mispriced opinion, which Kalshi Pro has none of.
Borrow their layout and density; don't accidentally bury the one thing
this app does that theirs doesn't, in either mode.

- [x] A shared Simple/Advanced toggle pattern — `isAdvanced`/
      `toggleAdvanced`/`advToggleHTML` in `static/index.html`, choice
      persists per panel via `localStorage`.
- [x] Navigation decision: Simple/Advanced fits same-list-different-density
      panels (trade log, cards, signal feed); order book/chart got a
      dedicated per-market drill-down instead, since they're too
      space-hungry for a card grid.
- [x] Kalshi-accurate terminology + real Event/outcome grouping —
      position/trade/fill rows now label contract count/entry/current price
      explicitly; `event_ticker` (previously fetched then dropped) now
      flows to the frontend so sibling-outcome markets group under one
      event header via a new `get_event()` lookup.
- [x] Follow-up: eliminated remaining raw-ticker/mental-math — fixed a real
      bug (`event.get('subtitle')` should have been `sub_title`, silently
      `None` since it shipped); `market_titles` now carries
      `{title, yes_sub_title, no_sub_title, event_ticker}` so
      `marketContext()`/`contextLineHTML()` show sport/matchup/position
      meaning; new `costHTML()` shows actual dollars put into a position.
- [x] Second follow-up, from real Kalshi screenshots: multi-outcome events
      were rendering N full duplicate cards — rebuilt `eventGroupCardHTML()`
      to match Kalshi's real pattern (one card, compact outcome rows sorted
      by probability, each clickable into the drill-down).
- [x] Market discovery was fundamentally broken (not just under-filtered) —
      Kalshi's MVE/combo markets vastly outnumber real ones in the API's
      default ordering, so even a 50,000-market flat browse could return
      zero with real volume. Fixed by switching to series-based discovery:
      `get_series_list(include_volume=True)` (~12,500 series, cached
      hourly) then `get_markets(series_ticker=...)` against the top active
      series. Also fixed an SDK gotcha: passing `mve_filter=None`
      explicitly (vs. omitting it) silently changed the result set. Search
      rebuilt on the same series-based approach.

Concrete gaps against the current 4-tab dashboard (Portfolio, Markets,
Whale Watch, Terminal — see `static/index.html`), each already framed as
a Simple/Advanced pair using the toggle above:

- [x] Per-market drill-down modal + order book depth — click any market to
      open a detail panel with Simple (best bid/ask/spread) and Advanced
      (full two-sided depth ladder, Kalshi's own bid/bid terms) views, via
      new `GET /api/markets/{ticker}/orderbook`.
- [x] Price history in the drill-down — `GET /api/markets/{ticker}/candlesticks`
      (needs a `series_ticker` resolved via one `get_event()` lookup).
      Simple: sparkline. Advanced: real OHLC + volume bars, hand-rolled SVG.
      Null `price.*` periods fall back to the yes_bid/yes_ask midpoint,
      carried forward, rather than plotting a misleading drop to zero.
- [x] Recent trades for one market in the drill-down —
      `GET /api/markets/{ticker}/trades`, last 15, no Simple/Advanced split
      needed.
- [x] Full-exchange trade tape, scoped to the current watchlist (not the
      literal whole exchange) — `_fetch_trade_tape()` fires one
      `get_trades(ticker=X)` per watched market concurrently. Simple:
      top-quartile-by-size in plain English. Advanced: full tape with a
      min-size filter, matched against how Polywhaler/WhaleScanr filter
      theirs.
- [x] Trade log / decision feed. Simple side unchanged (today's plain-
      English card feed for both). Advanced: `renderTrades`/`renderDecisions`
      rewritten to also offer a sortable/filterable table — Trade Log gets
      ticker/side/size/price/unrealized P&L (current price minus fill
      price, same math `renderPositions` already uses) and the raw
      confidence number pulled out of `Trade.reason`'s formatted text (a
      new `parseConfidence()`, since the number was only ever embedded in a
      sentence, not its own field); Decisions gets the same plus
      action/skip-reason, filterable by action. New small
      `sortRows`/`toggleSort`/`sortHeaderHTML` helpers, shared by both
      tables, click a column header to sort by it. Verified through a real
      Chrome session with synthetic data: correct P&L math, correct sort
      order and direction toggling, and both filters (min-size on trades,
      action on decisions) actually excluding the rows they should.
- [ ] Signal feed filters, matched against dedicated Kalshi/Polymarket
      whale-tracker products (Polywhaler, WhaleScanr — researched directly,
      not assumed, since these are literally the same category of tool this
      tab is trying to be). `renderSignals`/the Terminal signal feed has
      zero filter or sort controls today. Polywhaler's proven set: time
      range (1h/6h/24h/7d/30d), buy/sell, sort by recency or "impact," a
      position-grouping toggle. Worth matching rather than inventing our
      own from scratch — this category has already converged on what's
      useful here.
- [ ] Signal card enrichment, same source. Today's card
      (`ticker · side · size · confidence%`) is thin next to what these
      tools surface per print: Polywhaler shows market probability + 24h
      change, position size in both $ and contracts, an "impact" tag
      (low/medium/high), and a "stealth" count — how many separate trades
      built this position, i.e. one big print vs. a whale quietly
      accumulating over several smaller ones. The impact tag and price-
      change context are cheap UI additions on data this app already has;
      "stealth"/accumulation detection needs a backend change (grouping
      related signals over a time window) — see the matching P2 item below
      rather than treating it as pure UI.
- [ ] A real, browsable signal history — not just the aggregate stat cards
      `renderWhaleTrackRecord` already shows (win rate %, resolved count).
      WhaleScanr's specific framing is worth copying directly: "every flag
      and how it settled, misses included" — a trust-building design
      choice, not just a nice-to-have. `services/signal_log.py` already
      persists resolved/correct per signal (that's what feeds the win-rate
      stat today) — the data exists, there's just no UI to browse
      individual past signals and see what actually happened to each one,
      wins and misses both, rather than only the rolled-up percentage.
- [ ] Validated, not a gap: the existing "Betting is N pts more bullish/
      bearish than the market price implies" divergence line
      (`marketCardHTML`) is already the same core framing Upside's Whale
      Watch is built entirely around (comparing sharp/whale signal against
      a reference price to find the gap) — confirms this app's central
      idea is aimed at the right thing already. Worth leaning into further
      as the other items above land (e.g. sorting/filtering by divergence
      size, not just recency or raw whale size), not replacing it.
- [x] Potential payout (Simple tier) on every position/trade/fill row — new
      `payoutHTML()` helper (contracts settle to $1/$0, so max payout =
      contract count in dollars).
- [x] `renderPositions` rewritten into a real grouped table (per-event group
      + Total row, exact column structure confirmed against a real Kalshi
      screenshot), plus a whale-confidence badge and a 🐋 reason icon
      (surfacing the already-persisted `Trade.reason`, no backend change
      needed). Also investigated and closed: Kalshi's API exposes no
      per-market "largest open positions"/leaderboard data (confirmed 3
      ways) — `open_interest_fp` is the closest real substitute, not
      implemented.
- [x] Market search and browse, in a new Config tab — `GET
      /api/markets/search` (series-based, same approach as the discovery
      fix), checkbox-multi-select results merge into
      `kalshi.markets_watchlist` via the existing `/api/config` patch.
      Category taxonomy is still a plain text field (a real dropdown is a
      follow-up).
- [ ] Markets / Whale Watch cards. Simple: today's card view
      (`renderMarketCards`). Advanced: a dense, sortable table — price,
      spread, depth, 5-minute volume, whale lean — as an alternate
      rendering of the same underlying data, screener-style. Build it once
      and reuse it for Terminal's watchlist too — there are currently
      *three* separate market-list renderers (`renderMarkets` for
      Terminal's compact column, `renderMarketCards` for Markets/Whale
      Watch, and nothing shared between them), and Terminal's is the
      thinnest of the three (ticker, YES price, volume — no whale lean, no
      price movement). One shared, configurable renderer instead of three
      diverging ones.
- [x] Price-change indicators. Was a silent replace on every 5s poll with no
      acknowledgment a price moved. New shared `priceChangeHTML(ticker,
      price)`: compares against `previousPrices` (last poll's
      `state.latest_prices`, updated at the end of `refresh()` after every
      render that needed the old value), emits nothing when unseen/
      unchanged/sub-cent noise, otherwise a brief flash + arrow + ¢/% delta
      that naturally disappears again once the price stops moving (only
      rendered at all on the tick where it actually changed — no timer
      needed). Wired into the three highest-value spots: Terminal's compact
      market list, Markets/Whale Watch cards, and Portfolio's positions
      table "Now" column. Simplified from the original Simple/brief-flash
      vs. Advanced/full-delta split into one universal compact format
      (arrow + ¢, e.g. "▲ +12¢") rather than duplicating both tiers across
      four separate render paths — the Advanced tier's real destination
      (the not-yet-built dense screener table below) doesn't exist yet;
      revisit expanding it once that table lands. Not added to
      `eventGroupCardHTML`'s compact outcome rows (already dense
      multi-outcome lists — a flashing badge per row read as clutter, not
      signal). Verified live via three synthetic price-change sequences
      (up, down, and a repeat-unchanged tick) across all three render
      paths — correct arrow/delta, correct disappearance, zero console
      errors.
- [x] A real "LIVE" badge — not a timestamp proxy. Kalshi's live status is
      powered by a separate milestone/live-data system
      (`get_milestones(related_event_ticker=...)` → `get_live_data()`); a
      milestone's `end_date` stays `null` even for settled games (tested
      and disproven), but `details.widget_status` ("none"/"live"/
      "finished") is the real signal, confirmed against a real AFL match
      going live at its scheduled start. Checked only within a plausible
      live time window, replaced wholesale each poll tick.
- [x] Visible staleness/connectivity state. Was a silent
      `console.error('refresh failed', e)` and nothing else. New
      `#connectivity-badge` in the header, same loud-badge pattern as the
      exchange-status badge: tracks consecutive `refresh()` failures and
      seconds since the last real success, shows a pulsing "⚠ CONNECTION
      LOST · Ns" once anything fails, clears itself on the next success.
      `refresh()`'s own `/api/state` fetch also switched off the shared
      `fetchJSON` helper to add an explicit `res.ok` check — `fetchJSON`
      doesn't check status, so a 500 with a valid-JSON error body (FastAPI's
      default unhandled-exception shape) would otherwise parse
      "successfully" and be silently treated as a real update instead of
      the connectivity failure it actually is. Verified live via Selenium:
      a simulated network failure, a simulated 500-with-JSON-body, and
      recovery back to a clean badge — all three behave correctly.
- [x] Fixed the header equity strip ignoring the Portfolio account-mode
      toggle (`renderHeaderStrip()` now follows `accountMode`) — also
      surfaced and fixed a real bug in the same path: `real_balance_history`
      was storing raw cents instead of dollars.
- [x] Real watchlist/pinned-markets UI, shipped with market search above —
      `kalshi.markets_watchlist` already existed as a config field, just
      had no UI besides hand-editing YAML.
- [x] A self-serve "Reset Paper Account & Logs" button in the Config tab's
      new Danger Zone group, direct request — same `POST /api/reset` the
      Terminal Controls button already calls (wipes paper bankroll/
      positions/trade log, the daily-loss baseline, and the in-memory
      signal/decision feed; persisted whale track record and shadow trades
      are untouched by design, since those track long-run accuracy across
      resets), just reachable without asking for it. No new endpoint
      needed. Verified live: click actually reset the running account.
- [x] Follow-up, direct request: the Danger Zone reset was paper-only with
      no way to also wipe shadow/whale-track-record data short of asking for
      it — `POST /api/reset` now takes a JSON body (`{paper, shadow,
      signal_log}`, each independently optional, `paper` defaulting `true`
      to keep the Terminal button's old no-body behavior unchanged) and
      returns which domains it actually cleared. New `ShadowTrader.clear()`
      (wipes `shadow_trades`, resets the shadow daily-loss baseline) and
      `signal_log.clear_all()` (wipes the whale track record). Config tab
      now shows three checkboxes (Paper checked by default, Shadow/Whale
      track record opt-in) plus a confirm() prompt before firing. Verified:
      new unit tests for both `clear()` methods, plus live `curl` proving
      each flag combination reports back exactly the domains it cleared.
- [x] Open Positions → full market-detail modal, direct request ("essentially
      all the data on the market landing page on Kalshi itself"). The
      per-market drill-down modal above already existed but wasn't reachable
      from Portfolio, and only showed order book/chart/trades — no event
      context, prices, volume, or sibling outcomes. New
      `GET /api/markets/{ticker}/detail` bundles `get_market()` +
      `get_event()` in one call (siblings come back "for free" as part of
      the event payload, no per-sibling round trip); Open Positions' group
      title and each position row are now clickable into it. New
      `renderMarketInfoHTML()` renders category/title/subtitle, current
      Yes/No prices with a 24h change indicator, 24h volume/open interest/
      status/close time, the rules text, and — for multi-outcome events — an
      "Other Outcomes In This Event" list (reusing the existing
      `.outcome-row` styling from `eventGroupCardHTML`), each row clickable
      to jump the modal to that sibling. Verified live against a real
      multi-outcome event (an ITF tennis match), including the sibling-jump
      and zero console errors.
- [x] `/api/state` data-efficiency pass, direct request ("data consumption...
      hyper-efficient" but "keeping polling intervals as short as possible" —
      i.e. cut waste, don't trade away freshness). Measured first rather than
      guessed: `data/*.db` turned out trivially small (332KB total, and most
      of that was empty SQLite freelist pages left over from testing the
      reset-domains feature above, not real growth — confirmed via `PRAGMA
      freelist_count`) — not the actual pressure point. The real one:
      `/api/state` was 43.8KB per fetch, polled every 5s regardless of
      whether the backend's own `poll_interval_sec` (15s default) had
      actually produced anything new, so ~2 of every 3 polls re-sent and
      re-parsed byte-for-byte identical data. Fixed with a generation
      counter (`state["generation"]`, bumped once at the end of each poll
      tick and by every control endpoint that mutates state outside the
      loop — toggle/halt/resume/reset/config/trading-enable-disable/market-
      search) used as `/api/state`'s ETag: an unchanged poll now costs a
      304's worth of headers, not 43.8KB re-fetched and re-parsed for
      nothing. The response body itself is also memoized per generation, so
      `signal_log.stats()`/`shadow.recent()`/`shadow.stats()` (real SQLite
      queries) run once per actual change instead of once per HTTP request.
      Separately, `account.fills`/`account.positions` — full raw Kalshi
      objects, 13.4KB of the 43.8KB, mostly serving debug tooltips/raw-JSON
      detail rather than the rendered rows — got the same `_slim_market`-style
      trim (new `_slim_position`/`_slim_fill`, field names taken from the
      exact ones `renderRealPositions`/`renderRealFills` already read, not
      guessed), cutting fills to well under 2KB per fetch. WebSockets were
      considered and deliberately deferred: the dominant latency source is
      `poll_interval_sec` itself (backend's own Kalshi-fetch cadence), not
      the polling transport, so the marginal latency win didn't clear the
      complexity bar (reconnect/backoff, multi-tab fanout, an nginx Upgrade-
      handshake config change) yet — revisit if 15s-granularity updates
      still feel slow after this pass.
      Caught and fixed two real bugs during this work, both confirmed live
      via Selenium, not assumed: (1) a regression from the new caching
      itself — `GET /api/markets/search` mutates `state["market_titles"]`
      but wasn't bumping the generation counter, so a freshly-searched
      market's title wouldn't show up anywhere else in the UI (Open
      Positions, the detail modal) until the next real poll tick caught up,
      reported live as titles "reverting" to raw tickers — fixed by adding
      the missing bump; (2) a pre-existing bug surfaced during the same live
      testing, unrelated to the caching work — the market-detail modal's
      "click outside to dismiss" handler only checked the click event's
      final target, but `refreshMarketDetail()` replaces the modal body's
      `innerHTML` on every poll tick while it's open, so a mousedown-then-
      mouseup that straddled one of those replacements got its click event
      retargeted by the browser to the backdrop itself, silently closing the
      modal mid-interaction — fixed by also requiring mousedown to have
      started on the backdrop itself (`__backdropMouseDownOnSelf`), verified
      with a real mousedown → DOM-swap → mouseup race via Selenium
      ActionChains (modal stayed open) and a genuine outside click (still
      closes it).
- [ ] Reassess the Markets vs. Whale Watch split now that both share
      `renderMarketCards` — give them a genuinely distinct job (e.g. Whale
      Watch leans into the trade-tape/screener angle, Markets becomes the
      per-market book+chart view) or fold them into one tab with a filter,
      rather than two tabs showing near-identical cards today. Simple/
      Advanced modes reduce some of the pressure to split by density, but
      they're still duplicated content either way.
- [x] Bounded-height, scrollable list panels — new shared `.scroll-panel`
      class (`max-height: 420px; overflow-y: auto`) applied to all five
      feed panels, which previously rendered their already-capped 25-50 row
      feeds as full-height unscrolled DOM.
- [ ] Revisit the 5s polling model (`setInterval(refresh, 5000)` in
      `static/index.html`) once any Advanced view lands — a live order
      book and trade tape read as much less "live" on a 5s full-state poll
      than Kalshi Pro's presumably-pushed updates. Not blocking for the
      items above, but likely the next bottleneck once they're in, and
      probably an Advanced-mode-only concern (Simple panels don't need
      sub-5s freshness). Partially addressed by the ETag/304 pass above —
      the interval itself is unchanged, but an unchanged poll is now nearly
      free, so this item is really about push-vs-poll latency (WebSockets,
      considered and deferred there) rather than payload waste anymore.

Scope boundary, decided explicitly rather than left implicit: this app has
zero manual/discretionary trading anywhere today — no order entry, no way
to close a position early, paper or real; every trade is placed by the
automated whale-follow strategy. Kalshi Pro is fundamentally a manual
trading terminal with automation as an assist, the opposite emphasis.
Confirmed staying automated-only for Phase 0.5 — this stays a terminal for
*observing* the strategy, not a general manual trading UI. Worth
revisiting only if the goal of the app itself changes.

Three existing items elsewhere in this file overlap enough with this phase
that they're worth sequencing deliberately rather than doing twice by
accident: P3's mobile/responsive pass and accessibility pass (keyboard
nav, aria labels, colorblind-safe yes/no) both touch every panel this
phase is about to redesign — do them *after* Phase 0.5's layout settles,
not before, or they'll need redoing. P4's notification item (real trades,
kill-switch trips, a whale's win rate crossing the avoidance threshold) is
thematically a Phase 0.5 concern too, given this phase's own "visible
staleness/connectivity state" item just above — worth reconsidering
whether it's actually P4-nice-to-have or belongs in this phase's priority
band instead.

## P1 — Actually dummy-proof (a first-timer understands what's happening)

- [ ] First-run walkthrough. There is currently zero onboarding — a new
      user lands directly on a 4-tab dashboard with no explanation of what
      Portfolio/Markets/Whale Watch/Terminal even mean.
- [ ] A persistent glossary/help layer (tooltips or a dedicated Help panel)
      for every piece of jargon still in use: confidence, cooldown, kill
      switch, series, combo/parlay market, implied probability, basis
      points, whale "lean," etc.
- [ ] Extend the plain-English pattern from the Portfolio "Betting vs.
      Likelihood vs. Risk vs. Whales" panel to Strategy Decisions too —
      skip reasons are still raw strings like "confidence 0.34 below
      threshold" instead of a sentence a beginner would understand.
- [ ] A basic "how prediction markets work" explainer: what a price means
      as a probability, why Yes + No ≈ 100¢, what "settlement" means. The
      whole app currently assumes this prior knowledge.
- [ ] A global, impossible-to-miss "this is not real money" indicator.
      Today that's the header's PAPER tag plus a separate red account bar
      — clear once you know to look, but not loud enough for a total
      beginner, especially after a real account is connected.

## P2 — Whale-tracking maturity

- [ ] Add at least one more real named whale-watcher provider (beyond the
      `generic_rest` default and the unused `template_provider.py`) with
      actual, tested setup steps.
- [ ] Improve series/category grouping. `services/signal_log.py` currently
      groups "market type" by ticker-prefix-before-first-hyphen, a
      reasonable proxy but not a real category taxonomy — revisit once
      better Kalshi series metadata is available.
- [ ] Let a user manually exclude a specific whale/source from the
      strategy, not just the automatic win-rate cutoff
      (`min_whale_winrate_pct` / `min_resolved_for_whale_filter`).
- [x] A configurable "live markets only" trading gate, direct request — lets
      the strategy's real-world accuracy be observed in isolation from
      thin/pre-market signal noise. New `strategy.live_markets_only` config
      field (default `false`); when on, both `FollowTheWhaleStrategy.evaluate()`
      and `ShadowTrader.evaluate()` skip/decline any signal whose market
      isn't currently live. Deliberately reuses the exact same live-status
      lookup the dashboard's LIVE badge already shows
      (`state["live_status"]`, keyed by `event_ticker`) rather than a second
      definition of "live" — `main.py`'s trading loop resolves
      `signal.ticker` → `event_ticker` → live status once per signal and
      passes it to both evaluators. Config tab gets a matching checkbox.
      5 new tests (3 strategy, 2 shadow) cover on/off and live/not-live.
- [ ] Config-versioned performance tracking, to eventually feed a
      machine-learning advisory service — requirement only, not yet
      implemented (direct request: "just add in the requirement for now").
      Every distinct combination of the strategy config fields that
      actually govern trade decisions (`entry_threshold`,
      `max_position_pct`, `cooldown_sec`, `min_whale_winrate_pct`,
      `min_resolved_for_whale_filter`, `live_markets_only`, ...) should get
      its own tracked "config variant" — changing any one of those fields
      starts a new tracker instead of blending results into the previous
      config's numbers. Needs: (1) a stable fingerprint/hash of just the
      relevant strategy-config subset, not the whole `settings.yaml`
      (`poll_interval_sec`/`whale_signal.*` etc. shouldn't fragment
      tracking that has nothing to do with them); (2) a new persisted log
      (same one-file-per-concern SQLite pattern as
      `services/signal_log.py`) recording each trade's outcome (win/loss
      once resolved, size, P&L) tagged with the config-variant fingerprint
      active when it was placed; (3) an aggregate success rate per variant,
      comparable to `signal_log.py`'s existing win-rate math but scoped
      per-config instead of per-series; (4) the actual ML/advisory consumer
      of this data is explicitly out of scope for this item — this is the
      data-collection groundwork only, so "which config performed best" is
      answerable later without needing to backfill from scratch.
- [x] Whale-size threshold relative to each market, not a flat number —
      `whale_simulator.py`'s half, direct request ("more accurately reflect
      real-world behavior and volatility"). Was a flat configured
      `size_range` tuple picked uniformly regardless of which market got
      chosen; WhaleScanr's real methodology (researched directly) is
      relative — "roughly the size only the top few percent of [that
      market's] trades reach, plus an absolute dollar floor." New
      `_size_for()`: log-normal (real large-trade sizes cluster with a
      heavy right tail, not uniformly) centered on 3% of the chosen
      market's own `volume_24h_fp`, with `size_range`'s two configured
      numbers reinterpreted as an absolute floor/cap rather than a flat
      pick range. Market selection itself also changed — `_pick_market()`
      now weights by `volume_24h_fp` instead of `random.choice`, so
      simulated whale attention concentrates where a real market actually
      has liquidity and price action, the "volatility" half of the
      request — rather than spreading evenly across quiet and busy markets
      alike. `whalewatchers/generic_rest.py`'s matching `min(size/50000,
      1.0)` is real-provider code, out of scope for this pass (nothing
      "fake" to tune there) — still flat, still open.
- [x] Composite confidence scoring, `whale_simulator.py`'s half, same
      request. Was a single factor (size only, linear + noise). New
      `_score_confidence()` implements Polywhaler's stated "Insider Score"
      shape (researched directly) as a weighted composite: trade size
      relative to the chosen market's own depth (40%), how unusual the
      price is — closer to a coinflip reads as more informationally live
      than an already near-certain 5¢/95¢ market (25%), proximity to the
      market's own `close_time`, within a ~48h window (20%), and how busy
      this market is relative to the rest of the current watchlist (15%) —
      plus noise, same overall shape as before. Verified live: real
      near-resolved markets (3¢/90¢ prices) now correctly score low
      confidence (0.11-0.18) despite large sizes, instead of the old
      version's size-only score treating a big print on a foregone
      conclusion the same as one on a genuine toss-up. 12 new tests in
      `tests/test_whale_simulator.py`, one per factor plus edge cases
      (zero volume, missing/malformed `close_time`). `generic_rest.py`'s
      matching single-factor score is real-provider code, still open.
- [ ] (Stretch) Persistent flow clustering. WhaleScanr's approach to a
      genuine constraint this app already respects — Kalshi's real trade
      tape is anonymous, no usernames or account data, confirmed directly
      on their site — is to group trades into probable-same-actor
      "clusters" using statistical/behavioral similarity (same ticker/
      side, similar size, similar timing pattern), labeled with a
      confidence score, without ever claiming verified identity. Validates
      this app's current anonymous-by-design signal model rather than
      contradicting it (there's no "whale identity" field to add — Kalshi
      genuinely doesn't expose one). Clustering repeated signals into "this
      looks like one actor accumulating" is the concrete version of the
      Phase 0.5 "stealth" card item above; lower priority than the two
      items above it since it's inference on top of already-good data, not
      a correctness fix.

## P3 — Reliability & engineering hygiene

- [x] Automated test suite — 35+ tests in `tests/` covering
      `paper_broker.py`, `risk_manager.py`, `strategy_engine.py`, and
      `signal_log.py`; each isolates its own SQLite file via
      `monkeypatch`, none touch the real `data/*.db` files.
- [x] Basic CI — `.github/workflows/tests.yml` runs the suite on every
      push/PR to `main`.
- [x] Migrated fully to Kalshi's official `kalshi_python_async` SDK (3.27.0,
      requires Python ≥3.13) — supersedes an earlier same-day "decided
      against it" finding, which turned out to be `pip index versions`
      silently resolving to a stale, Python-3.11-only release (3.2.0) with
      real validation bugs since fixed upstream. All endpoints verified
      against the real connected account with zero `ValidationError`s.
- [x] Exponential backoff on `429 Too Many Requests`, per Kalshi's
      rate-limit docs (no `Retry-After` header provided) —
      `call_with_backoff()` wraps SDK client calls, detects 429 via the
      SDK's exception shape. 5 tests, no real network/sleep.
- [x] Exchange open/closed status badge in the header (`GET
      /exchange/status`, public) — distinguishes "market's closed" from
      "strategy is stuck."
- [x] Fixed intermittent 403/404s on the dashboard — root cause was ddev's
      default `web` service and the custom `fastapi` service both
      auto-registering a Traefik router for the same hostname, an unstable
      tie-break. Real fix: `main.py` is API-only now; `web` (nginx) is the
      sole public entrypoint serving `static/*` and reverse-proxying
      `/api/`+`/auth/` to `fastapi`, which has no public exposure of its
      own — structurally impossible for the collision to recur.
- [ ] Mobile/responsive pass — the Terminal view's 3-column grid is
      desktop-only right now.
- [ ] Accessibility pass — keyboard navigation, aria labels, and a check
      that the heavy green/red (yes/no) coding has a non-color fallback
      for colorblind users.

## P4 — Nice-to-haves

- [ ] Notifications (email/push) for real trades, kill-switch triggers, or
      a tracked whale's win rate crossing the avoidance threshold.
- [ ] Finalize the app name — "Nessie" vs. "Operation Deepscan" is still
      an open decision; once picked, it needs to flow through page titles,
      headers, and the README.
