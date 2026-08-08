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
- [x] Follow-up, a real bug caught live via direct report ("the only market
      that shows up is Wyndham Championship, which isn't even live... Kalshi
      itself is showing 50 live markets right now"). Root cause: series-based
      discovery above fixed *what* gets browsed, but `get_top_volume_markets`
      still picked the final watchlist via a flat top-n-by-volume sort across
      every candidate market with no diversification — confirmed directly,
      not assumed: a single 8-market golf tournament, every sub-market
      individually high-volume, filled all 8 watchlist slots and crowded out
      every other series entirely, even with dozens of other markets
      trading (some genuinely live) at the time. A flat per-event cap was
      tried first and replaced before shipping — it fixes the crowding-out
      case but creates the mirror problem, needlessly truncating a
      genuinely multi-outcome event's own sub-markets when nothing else is
      competing for slots. Shipped instead: round-robin selection across
      distinct events (still highest-volume-first *within* each event) —
      self-sizes with no hardcoded number, so the effective per-event share
      shrinks automatically as more distinct events compete and grows
      toward the full watchlist size when one genuinely dominates. Also
      bumped several related caps that were tuned for an 8-market watchlist
      and hadn't been revisited since: `watchlist_size` 8→20,
      `_get_top_series` top_n 15→30, per-market trade-tape fetch 5→10
      trades (total tape cap 30→50), unresolved-signal-resolution batch
      3→10 per tick (switched from sequential to concurrent fetching in the
      same pass, so a bigger batch doesn't stack up round-trip latency),
      real-account fills page size 25→50, `market_titles`/`event_titles`
      long-run caps 300→500, `equity_history`/`real_balance_history`
      300→500 points. Verified live against the real Kalshi API, not just
      unit tests: the watchlist went from 1 distinct event / 0 live markets
      to 20 distinct events / 4 confirmed live. 6 new tests in the first
      dedicated `tests/test_kalshi_client.py` (this module had none before)
      covering the round-robin behavior directly, including the "only one
      real event available" case explicitly.

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
- [x] Signal feed filters, matched against Polywhaler/WhaleScanr's proven
      set (researched directly, see the P2 whale-simulator items for the
      same research applied elsewhere) rather than invented from scratch:
      time range (1h/6h/24h/7d/30d/all), buy/sell (Yes/No), sort by
      recency or "impact," and a position-grouping toggle. "Impact" isn't
      a field this app tracks — defined as `size x confidence`, and tiered
      low/medium/high *relative to the currently visible feed* rather than
      a flat cutoff (same relative-not-flat lesson as the whale simulator's
      own sizing fix above). Position-grouping combines every signal on
      the same ticker+side into one card with a "N× prints" tag — a light,
      client-side, non-persisted taste of the P2 stretch item's
      accumulation detection, not a replacement for it. New `signalFilter`
      state + filter bar reusing the existing `.table-filter-row` styling.
      Verified live via synthetic signals spanning old/new timestamps and
      both sides: time-range and side filters correctly exclude what they
      should, impact sort correctly orders highest-impact first,
      group-by-market correctly combines two same-ticker signals into one
      2×-prints card.
- [x] Signal card enrichment, same pass, same source comparison. Did the
      two "cheap UI addition" pieces the item called out — an impact tag
      (low/medium/high, see above) and position size in both $ (new,
      `size x price`) and contracts (already shown) — plus the "stealth"
      print-count tag via the grouping toggle above. Skipped 24h price
      change on purpose, not silently: this app has no per-ticker
      historical-price data available in bulk (candlesticks are fetched
      on-demand per-market in the drill-down modal only, one API call per
      ticker — not something to fan out across every ticker on a live
      signal feed) — faking it wasn't an option. Real "stealth"/
      accumulation detection (same-actor clustering, not just same-
      ticker-same-side grouping) remains the P2 stretch item below.
- [x] A real, browsable signal history — WhaleScanr's framing, copied
      directly: "every flag and how it settled, misses included," not just
      `renderWhaleTrackRecord`'s rolled-up win-rate percentage. New
      `signal_log.recent(limit, offset, resolved_only)` /
      `signal_log.total_count()` and `GET /api/signals/history` (a separate
      on-demand/paginated fetch, deliberately *not* part of the
      `/api/state` poll cycle — a live-updating history table would fight
      with someone actively paging/sorting through it). New "Signal
      History" panel on the Whale Watch tab: a "Resolved only" filter,
      sortable columns (reusing the existing `sortRows`/`toggleSort`/
      `sortHeaderHTML` helpers), pending/correct/miss outcome column, and
      Prev/Next pagination. Loads once when the tab opens (`showView()`)
      and again on filter/sort/page changes, not every 5s, so browsing
      doesn't reset itself out from under you. 4 new tests
      (`tests/test_signal_log.py`) for ordering, pagination, and the
      resolved-only filter. Verified live: real signal_log.db data (100+
      logged signals) renders and paginates correctly, sort toggling works,
      zero console errors.
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
- [x] Markets / Whale Watch cards. Simple: unchanged card view
      (`renderMarketCards`). Advanced: new dense, sortable
      `renderScreenerTable`/`renderScreenerTableFromState` — Market/
      Category/Yes/No/Spread/24h Volume, plus a Whale Lean column when
      `includeWhale` is on — one shared, configurable renderer reused
      across all three former call sites (`renderMarkets` for Terminal's
      compact column, `renderMarketCards` for Markets/Whale Watch), each
      with its own independent sort state (`screenerState`, keyed by panel
      id, same pattern as `tradeLogFilter`/`decisionFilter`). Two columns
      from Kalshi Pro's own screener were deliberately *not* faked: real
      order-book depth would cost one extra API call per market per poll
      tick (the same request-fan-out growth the `/api/state` efficiency
      pass earlier avoided); "5-minute volume" would need far more trade
      data than `_fetch_trade_tape` actually pulls (5 trades/market, 30
      total across the whole watchlist — nowhere near enough for a real
      rolling figure), so 24h volume is shown instead of a number that
      would quietly be wrong on any market busier than a handful of
      trades. Spread comes for free — `yes_ask_dollars` was already on
      every market object `_fetch_markets` fetches, just not previously
      exposed (`_MARKET_FIELDS`). Verified live across all three panels:
      Advanced shows the sortable table, a header click re-sorts, toggling
      back to Simple correctly restores the original card/list view, and
      the Whale Lean column appears only on Whale Watch's table, not
      Markets'.
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
- [x] Reassess the Markets vs. Whale Watch split. Resolved as "give them a
      genuinely distinct job," not "fold them into one tab with a filter"
      — the merge option would have meant cramming Whale Watch's now-
      substantial whale-specific content (Track Record stat cards, the
      new browsable Signal History panel, Trade Tape) alongside general
      market browsing behind a filter toggle, awkward for both jobs at
      once. Distinct jobs instead: Markets stayed pure market discovery
      (cards/screener table, whale-neutral, `includeWhale=false`); Whale
      Watch keeps its three whale-specific sections *and* its own
      cards/screener render whale-annotated (`includeWhale=true` — whale-
      lean blocks in Simple, a Whale Lean column in Advanced). The shared
      screener table above already made this concrete: same renderer, same
      code, genuinely different output per tab, not just a different tab
      label over identical content. Verified live: the Whale Lean column
      renders on Whale Watch's Advanced table and is absent from Markets'.
- [x] Bounded-height, scrollable list panels — new shared `.scroll-panel`
      class (`max-height: 420px; overflow-y: auto`) applied to all five
      feed panels, which previously rendered their already-capped 25-50 row
      feeds as full-height unscrolled DOM.
- [x] Follow-up, direct report: Terminal's Whale Signals and Strategy
      Decisions panels were already scroll-capable (the item above), but a
      full `innerHTML` teardown-and-rebuild on every 5s poll reset
      `scrollTop` back to 0 every time, so any scroll position got wiped
      out within 5 seconds and the panel *felt* broken even though it
      technically wasn't - and visibly flickered on every update besides.
      New `renderFeedListSmooth()`: since new items are only ever prepended
      server-side and only ever fall off the tail once capped, the common
      case ("same items as last time, plus some new ones in front") is
      detected directly and only the new items' HTML gets inserted -
      existing DOM nodes for unchanged rows are never touched, so nothing
      flashes and the browser's own scroll anchoring keeps whatever was on
      screen in view. Falls back to a full rebuild whenever that
      invariant doesn't hold (a filter/sort/grouping change, or switching
      the Decisions panel between Simple and Advanced — which needed its
      own guard, since the Advanced table writes into the same container
      outside the smooth-render path and could otherwise be mistaken for
      an up-to-date card list on switching back). Split both panels'
      markup into a stable filter-bar container plus a scrolling list
      container so filters stay pinned while only the list scrolls, rather
      than scrolling out of view with the cards. Verified live: existing
      card DOM nodes survive an update untouched (a planted marker
      attribute persists), scrollTop is never reset to 0 by new arrivals,
      and the Simple/Advanced switch (with its own filter bar swap) still
      behaves correctly in both directions.
- [x] Thin, semi-transparent scrollbars everywhere, direct request — the
      default OS scrollbar read as heavy against this app's dark theme.
      `scrollbar-width`/`scrollbar-color` (Firefox) plus
      `::-webkit-scrollbar*` (Chrome/Safari), applied globally.
- [x] Price-change indicators, follow-up correction — the original version
      (earlier in this same section) compared only against the last
      poll and vanished again the next tick if the price held steady;
      direct correction: "they shouldn't be transient... they should stay
      visible and update as they change." Redesigned around a persistent
      per-ticker baseline (the first price seen for that ticker this
      browser session) instead of the last poll's price — the badge is
      visible continuously once a baseline exists and its number updates
      in place every tick, rather than flashing once and disappearing.
      Dropped the fade-in pulse animation that made sense for a one-tick
      flash but not a steady, continuously-updating badge. Verified live
      across four synthetic price steps: no badge on first sighting (no
      baseline to compare against yet), badge appears and stays visible
      across an unchanged tick (the exact case that used to vanish), and
      updates correctly when the price moves again.
- [x] Search/browse markets directly in the Markets tab, direct report
      ("you never included an ability to search or browse markets myself
      in the markets tab which i asked you to do") — the existing market
      search (`GET /api/markets/search`) only lived in the Config tab,
      scoped to *managing the watchlist*, not general browsing. New search
      panel at the top of the Markets tab, same endpoint, different
      purpose: every result is clickable straight into the existing
      per-market detail modal (`openMarketDetail`), with a secondary
      "+ Pin" button per result as a bridge into the watchlist-management
      flow rather than the point of this panel. Verified live: a broad
      query returns real results, clicking one opens the real detail
      modal, and the Pin button correctly adds to
      `kalshi.markets_watchlist`.
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
zero *manual* trading anywhere today — no order entry UI; every position is
still opened by the automated whale-follow strategy, never a person
clicking "buy." Kalshi Pro is fundamentally a manual trading terminal with
automation as an assist, the opposite emphasis. Confirmed staying
automated-only for Phase 0.5 — this stays a terminal for *observing* the
strategy, not a general manual trading UI. Worth revisiting only if the
goal of the app itself changes. (Exit management below is still
automated — config-driven rules and an algorithm decide when a position
closes, not a person clicking "sell" — so this boundary still holds; what
changed is that positions are no longer left open unmanaged after entry.)

## Active position management & Trading History

Direct request: "once positions are opened they are never monitored/
changed to better reflect whale trends and minimize potential losses/
maximize gains as confidence/whale positions change... kalshi lets you set
sell prices so if say you open a position at 50c you can set to cash out
at 75c instead of 99c and profit." Before this, `PaperBroker` had no exit
mechanism at all — a position sat untouched from entry until the process
happened to notice it later; "unrealized P&L" never became realized, gains
were never locked in, losing positions were never cut, and a position on a
market that had already settled just stayed open forever with no realized
outcome.

- [x] `PaperBroker.close_position(ticker, exit_price, reason)` — sells a
      position back at a given price instead of only holding to $1/$0
      settlement, same side-aware cash-back math (`price` for yes,
      `1-price` for no) `mark_to_market` already used. Reuses the existing
      `trades` table exactly (no schema change) — a close is a trade whose
      `reason` starts with `"closed: "`, `side` stays the position's
      original side so existing side-tag styling/logic works unchanged.
- [x] `FollowTheWhaleStrategy.check_exits(latest_prices, signal_feed, cfg,
      market_results)`, run every trading-loop tick regardless of whether a
      new signal arrived. Checked in priority order per open position:
      1. **Market settlement** (unconditional, not opt-in) — closes at the
         terminal $1/$0 price the instant `market.result` is set,
         regardless of any other config. This is the correctness fix for
         the "left open forever" gap above.
      2. **`take_profit_pct`** (opt-in, default `null`) — close once
         unrealized gain reaches this fraction of cost basis.
      3. **`stop_loss_pct`** (opt-in, default `null`) — same, for a loss.
      4. **`exit_on_sentiment_reversal`** (opt-in, default `false`) — close
         if whale sentiment on this ticker (`_whale_lean`, same math as the
         dashboard's `computeWhaleLean`) has flipped decisively against the
         held side, once enough recent signals exist
         (`exit_sentiment_min_signals`/`exit_sentiment_lean_pct`).
      5. **`auto_exit_enabled`** (opt-in, default `false`) — an automated,
         tweakable multi-factor "exit confidence" algorithm, a further
         direct request ("automated, using another tweakable algorithm
         based on sensible factors from kalshi and whale watch data").
         `_exit_confidence()` blends three independently-weighted 0-1
         factors into one composite score, closing once it crosses
         `auto_exit_threshold`: unrealized P&L magnitude (scaled against
         configurable `auto_exit_gain_reference_pct`/
         `auto_exit_loss_reference_pct`), whale-sentiment reversal strength
         (same `_whale_lean`), and staleness (time since the last whale
         print on this ticker, `auto_exit_stale_after_sec`) — the same 0-1
         "confidence" mental model the entry side already uses. A factor
         with no data (e.g. no whale prints at all) is omitted from the
         average, not scored as zero, so missing data never gets treated
         as agreement or disagreement.
      All five checks (2-5 are the tunable layer; 1 is a hard rail) are
      independently unit-tested — 30 new tests across
      `tests/test_paper_broker.py`/`tests/test_strategy_engine.py`.
- [x] `main.py` wiring: `check_exits()` called once per tick with a
      `market_results` dict built from this tick's already-fetched markets
      (`{ticker: market.result}`), decisions appended into
      `state["decision_feed"]` the same as a normal trade/skip. `_fetch_markets`
      now always includes every currently-open position's ticker even if
      it's rotated out of the top-volume watchlist selection, so
      `latest_prices`/titles never go stale for a position that's actually
      still held — without this, a position that fell out of the watchlist
      would silently stop getting price updates and its exit triggers would
      never fire. Config tab gets a new "Position Management (Exits)"
      section for all twelve new `strategy.*` fields.
- [x] New Trading History tab, direct request: "a trading history tab that
      shows graphs, positions made, win loss rates, times auto management
      changed positions or sold at a price less than what a full win would
      provide..., with relevant whale data and relevant config metadata
      that will help inform me on how to change management config, whale
      config, etc." New `services/trade_analytics.py` (pure functions, no
      new persistence): classifies each closed trade's `close_type`
      (take_profit/stop_loss/sentiment_reversal/auto_exit/settled_win/
      settled_loss) by parsing the reason-string conventions
      `close_position`/`check_exits` already write, pairs each close back
      to its entry trade, and computes `cost_basis`/`cash_back` (actual
      dollars in/out, side-aware), `hold_sec`, and `left_on_table` — the
      "sold at 75c instead of $1" number from the request, computed only
      for a genuine early profit-take (take-profit/auto-exit/sentiment-
      reversal with positive realized P&L), explicitly framed as a
      hypothetical ("if this had gone on to fully resolve your way"), never
      a claim about what would actually have happened. New
      `GET /api/trading-history` (paginated) returns per-trade rows, an
      aggregate summary (win rate, total capital deployed, total realized
      P&L, total left on table, avg hold time, per-close-type breakdown), a
      cumulative realized-P&L curve, and **insights**: sample-size-hedged
      heuristic hints about which config knob a pattern in the trade
      history might argue for adjusting (e.g. win rate by entry-confidence
      bucket → `entry_threshold`; average left-on-table on take-profit
      closes → `take_profit_pct`), each tagged with the trade count it's
      based on and a low/moderate/higher confidence label. Explicitly
      **not** a recommendation engine and never writes config — resolved
      directly with the user rather than assumed: full config-versioned
      performance tracking (the still-open P2 item below) and any
      auto-generated tuning *recommendation* are deliberately out of scope
      until there's enough real trade volume for that to be trustworthy;
      this ships the descriptive layer plus clearly-hedged heuristic hints,
      not an advisory system. 18 new tests in `tests/test_trade_analytics.py`.
      New History tab in `static/index.html`: summary cards, a cumulative
      P&L chart (existing `renderEquityChart` generalized to take a target
      element id), a by-close-type breakdown table, an insights panel, and
      a paginated trade table with per-row dollar cost/payout alongside
      price/side/close-type/hold-time/entry-confidence/realized-P&L —
      verified live via Selenium with synthetic data covering every
      close_type and zero console errors.
- [x] Found and fixed, via direct live-data investigation (not
      hypothetical): a real bug in the settlement code above itself —
      `terminal_price` was computed as `1.0 if won else 0.0` (won =
      does the result match the held side), which for a **no** position
      double-applies `close_position`'s own side inversion and silently
      pays $0 on an actual win. Caught by a failing test before it could
      spread, but had already run live for a few ticks and mis-paid 2 real
      trades — corrected live via the two independent fixes below.
- [x] Found and fixed, while tracing the bug above: a significant
      pre-existing bug (not introduced this session) in
      `PaperBroker.open_position()` — it charged `size * price`
      unconditionally, but `price` is always the *yes* price by convention
      (confirmed in `whale_simulator.py`); a **no** position's real cost is
      `size * (1 - price)`. This under-charged every no-side entry the app
      has ever opened and manufactured phantom profit on any no position
      that never even moved. Fixed in `open_position` (plus its
      `actual_size` bankroll-capping math, which had the same unit-cost
      bug), and the identical bug in `FollowTheWhaleStrategy.evaluate()`'s
      and `ShadowTrader.evaluate()`'s position-sizing (mis-sizing a no-side
      trade there also silently blew past the intended
      `max_position_pct` risk cap, since `open_position` caps spend at
      whatever bankroll remains, not at the intended size). New
      `PaperBroker.cost_basis(ticker)` is now the single source of truth
      for "real dollars in this position," used by `check_exits`' pnl_pct
      math and exposed on every position in `broker.state()` so the
      dashboard doesn't reimplement it. User's explicit decision on
      remediation (asked directly, not assumed): fix the code and reset the
      paper account (`POST /api/reset`) rather than try to retroactively
      correct historical no-side trades.
- [x] Found and fixed, via a deep-scan review requested directly after the
      above ("do a deep scan of your math and logic regarding market data,
      whale data, positions, management... to make sure you're not missing
      any gaps or errors"):
      - `evaluate()` had no check for a ticker that already had an open
        position — only cooldown was checked, so a signal on a ticker whose
        earlier position was never closed could silently overwrite it in
        `self.positions[ticker]`, discarding its cost basis with zero
        accounting trail. Confirmed this had actually happened in live
        trade history (not hypothetical). Now an explicit skip: "position
        already open on this market."
      - `evaluate()`/`ShadowTrader.evaluate()` never checked whether a
        market had already resolved before opening a new position on it —
        a narrow but real window (a market settling between polls, or only
        appearing in this tick's markets list because an unrelated open
        position pulled it in). Now skipped via the same `market_results`
        data `check_exits` already uses: "market has already resolved."
      - Four separate frontend instances of the same no-side dollar-math
        bug class: the Positions panel's Cost/Value/Return columns, the
        Explain-Like-I'm-5 panel's "capital at risk," the whale signal
        card's dollar-size display, the paper Trade Log's "put in" cost
        figure, and — a distinct sign-flip variant, not just a missing
        inversion — the Advanced Trade Log table's per-row P&L, which had
        no `direction` term at all and showed a no-side trade's gain/loss
        with the sign flipped.
      - The whale-print "why this position was opened" `<details>`
        disclosure was silently snapping shut on every dashboard refresh
        (`renderPositions` rebuilds the whole table's `innerHTML` every
        poll) — same class of bug as the market-detail-modal one Phase 0.5
        fixed, direct report. Now captures which tickers were expanded
        before the rebuild and restores them after.
      All fixed and covered by new/updated tests. Full suite: 171 passing
      (was 127 immediately before this batch of work).

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

- [x] First-run walkthrough + persistent glossary/help layer + "how
      prediction markets work" explainer — built as one combined Help
      modal rather than three separate surfaces, since all three are
      really the same "get a first-timer oriented" job. New
      `#help-backdrop` (same modal chrome/mousedown-guard pattern as the
      market-detail modal): a plain-English prediction-markets primer
      (what a price means as a probability, why Yes + No ≈ 100¢, what
      settlement means) plus an 11-term glossary (confidence, whale
      "lean," cooldown, kill switch, implied probability, basis points,
      series, combo/parlay market, shadow mode, paper trading, live
      market) written in terms of how *this app* actually uses each term,
      not textbook definitions. Auto-opens once on a brand-new browser
      (`localStorage` flag), always reachable afterward via a new "Help"
      link in the util-bar. Deliberately scoped as a reference panel to
      read once, not a step-by-step guided tour that highlights individual
      UI elements — that would need real positioning/overlay machinery
      this app doesn't have, and a first-timer reading one page is a
      reasonable bar to clear before building that. Verified live: a
      cleared-localStorage session auto-opens it, closing sets the flag so
      a reload doesn't reopen it, and the util-bar link reopens it
      manually afterward.
- [x] Extended the plain-English pattern from the Portfolio "Betting vs.
      Likelihood vs. Risk vs. Whales" panel to Strategy Decisions. New
      `plainEnglishSkipReason()` pattern-matches `strategy_engine.py`'s
      `_skip()` reason strings (left unchanged at the source — they're
      fine, concise technical strings for logs/Advanced mode) into full
      sentences for Simple mode, same Simple/Advanced split as everywhere
      else: Advanced still shows the exact raw string. Falls back to the
      raw string for anything it doesn't recognize rather than hiding or
      guessing at it. Verified live against all 5 real skip-reason shapes
      this file actually produces, including the unrecognized-string
      fallback.
- [x] A global, impossible-to-miss "this is not real money" indicator. New
      full-width, sticky `#real-money-banner` above the util-bar — not
      buried in the header strip, which was the actual complaint. Exactly
      two states, keyed off `account.trading_enabled` (the field that
      genuinely gates whether a real order can ever be placed, per
      `kalshi_account_client.py`'s `_require_trading_enabled` — not just
      "is an account connected," which is still 100% safe on its own under
      this app's architecture): a calm green "🧪 PAPER TRADING — no real
      money is at risk" by default, and a pulsing red "⚠️ REAL TRADING IS
      ENABLED" only once real orders are genuinely possible — loud
      specifically when it needs to be, not crying wolf the rest of the
      time. Verified live in both states.
- [x] Config tab rebuilt as a genuine multi-section control panel, direct
      report: "the config tab is hard to make sense of for a like-I'm-5
      user." Was one long flat scroll of every field at once; now ten
      collapsible `<details class="config-section">` accordions (Mode and
      Strategy open by default, the rest collapsed), each with a one-line
      plain-English subtitle. New data-quality badge system, direct
      request ("highlight config options that aren't fully baked yet due
      to data limitations"): 🧪 sim data (fields calibrated against the
      built-in whale *simulator*, not a real feed — the whale win-rate
      filter fields, the sentiment-reversal exit block, the auto-exit
      sentiment/staleness weights, the whole Whale Signal section), 📉
      needs history (the Advisory Engine section), 🆕 new (the
      Market-Native Strategy section below — a brand-new feature with no
      track record yet, a different concern from data quality). Explained
      once in a legend box at the top of the tab rather than repeated
      inline. Verified live via Selenium: all ten sections render and
      expand/collapse, every badge type present, Save Config still
      round-trips correctly through `/api/config`.

## P2 — Whale-tracking maturity

- [x] Add at least one more real named whale-watcher provider (beyond the
      `generic_rest` default and the unused `template_provider.py`) with
      actual, tested setup steps. Researched first, not assumed: Kalshi has
      no public trader identity or leaderboard (trades are anonymous
      member-to-member), so unlike Polymarket's on-chain wallet-based whale
      trackers (see `docs/simmer-integration-research.md`), a real Kalshi
      provider can only be size-based. Turned out this app already had the
      raw data for free — `services/kalshi_client.py`'s public, no-auth
      `get_trades()`, already fetched every tick for the UI trade tape.
      New `services/whalewatchers/kalshi_trade_tape.py`: classifies a real
      trade as a whale print once its side-aware real notional dollar size
      (`count_fp * yes_price_dollars` or `no_price_dollars` — the same
      no-side-cost lesson this app already paid for once) clears a
      configurable threshold (`whale_watcher_kalshi.min_notional_usd`,
      default $2500). Needs zero credentials and zero extra API calls —
      reuses each tick's already-fetched market/trade data via a new
      `market_context` param on `WhaleWatcherProvider.fetch_signals()`.
      Confidence scoring extracted from `WhaleSimulator._score_confidence`
      into a shared, noise-free `composite_confidence()` so the real
      provider reuses the exact same four-factor formula without the
      simulator's synthetic noise. Verified against real, live trade data
      (correctly zero signals at the $2500 default against real trades
      currently under $1,000; correctly produced real signals when
      temporarily lowered), and end-to-end through a real `ddev restart`
      and the actual trading loop. Direct follow-up once verified working:
      "there's no reason to have the simulator enabled by default" — the
      default provider flipped from `generic_rest` (silently simulator-only
      with no URL set) to `kalshi_trade_tape`; the Config tab's Whale Signal
      section split into simulator/real halves, with the inactive one
      visually grayed out based on which is actually running each tick.
      13 new tests (`tests/test_whalewatchers_kalshi_trade_tape.py`) plus 2
      locking in the `composite_confidence()` extraction. Full suite: 275
      passing (was 262).
- [x] Widened market coverage and made "live markets only" filterable at
      discovery/search time, not just at the strategy/simulator level.
      Direct report: "you're not looking at trades from nearly enough
      markets even with the round robin... you should be monitoring closer
      to 50." `kalshi.watchlist_size` 20 → 50, with the dependent caps that
      scale with it bumped in tandem (same pattern as the earlier 8 → 20
      change): `_get_top_series`'s candidate-series pool 30 → 40, and
      `_fetch_trade_tape`'s total cap 50 → 100 (promoted to a named
      `_TRADE_TAPE_TOTAL_CAP`) — this cap is now the entire input to the
      real whale provider above, not just cosmetic for the UI panel, so
      widening the watchlist without widening this too would have quietly
      capped whale-detection coverage right back down.
      Direct follow-up: "for market discovery and watchlist i want to have
      the option to only include LIVE markets" — a third, distinct gate
      from the two that already existed (`strategy.live_markets_only` gates
      whether the strategy *acts* on a signal; `whale_signal.live_markets_only`
      is simulator-only); this one filters which markets get selected into
      the watchlist/candidate pool in the first place. Two real design
      forks resolved directly before building, not assumed: fallback
      behavior when too few markets are live (chosen: shrink the watchlist,
      even to zero, never backfill with non-live markets to hit
      `watchlist_size`) and the cost/latency tradeoff of checking live
      status across the *wider candidate pool* before round-robin selection
      rather than just the final watchlist after the fact (accepted —
      checking only the already-narrowed watchlist would have meant "only
      live" really meant "only live among whichever markets already won on
      volume," which could easily be zero of them). `services/kalshi_client.py`'s
      `get_top_volume_markets()` split into two composable, independently
      testable pieces: `get_candidate_markets()` (fetch/filter/sort, no
      cutoff) and a new static `round_robin_select()` (the existing
      selection logic, now a pure function of its input, callable on a
      pre-filtered subset) — `get_top_volume_markets()` itself becomes both
      composed together, fully behavior-preserving. New
      `kalshi.live_markets_only` config (default `false`); when on,
      `main.py`'s `_fetch_markets()` fetches the full candidate pool, checks
      live status across all of it, filters to live-only, then round-robins
      the final watchlist from that subset. Extended to
      `GET /api/markets/search` too, direct request ("same for market
      search") — a new `live_only` query param, same candidate-then-filter-
      then-select approach, reused in both search UIs (the Markets tab
      browse panel and the Config tab's watchlist-management search).
      Verified live across several ticks: watchlist correctly shrank to 6,
      then 12 markets with the flag on (100% actually live each time, per
      `state.live_status`), back to 50 with it off; search's `live_only=true`
      returned 12 real, currently-live results. 6 new tests
      (`tests/test_kalshi_client.py`). Full suite: 281 passing (was 275).
- [x] Improve series/category grouping — "better Kalshi series metadata"
      turned out to already exist and just be unused, confirmed by
      actually querying `get_series_list()` directly rather than assuming
      it was still unavailable: real titles ("ITF Women's Match") and a
      real, clean 18-category taxonomy (via `category`, e.g. "Sports")
      plus finer tags (e.g. "Tennis") per series. The grouping *key* itself
      (`services/signal_log.series_of`'s ticker-prefix heuristic) turned
      out fine as-is — every prefix checked matched a real series ticker
      exactly — what was actually missing was a human name for it. New
      `main.py:_series_meta_map()` builds a ticker→{title, category, tags}
      lookup from the series list already cached hourly for the watchlist/
      search (zero extra API cost), exposed as `state["series_meta"]`.
      Caught and fixed a real mistake before it shipped: the first version
      dumped the *entire* ~9,400-entry cache into every `/api/state`
      response, ballooning it from ~30-50KB to over 1MB in one line —
      confirmed by actually measuring, not assumed safe. Fixed by scoping
      to just the series on the current watchlist
      (`state["series_track_record"]`'s own `series` values), which is how
      it shipped. Now used in the whale win-rate block ("ITF Women's Match
      (Tennis)-type markets" instead of `"KXITFWMATCH"`) and the matching
      plain-English skip reason, both falling back to the raw ticker
      whenever a series isn't in the scoped map yet, same as this app's
      other real-data-or-honest-fallback patterns.
- [x] Let a user manually exclude a specific whale/source from the
      strategy, not just the automatic win-rate cutoff. Semantics resolved
      explicitly before building anything (direct instruction: lay out the
      intermediary design steps first) — Kalshi's trade tape is anonymous,
      no whale identity exists to exclude by, so "series" (the same
      grouping the automatic win-rate filter already uses) is the
      implementable, honest interpretation: a manual denylist on top of
      the automatic cutoff, for a series distrusted before it's racked up
      enough resolved signals to trip the automatic filter on its own.
      `signal_log._series_of` promoted to public `series_of()` so both the
      automatic filter and this new manual one share one series
      definition, not two that could quietly drift. New
      `strategy.excluded_series: []`, checked in both
      `FollowTheWhaleStrategy.evaluate()` and `ShadowTrader.evaluate()`
      (same mirroring pattern as `live_markets_only`), a Config tab
      denylist editor (comma-separated, same UI pattern as the existing
      category filter), and a `plainEnglishSkipReason()` case. 6 new tests
      (3 strategy, 2 shadow, plus verifying the public rename didn't break
      anything).
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
- [x] Follow-up, direct request: a matching toggle one level upstream, on
      the simulator itself rather than the strategy's trade-decision gate.
      `strategy.live_markets_only` above only decides whether a signal that
      already exists gets *acted on*; this new `whale_signal.live_markets_only`
      (default `false`) decides whether the simulator *generates a print at
      all* for a non-live market in the first place — a quiet/pre-market/
      settled market produces zero simulated whale activity, not just
      activity that then gets skipped downstream. `WhaleSimulator.maybe_generate()`
      takes `live_status`/`live_only` params, filters candidate markets to
      `live_status.get(event_ticker) == "live"` before picking one (same
      `state["live_status"]` lookup, no second definition of "live"); if
      the filtered set is empty, no signal fires that tick rather than
      falling back to a non-live market. `_score_confidence()`'s "market
      context" factor also now compares against the same filtered
      candidate set, not the full unfiltered watchlist, so "how busy is
      this market relative to the others" stays an apples-to-apples
      comparison once live-only is on. Config tab gets a matching checkbox
      under Whale Signal (simulated). 4 new tests cover on/off, no-live-
      markets-available, and that the default (`live_status` omitted)
      behaves as off. Verified live: toggled via the Config tab, confirmed
      round-tripped through `/api/config` correctly, reverted after
      testing.
- [x] Config-versioned performance tracking. Implemented as
      `services/config_performance.py`: `fingerprint()` hashes `strategy.*`
      minus `name` (so a newly added tunable field joins the fingerprint
      automatically, no code change needed), `config_variants` +
      `applied_changes` tables in `data/config_performance.db`.
      `services/paper_broker.py`'s `Position`/`Trade` gained
      `config_fingerprint` (idempotent `ALTER TABLE` migration on the live
      db); a closed position always inherits the fingerprint active at
      *entry*, even if config changed mid-hold — a documented, deliberate
      limitation, not a bug. Superseded by, and built as the direct
      foundation for, the advisory engine item below.
- [x] The advisory/recommendation engine itself, direct request
      (2026-08-08): "I want a recommendation engine/advisory system... keep
      it disabled until that data threshold has been reached." Full design
      in `docs/advisory-engine-plan.md`, then built: `services/advisory_engine.py`
      (`variant_summaries()`, `generate_recommendations()` — within-variant
      heuristics upgraded from `compute_insights()`'s hedged prose into a
      concrete suggested value, plus cross-variant comparison once two
      variants each clear the threshold). Rule-based, not ML, decided
      directly rather than assumed. The per-variant minimum-resolved-trades
      gate is enforced *inside* `generate_recommendations()` itself, not the
      route or UI — no code path can leak an under-sampled recommendation.
      New `advisory.*` config (`enabled` default `false`), routes
      (`GET /api/advisory/status`, `GET /api/advisory/recommendations`,
      `POST /api/advisory/recommendations/apply` — manual-click-only,
      re-validates fresh and writes an audit row, `GET
      /api/advisory/applied-changes`), and a `POST /api/config` rejection
      guard for `advisory.auto_apply_enabled` (same shape as
      `kalshi_account.trading_enabled`'s existing guard). New "Advisory
      Recommendations" panel on the Trading History tab, next to (not
      replacing) "Config Tuning Hints". Opt-in auto-apply's actual
      `POST /api/advisory/auto-apply/enable` confirmation-phrase endpoint is
      deliberately **not** built yet — ships as a separate, later, more
      carefully reviewed follow-up once the manual-apply path has run for a
      while; the data model (`applied_changes`, the two-flag design) is
      already in place for it. New `tests/test_config_performance.py`,
      `tests/test_advisory_engine.py`, extensions to
      `tests/test_paper_broker.py`/`tests/test_strategy_engine.py`/
      `tests/test_trading_gate.py` (including a full seed-trades →
      fetch-recommendation → apply → verify-audit-log integration test).
      Verified live via Selenium against the real running app, both the
      disabled and enabled/gated states.
- [x] Scaffolding for a future ML agent to work *alongside* (not replace)
      the rule-based advisory engine above, direct request (2026-08-08):
      "feed this heuristic suggestion data, market data, historical data,
      portfolio data, recommendation engine data, to a machine-learning
      agent... let's not pursue that until the project is already
      finished." `services/ml_feed.py`'s `build_context_snapshot()` is the
      shape of that future export — a pure function assembling one bundle
      out of data every existing service already produces, not wired into
      any route or called from anywhere yet (`docs/advisory-engine-plan.md`
      §9). The scaffolding itself is done; deliberately nothing beyond it:
      no model, no training pipeline, no route. Revisit only once the rest
      of this app is otherwise done.
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
- [x] (Stretch) Persistent flow clustering. WhaleScanr's approach to a
      genuine constraint this app already respects — Kalshi's real trade
      tape is anonymous, no usernames or account data, confirmed directly
      on their site — is to group trades into probable-same-actor
      "clusters" using statistical/behavioral similarity (same ticker/
      side, similar size, similar timing pattern), labeled with a
      confidence score, without ever claiming verified identity. Validates
      this app's current anonymous-by-design signal model rather than
      contradicting it (there's no "whale identity" field to add — Kalshi
      genuinely doesn't expose one). Built against the persisted signal log
      (not just the ephemeral, in-memory position-grouping toggle the
      signal feed already had — this is the real, lasting version): new
      `signal_log.find_clusters()` walks same-ticker/same-side signals
      ordered by time and greedily joins consecutive ones within a time
      window (30min default) *and* a size-similarity ratio (4x default) —
      a lone 500-contract print doesn't get lumped in with an unrelated
      50,000-contract one just because they share a ticker/side. Cluster
      confidence scales with print count and tightness of timing, capped
      at 0.95 — inference, never a claim of verified identity. New `GET
      /api/signals/clusters` and a "Possible Accumulation" panel on Whale
      Watch, next to Signal History. 7 new tests covering grouping,
      time-gap and size-mismatch splitting, cross-ticker/side isolation,
      confidence scaling, and sort order. Verified live against the real
      simulated signal log: real multi-print clusters detected (16 prints/
      408,645 contracts/95% confidence on one real market), clicking one
      opens the real detail modal.
- [x] Real market-data storage + a second, whale-independent paper strategy,
      direct request (2026-08-08): "market data is real, the whale data is
      fake... there's no reason why I shouldn't start storing and analyzing
      market data now... when whale watch data becomes available... those
      personal trades and whale watch data should then be incorporated."
      Asked directly rather than assumed which shape this should take
      (passive logging only, vs. a real second automated strategy) — the
      user chose the latter, backend-only for now (no dashboard panel yet).
      New `services/market_history.py`: a real per-market snapshot log
      (price/spread/volume/time-to-close, `data/market_history.db`)
      populated every trading-loop tick from the market data already
      fetched for the whale-follow strategy — zero extra API cost,
      completely independent of whale signals or of whether either
      strategy trades a given market. `momentum()` computes real price
      movement over a trailing window, with a minimum-window-coverage
      guard so a ticker with only seconds of history can't report a
      confident-looking "30-minute momentum" reading off noise.
      `compute_hypothetical_trades()` — explicitly labeled
      retrospective/hypothetical, never a claim about a real position —
      characterizes what a simple "buy the side the price already
      favored" entry would have returned, per lookback window, from real
      settlement outcomes. New `services/market_strategy.py`:
      `MarketNativeStrategy`, a second, fully independent automated paper
      strategy — momentum continuation gated by a price band, spread,
      volume, and time-to-close filters, composite entry confidence
      blending momentum/liquidity/spread (same weighted-factor mental
      model as `whale_simulator._score_confidence`). Zero whale-signal
      input. Off by default (`market_strategy.enabled: false`).
      Needed a real architecture fix to do this safely: `PaperBroker` and
      `RiskManager` both gained an optional per-instance `db_path`
      (backward compatible — existing tests' `monkeypatch.setattr(...,
      "DB_PATH", ...)` pattern still works unchanged), so `main.py`'s new
      `market_broker`/`market_risk` pair (own `data/market_broker.db` +
      `data/market_risk_state.db`) runs its own capital pool without
      colliding with the whale-follow broker's tables — `main.py` derives
      the second pair's path from the first pair's already-redirectable
      `db_path` attribute specifically so test isolation
      (`tests/test_trading_gate.py`'s real-file-safety redirect) still
      holds. `strategy_engine.py`'s settlement-closing math (the
      `terminal_price = 1.0 if result == "yes" else 0.0` fix from the
      Active Position Management section above — real bug, previously
      shipped) was extracted into a shared `close_if_settled()` so a
      second strategy can't silently reintroduce it by duplicating the old
      inline logic. `trade_analytics.py` gained a `momentum_reversal`
      close-type pattern, the market-native analog of `sentiment_reversal`.
      `main.py`'s market fetch now includes both brokers' open positions in
      `extra_tickers` (the same "a held position shouldn't go stale after
      rotating off the watchlist" fix this file already records for the
      whale broker, applied proactively here too); new debug endpoints
      `GET /api/market-strategy/state`, `GET /api/market-history/summary`,
      `GET /api/market-history/hypothetical-trades`. New Config-tab
      section ("Market-Native Strategy", 🆕 badge) with all its fields.
      New `tests/test_market_history.py`, `tests/test_market_strategy.py`
      (13 + 24 tests), `db_path`-isolation tests added to
      `tests/test_paper_broker.py`/`tests/test_risk_manager.py`, route
      smoke tests added to `tests/test_trading_gate.py`. Caught and fixed a
      real bug during this work: `market_history.py`'s `_connect()`
      originally defaulted its `db_path` parameter at function-definition
      time (`db_path: Path = DB_PATH`), which silently broke test-path
      redirection — monkeypatching `DB_PATH` afterward had no effect since
      Python binds default argument values once, at `def` time, not per
      call. Fixed by requiring an explicit argument and having every
      caller pass the module-level name (resolved dynamically at call
      time instead). Full suite: 262 passing. Verified live: `curl`/direct
      endpoint checks against the real running app confirmed
      `market_history` was already logging real snapshots within seconds
      of the reload.

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
- [x] Mobile/responsive pass — this app had zero `@media` breakpoints
      before this. New `@media (max-width: 860px)`: Terminal's fixed
      3-column `.layout` grid stacks into one naturally-scrolling column
      instead of three independently-scrolling fixed-viewport-height
      panes (the old `calc(100vh - 57px)` was itself a desktop-only
      assumption — mobile browser chrome resizes the visible viewport as
      you scroll, fighting a fixed vh height); header/util-bar/equity-strip
      wrap instead of overflowing; wide sortable tables get their own
      horizontal scroll instead of forcing the whole page sideways.
      Verified live at real narrow viewports (375-500px), not just read
      off the CSS — and caught two genuine overflow bugs doing it, not
      hypothetical: a classic flexbox gotcha (`min-width: auto` on a flex
      item refusing to shrink/wrap below its own unbroken text width,
      `.account-bar .msg`) and one `white-space: nowrap` status tag too
      wide for a narrow screen (`.account-bar .tag`) — both fixed and
      confirmed with `document.body.scrollWidth` measured directly at
      several viewport widths, not assumed fixed.
- [x] Accessibility pass, partial and honestly scoped as such — added
      `aria-label`s to icon-only controls (modal close buttons, the 🐋
      "why this position was opened" disclosure). Colorblind fallback
      checked, not assumed: every yes/no indicator in this app already
      pairs color with a "YES"/"NO" text label, never color alone. Full
      keyboard navigation for every clickable card/row (`<div onclick=...>`
      elements have no `tabindex`/keydown handling today, so a keyboard-
      only user can't activate them at all) is a real, larger gap still
      open — didn't want to claim it done via a handful of `aria-label`
      additions.
- [x] Follow-up bug, direct report: the 🐋 "why this position was opened"
      disclosure sits inside a `<tr onclick="openMarketDetail(...)">` (see
      `renderPositions`) — clicking it to expand also opened the
      market-detail modal underneath, since the click bubbled up to the
      row. Fixed with `event.stopPropagation()` on the `<details>` element.
      Verified live: clicking the icon expands it without opening the
      modal; clicking the row itself still opens the modal as before.

## P4 — Nice-to-haves

- [ ] Notifications (email/push) for real trades, kill-switch triggers, or
      a tracked whale's win rate crossing the avoidance threshold.
- [ ] Finalize the app name — "Nessie" vs. "Operation Deepscan" is still
      an open decision; once picked, it needs to flow through page titles,
      headers, and the README.
