# Roadmap: comprehensive, dummy-proof paper trading terminal

Guiding principle: someone who has never touched a prediction market or a
trading interface before should be able to open this app and understand
*what they're looking at*, *why it did what it did*, and that *no real money
is ever at risk* unless they deliberately configure it to be. Everything
below is measured against that bar, not against "does it technically work."

This is a living to-do list, not a snapshot — check items off in place and
add new ones as they turn up. For what's already built and in what order,
see `static/status.html` (`/status`) — this file is forward-looking, that
one is the historical record.

## P0 — Safety & correctness (before anything else)

- [x] Verify real Kalshi balance/position/fill field names against an actual
      connected account. **Also found and fixed a real bug in the process:
      every signed request was returning 401 Unauthorized**, on both
      production and demo hosts — the signed message omitted the
      `/trade-api/v2` prefix that Kalshi's own docs example
      (`path='/trade-api/v2/portfolio/balance'`) includes. This had never
      been tested against a real key before; nothing was wrong with the
      account/credentials, the signing code itself was wrong. Fixed in
      `services/kalshi_account_client.py` (`_base_path`, derived from
      `base_url` rather than hardcoded, so it's correct on production,
      demo, or any other host). Also added `KALSHI_ACCOUNT_BASE_URL` so the
      account client can point at a different Kalshi environment than
      public market data does — demo and production use separate
      credentials entirely. Once auth worked, real field names turned out
      to differ from the guesses in several places: `market_positions`
      entries use `position_fp`/`market_exposure_dollars`/
      `total_traded_dollars`, not `position`/`market_exposure`/
      `total_traded`; fills use `count_fp`/`yes_price_dollars`/
      `no_price_dollars`, not `count`/`size`/`yes_price`/`price` — the old
      guesses didn't exist on any real response and silently rendered
      "—"/blank for every row. Balance turned out to have two genuinely
      different real numbers (`balance` = uninvested cash, `portfolio_value`
      = cash + open positions) that the old code picked between as if one
      were a fallback for the other; the dashboard now shows both labeled
      explicitly. Raw JSON stays visible everywhere regardless, since
      Kalshi's docs can still drift again. 2 new regression tests
      (`test_signed_message_includes_the_trade_api_v2_prefix`,
      `test_base_path_derived_from_base_url_not_hardcoded`); the field-name
      fixes were verified live against the real connected account (visually
      confirmed in a real Chrome session, not just curl).
- [x] Verify the `create_order`/`cancel_order` request schema against
      Kalshi's *current* docs. **The concern was justified — the original
      implementation was wrong.** It targeted Kalshi's legacy order shape
      (`POST /portfolio/orders`, `action`+`side(yes/no)`+`count`+
      `{yes,no}_price` in cents). Current docs (verified 2026-08-07 by
      fetching docs.kalshi.com directly) show the endpoint moved to
      `POST /portfolio/events/orders` / `DELETE
      /portfolio/events/orders/{order_id}`, `action`+`side` collapsed into
      one `side` field (`"bid"`/`"ask"`, no `"no"` value — selling YES and
      buying NO are the same order-book trade), `count`/`price` are now
      *strings* (contracts and dollars, not integer cents), and
      `time_in_force`/`self_trade_prevention_type` are newly *required*
      fields with no old equivalent. Kalshi's docs note migration off the
      legacy endpoint "no earlier than May 6, 2026" — today is past that,
      so the old code could already have been rejected outright, silently,
      the first time it was ever used. `services/kalshi_account_client.py`
      is rewritten to the current shape; read endpoints (balance/positions/
      fills) were also checked and are unchanged. 4 new tests
      (`tests/test_kalshi_account_client.py`) verify the exact request
      shape sent (path, method, body fields) against a faked HTTP client —
      no network call, no real credentials. The balance/position/fill
      *response* field-name item right below this one is still open — that
      one needs a real connected account to verify, which is out of scope
      for this pass.
- [x] Add an in-app confirmation step before real trading can be enabled.
      `POST /api/config` now structurally refuses to touch
      `kalshi_account.trading_enabled` at all — the only path is
      `POST /api/trading/enable`, which requires both a connected real
      account and an exact-match typed confirmation phrase ("ENABLE REAL
      TRADING", not a checkbox). `POST /api/trading/disable` always works,
      no confirmation needed. Dashboard gets a matching control on the
      account bar. Covered by 8 backend tests (`tests/test_trading_gate.py`,
      isolated from the real config/settings.yaml and data/*.db) and
      verified live in a real browser via the project's ddev selenium-chrome
      service, including that the confirmation input survives the
      dashboard's periodic 5s refresh instead of getting wiped mid-typing.
- [x] Persist paper broker state (bankroll, open positions, trade log)
      across restarts. `services/paper_broker.py` now persists to
      `data/paper_broker.db`, same SQLite pattern as `signal_log.py`.
      `services/risk_manager.py`'s daily-loss baseline and kill-switch halt
      state were persisted alongside it (`data/risk_state.db`) — without
      that, a restart would keep the recovered bankroll but reset the loss
      baseline to `config/settings.yaml`'s `starting_bankroll`, either
      mismeasuring today's loss or silently un-halting a tripped kill
      switch. A plain restart now resumes; `POST /api/reset` is the only
      thing that wipes it. Verified end-to-end with a real `ddev restart`.
- [x] Tighten CORS (`allow_origins=["*"]` in `main.py`). Now defaults to the
      DDEV hostname + `localhost:8000`, overridable via a comma-separated
      `ALLOWED_ORIGINS` in `.env` for any other deployment. Verified live:
      preflight from the DDEV origin gets `access-control-allow-origin`
      back, a random origin gets nothing.
- [x] Build shadow mode (logs intended real trades, executes nothing).
      `services/shadow_mode.py`'s `ShadowTrader` runs the exact same
      follow-the-whale gates as `strategy_engine.py` (confidence, whale
      win-rate filter, cooldown, position-size-rounds-to-zero, its own
      independent daily-loss kill switch) but sized against a *real*
      reference bankroll — the connected account's actual balance if one
      exists, or `risk.starting_bankroll` as a clearly-labeled fallback
      when it doesn't — and only ever logs the result to
      `data/shadow_mode.db`; `create_order` is never called. Active when
      `mode: shadow` or `mode: live` in `config/settings.yaml` (a Controls
      panel dropdown now exposes this — previously `mode` was set in the
      YAML but read by nothing). Portfolio view gets a Shadow Trades panel.
      10 tests cover every gate in isolation; live-verified end-to-end
      against the running app (flipped to `shadow`, watched a real
      intended-trade row appear with the correct fallback-bankroll label,
      confirmed the dashboard renders it, reverted back to `paper`
      afterward).

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

- [x] A shared Simple/Advanced toggle pattern — one small, reusable
      component (a per-panel header button + a bit of client state) so
      every panel below implements the same interaction instead of five
      one-off toggles. Decide once whether the choice persists (e.g.
      `localStorage`) or resets each session before building the rest on
      top of it. Shipped: `isAdvanced`/`toggleAdvanced`/`advToggleHTML` in
      `static/index.html`, choice persists per panel via `localStorage`.
      First consumer is the order book drill-down below.
- [x] Navigation decision: Simple/Advanced toggles are the right fit for
      panels that show the *same list* at different density (trade log,
      market cards, signal feed) — they're not the right fit for order
      book depth or a price chart, which are inherently per-market and too
      space-hungry to cram into a card grid (can't show a full depth
      ladder + candlestick for 20 markets inline at once). Resolution: a
      new per-market drill-down view — click any market to open a
      dedicated detail panel with its order book, chart, and recent trades
      together, closer to Kalshi Pro's Canvas than an in-card toggle.
      Simple/Advanced stays the pattern for same-data-different-density
      panels; the drill-down is the pattern for space-hungry per-market
      detail. The two items below are written against the drill-down, not
      a card-level toggle.
- [x] Kalshi-accurate terminology + real Event/outcome grouping. Two
      distinct problems, confirmed by reading a real raw market object
      (not assumed): (1) rows across the app under-described what they
      showed — e.g. a position row was just `500 ct @ 62¢ → 65¢`, no label
      saying that's contract count, entry price, and current price. (2)
      `event_ticker` — the field that would let the UI show "this market
      is one of N possible outcomes of the same underlying question" —
      was fetched from Kalshi but dropped immediately in `main.py`'s
      `_slim_market()`, never reaching the frontend, so sibling outcome
      markets rendered as fully disconnected cards. Both shipped: (1)
      position/trade/fill rows across `renderPositions`/`renderTrades`/
      `renderRealPositions`/`renderRealFills` now say "N contracts @ X¢
      entry → Y¢ now" / "fill price" instead of bare `ct`/`qty` shorthand,
      with tooltips explaining each figure. (2) `_slim_market()` now keeps
      `event_ticker`/`close_time`/`strike_type`; a new `_fetch_event_titles()`
      fetches each event's own title/category via a new `get_event()`
      wrapper — but only for events with more than one sibling market in
      the current watchlist batch, cached like `market_titles` so a
      solo-outcome market never pays for an unnecessary lookup.
      `renderMarketCards` groups sibling markets under one event header
      (`.event-group`) instead of N disconnected cards; groups of one
      render exactly as before. Verified two ways: the backend grouping
      logic directly against a real 200-market batch with confirmed real
      sibling events, and the frontend rendering via an injected synthetic
      state through a real Chrome session (ddev's selenium-chrome) showing
      correct group/solo separation. Narrower than originally scoped: the
      full "audit every panel's labels against Kalshi's vocabulary" is
      ongoing, applied incrementally as each panel is touched, not a single
      one-shot pass over the whole app — the market-card "meta" line's raw
      ticker-prefix-as-category still isn't a real series name (no
      `series_ticker` field on the market object itself, only on its
      event), left for a future pass rather than adding a per-market event
      fetch just for that one line.
- [x] Follow-up on the above, from direct feedback: still too much raw-
      ticker/mental-math left in practice — a user shouldn't have to parse
      `KXMVESPORTSMULTIGAMEEXTENDED-S2026CDE6FF21DD8-582E1DFB434` or
      multiply contracts by price themselves to know what sport, who's
      playing, what a Yes/No position means, or how much money is actually
      in it. Two fixes. First, a real bug caught while investigating: the
      earlier event-title code read `event.get("subtitle")`, which doesn't
      exist — the real field is `sub_title` (confirmed directly against a
      live event: "SD vs AZ (Aug 6)" only came back under that key) — so
      every event's subtitle had silently been `None` since it shipped.
      Second, event fetching was previously scoped to only multi-sibling
      groups; broadened to every market's event, since this data is also
      what answers "what sport, who vs who" for a *single* market, not
      just grouping. `market_titles` now carries an object
      (`{title, yes_sub_title, no_sub_title, event_ticker}`) instead of a
      single collapsed string, so a Yes/No position's actual meaning
      ("Betting: San Diego") is available, not just a side tag. New
      `marketContext()`/`contextLineHTML()` combine this with
      `event_titles` to show category + real matchup + position meaning on
      position/trade/fill rows, market cards, and the drill-down modal
      title. New `costHTML()` shows the actual dollar total put into a
      position/trade (contracts × price) next to the existing max-payout
      figure — directly requested, previously required doing that
      multiplication by hand. Verified end-to-end through a real Chrome
      session with realistic clean-market data (an MLB matchup, not the
      messy 7-leg combo markets this account's current watchlist happens
      to favor): a position row correctly rendered "Sports · SD vs AZ (Aug
      6) · Betting: San Diego" plus "$6.20 put in," and a market card
      correctly showed the matchup line and "Sports" in place of the old
      raw ticker-prefix meta tag.
- [x] Second follow-up, also from direct feedback with real reference
      screenshots of actual Kalshi UI provided: the multi-sibling
      `.event-group` wrapper (from the original event/outcome-grouping
      work) rendered N *full* `marketCardHTML()` cards side by side under
      one header — better than N disconnected cards, but still "a clear
      lack of data for any market in particular," and nothing like how
      real Kalshi actually shows a multi-outcome event. The screenshots
      showed the real pattern directly: one card, a compact list of
      outcome rows (name + price), not N duplicate cards. Rebuilt
      `eventGroupCardHTML()` to match exactly that — category badge, event
      title, then each sibling as one row (outcome name from
      `yes_sub_title`, Yes/No price pills), sorted highest-probability
      first, each row clickable straight into the per-market drill-down.
      Verified through a real Chrome session: correct category/title,
      correctly-sorted rows, and clicking a row opened the right specific
      outcome's drill-down with the right context line.
- [x] Market discovery was fundamentally broken, not just under-filtered —
      found via direct pushback ("you're wrong that combo markets are all
      that's open") on the assumption that the watchlist reflected real
      market conditions, and root-caused properly rather than patched.
      Kalshi auto-generates a huge number of "MVE" (multivariate event /
      combo) markets, and confirmed directly, repeatedly, with real
      numbers: a flat browse of even 50,000+ open markets (the API's own
      cursor pagination, run to that depth) can return **zero** with any
      real trading volume, because combos vastly outnumber real markets in
      the API's default ordering. `get_top_volume_markets` used to browse
      a 100-market page and sort it — both too shallow a sample and
      sorting a mostly-dead sample doesn't help. Two real SDK gotchas
      caught along the way, neither guessed: passing `mve_filter=None`
      explicitly (instead of omitting the kwarg) silently changed the
      result set at the wire level (271 real markets found vs. 0); and the
      real fix isn't excluding combos by type at all — per direct
      correction ("you shouldn't be filtering out markets... only filter
      out markets with 0 volume") — it's querying by **series** instead of
      browsing individual markets. `get_series_list(include_volume=True)`
      returns all ~12,500 series (a series is a recurring-event template —
      "Pro Basketball Game," "Bitcoin price up/down" — confirmed via the
      SDK's own docstring) with real lifetime volume in one ~1.1s call;
      querying `get_markets(series_ticker=...)` against just the
      highest-volume *currently active* series (KXMLBGAME, KXPGATOUR,
      KXBTCD, KXATPMATCH all verified directly) reliably returns clean,
      real, well-titled markets. Cached in `state["series_cache"]`
      (refreshed hourly, not every 15s poll tick — a full series fetch is
      too expensive to repeat that often) via new `_get_series_cache()`/
      `_get_top_series()`. `kalshi.min_volume_24h` (config, default 1) and
      `kalshi.categories` are the resulting configurable criteria for what
      counts as "active enough" for the automatic watchlist — pinned
      markets (see the watchlist item below) always show regardless.
      Search (below) reuses the same series-based approach for the same
      reason: an early version of it browsed markets directly (even
      cursor-paginating 5,000+ for a text query) and that was just as
      unreliable — text-matching against series title/tags/category
      first, then querying only matching series, is what actually works.
      Verified repeatedly against live data, not assumed fixed after one
      good result — the watchlist now reliably surfaces things like PGA
      Tour and Bitcoin markets instead of `KXMVESPORTSMULTIGAMEEXTENDED-*`
      combo tickers.

Concrete gaps against the current 4-tab dashboard (Portfolio, Markets,
Whale Watch, Terminal — see `static/index.html`), each already framed as
a Simple/Advanced pair using the toggle above:

- [x] Per-market drill-down modal + order book depth (chart and per-market
      trades below are separate, not done yet — this shipped the modal
      shell and its first tenant). Click any market — a card or a Terminal
      watchlist row — to open a dedicated detail panel, not an inline card
      toggle. `services/kalshi_client.py` already had `get_orderbook()`
      (`get_market_orderbook` under the hood), implemented and unused;
      wired up via a new `GET /api/markets/{ticker}/orderbook`. Simple:
      best Yes bid/ask + spread, derived from the book itself (best Yes
      bid = highest resting Yes-bid price; best Yes ask = 1 − highest
      resting No-bid price). Advanced: the full two-sided depth ladder,
      shown in Kalshi's own bid/bid terms (Yes bids, No bids) rather than
      converted to a single Yes bid/ask ladder, so a conversion mistake
      can't silently hide inside the display. Verified against real
      Kalshi data two ways: a thin market with an empty book (both sides
      correctly show "no resting bids" instead of breaking), and a market
      with an actual resting order (Advanced ladder correctly showed its
      real price/size) — driven through an actual Chrome session via
      ddev's selenium-chrome, not just curl.
- [x] Price history in the same drill-down (the app previously had one
      chart total — portfolio equity-over-time, hand-rolled inline SVG).
      Kalshi's `get_market_candlesticks` requires a `series_ticker`, which
      market objects don't carry directly (only `event_ticker`) — verified
      via the SDK's own docstring rather than guessed, since a wrong value
      there is a hard API error, not a silently-wrong display; resolved via
      one `get_event()` lookup per drill-down open, using `event_ticker`
      the frontend already has on `state.markets`. New `GET
      /api/markets/{ticker}/candlesticks` (fixed window: last 7 days,
      hourly) and a `get_candlesticks()` wrapper. Simple: a compact
      sparkline (hand-rolled inline SVG, same approach as the equity
      chart). Advanced: real OHLC candlesticks + a volume bar per period,
      also hand-rolled SVG, no charting library. Candlestick `price.*`
      fields are null for any period with no actual trade (confirmed on
      real data, common on thin markets) — falls back to the yes_bid/
      yes_ask midpoint, then carries the last known value forward, rather
      than plotting a misleading drop to zero. The chart gets its own
      Simple/Advanced toggle, independent from the order book's — verified
      live that toggling one doesn't affect the other. Verified end-to-end
      through a real Chrome session against a market with real trading
      history: sparkline rendered, Advanced showed the correct candle/
      volume-bar count matching the real API response exactly.
- [x] Recent trades for that one market, also in the drill-down (not the
      full-exchange trade tape below, which is a separate, Terminal/Whale-
      Watch-level feed) — the SDK's `get_trades` accepts a plain `ticker`
      filter (no `series_ticker` complication, unlike candlesticks). New
      `GET /api/markets/{ticker}/trades` and a `get_trades()` wrapper;
      shows the last 15 trades (taker side, contracts, price, time), no
      Simple/Advanced split — a short recent-trades list doesn't have a
      meaningfully denser "Advanced" form the way the book/chart do.
- [x] Full-exchange trade tape — distinct from the per-market drill-down
      above, a Whale Watch-level feed across every market being watched,
      not one at a time. Scoped to the current watchlist rather than the
      literal whole exchange: `get_trades` with no ticker filter returns
      trades across every Kalshi market, most of which aren't on anyone's
      watchlist here and would just be noise next to the whale-signal
      concept this ties into — new `_fetch_trade_tape()` instead fires one
      `get_trades(ticker=X, limit=5)` per watched market concurrently
      (same pattern `_fetch_markets` already uses for its explicit-
      watchlist branch), merges, sorts newest-first, caps at 30. Simple:
      the top quartile by size in the current batch, plain English
      ("Someone bought 500 YES on..."). Advanced: the full tape with a
      minimum-size filter — matched against how Polywhaler/WhaleScanr
      actually let you filter their tape (researched directly, not
      invented) rather than designing one from scratch. New panel in the
      Whale Watch tab, its own Simple/Advanced toggle. One real gap caught
      by testing through an actual browser rather than just reading the
      code: the filter `<input>` had no `id`, so nothing (a test, or any
      future script) could target it — added. Verified live: Simple
      correctly showed only the larger of four synthetic trades in plain
      English; Advanced showed all four with working size filtering down
      to just the ones at or above the threshold.
- [ ] Trade log / decision feed. The Simple side of this already exists —
      the Portfolio "Betting vs. Likelihood vs. Risk vs. Whales" plain-
      English pattern, and P1's item to extend it to Strategy Decisions'
      skip reasons — extend it, don't replace it. Advanced: the same
      underlying data (`renderTrades`/`renderDecisions`) as a sortable/
      filterable table — ticker, side, size, price, P&L, raw confidence
      numbers — instead of today's strict reverse-chronological feed with
      no sort, filter, or search.
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
- [x] Potential payout, on every position/trade/fill row — Simple tier
      shipped (the Advanced full breakdown — cost, mark-to-market,
      breakeven — is still open, a separate follow-up, not blocking).
      Kalshi contracts settle to $1 or $0 per contract, so max payout if
      correct is just the contract count in dollars — cheap to compute,
      was simply never shown. New shared `payoutHTML()` helper, wired into
      all four row renderers (`renderPositions`, `renderTrades`,
      `renderRealPositions`, `renderRealFills`), including the real-account
      rows where the count comes as a fixed-point string (`position_fp`/
      `count_fp`) that needs parsing first.
- [x] Follow-up, direct request with a real screenshot of Kalshi's own
      positions table: `renderPositions` rewritten from one flat line item
      per position into a real grouped table — positions sharing an
      `event_ticker` (e.g. two different outcomes of the same election
      both held) grouped under one market header with a Total row, exact
      column structure confirmed against the screenshot (Position,
      Contracts, Entry, Now, Cost, Payout if right, Value, Return) rather
      than invented. Two more things folded in from the same request
      round: a whale-confidence badge per position (`🐋 78%`, reusing
      `computeWhaleLean` — already built for Whale Watch's market cards,
      just never applied to Portfolio) showing whether whale prints on
      that market agree or disagree with the side actually held; and a
      🐋 icon that expands to show *why* a position was opened —
      `services/paper_broker.py`'s `Trade.reason` (e.g. "whale print 5230
      @ 0.62 (conf 0.78)") was already being computed, persisted, and sent
      to the frontend in `broker.recent_trades`, just never displayed
      anywhere — no backend change needed, only surfacing data that
      already existed. Also looked into whether Kalshi exposes a "largest
      open positions on this market" dataset, direct request — confirmed,
      three independent ways (SDK method introspection across every API
      class, Kalshi's own API reference endpoint index, and Kalshi's
      LLM-oriented docs index, which states outright: "No endpoints exist
      for leaderboards, largest traders, market holders, or public
      position rankings") that this data genuinely isn't exposed — the
      "Kalshi Leaderboard" that does exist ranks overall trader P&L/
      performance, unrelated to per-market position size. Not built, since
      the underlying data doesn't exist to build it from; `open_interest_fp`
      (aggregate, not per-holder) is the closest real substitute, noted
      but not implemented this pass. Verified end-to-end through a real
      Chrome session: correct grouping, a Total row only on the multi-
      position group (not the solo one), correct sums, and the reason
      details expanding to show real trade data on click.
- [x] Market search and browse. Shipped as one combined tool (search text +
      category filter + include-dormant toggle, not two separate features
      as originally scoped) in the new Config tab. New
      `GET /api/markets/search` — series-based, same approach and same
      reason as the market-discovery fix above (text-matches
      title/tags/category against the cached series list, then queries
      only matching series) — defaults to `min_volume=0` (dormant markets
      included) so a market being excluded from the automatic watchlist
      never means it's unreachable. Results are checkbox-multi-selectable;
      "Add Selected to Watchlist" merges the picks into
      `kalshi.markets_watchlist` via the existing `/api/config` patch
      endpoint (no new endpoint needed — `ConfigStore.update()`'s shallow
      per-key merge already does the right thing). Verified end-to-end
      through a real Chrome session: searched "golf," multi-selected two
      Wyndham Championship outcome markets, added them, confirmed they
      appeared in the pinned list, removed one, confirmed the removal.
      Category taxonomy is a plain text field for now (Kalshi's
      `get_tags_for_series_categories` endpoint, for a real dropdown of
      valid categories, wasn't explored this pass) — a reasonable
      follow-up, not required for this to be useful today.
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
- [ ] Price-change indicators. Every price in the app (market rows, cards,
      positions) silently replaces on each 5s poll with no acknowledgment
      that it moved — no up/down arrow, no color flash, no delta. Every
      real trading UI (Kalshi Pro included, via its rolling 5-minute-volume
      ranking) treats "did this just move" as first-class information, not
      an afterthought. Simple: a brief color flash + arrow on change.
      Advanced: an explicit delta (¢ and %) since last poll or over a
      rolling window, feeding the same screener-style table above.
- [x] A real "LIVE" badge, direct request, built properly rather than
      settled for a proxy — an explicit instruction to spend the effort
      and not guess paid off: the first attempt was a timestamp-only
      heuristic (`occurrence_datetime` passed + market still open), openly
      flagged as not the same guarantee as Kalshi's real signal. Pushed
      further and found the real one. Kalshi's live status is powered by a
      milestone/live-data system (`MilestoneApi`/`LiveDataApi`), not
      exposed on the market or event object directly — the missing link is
      `get_milestones(related_event_ticker=...)`, which returns the actual
      scheduled game/match (id, type, start_date) tied to an event; that
      milestone's `id`/`type` then feed `get_live_data()` for the real
      status. Confirmed empirically, not from docs alone: a milestone's
      `end_date` stays `null` forever, even for a fully settled game — that
      hypothesis was tested and killed before it shipped. The real signal
      is `details.widget_status`, confirmed directly by watching a real AFL
      match go live at its actual scheduled start time:
      "none" (scheduled) → "live" (in progress) → "finished" (over). New
      `get_milestones_for_event()`/`get_live_data()` wrappers and
      `_fetch_live_status()`, checked only for markets whose
      `occurrence_datetime` falls in a plausible live window (most
      watchlist markets aren't starting imminently, so this is rarely two
      wasted API calls) — replaced wholesale every poll tick, not
      accumulated, since a stale "live" would be actively wrong, not just
      incomplete. Verified against real, live production data: a genuine
      in-progress tennis match was correctly flagged live in the running
      app, not just in a synthetic test.
- [ ] Visible staleness/connectivity state. `refresh()`'s catch block
      today only does `console.error('refresh failed', e)` — if
      `/api/state` starts failing (network blip, backend restart,
      ddev-router hiccup), the dashboard just silently stops updating with
      no visible signal to the person watching it. For an app whose whole
      premise is "watch this and trust what it shows you," a stale/dead
      connection should be as loud as the exchange-closed badge already is
      (`renderExchangeStatus`) — same pattern, applied to connectivity
      itself: a visible "data may be stale, last updated Ns ago" state
      once a poll fails or a response is overdue.
- [x] Header equity-strip doesn't follow the Portfolio account-mode
      toggle — a real inconsistency, not a hypothetical: `refresh()`
      unconditionally set the header's Bankroll/Equity/Unrealized P&L
      (and the static `PAPER` tag) from `broker.*` every poll, regardless
      of `accountMode`. Switching Portfolio to "💰 Real Kalshi Account"
      correctly showed real balance/positions/fills in the tab body while
      the header above the tabs kept showing paper numbers under a
      `PAPER` label the whole time. Fixed via a new `renderHeaderStrip()`
      that follows `accountMode` — Cash Balance/Portfolio Value/session
      change and a `REAL` tag when real mode is active and connected,
      Bankroll/Equity/Unrealized P&L under `PAPER` otherwise. Fixing this
      surfaced a second, previously-unknown bug in the same code path:
      `main.py`'s `real_balance_history` was appending the raw cents value
      instead of dividing by 100 — every consumer of that history (the
      real-mode equity chart, and now this header) expects dollars, so a
      real $1,000.00 balance would have silently rendered as
      "$100,000.00." Fixed at the source in `trading_loop()`.
- [x] A real watchlist / pinned-markets concept — shipped together with
      market search above (same Config tab section), since pinning is
      what the search results' multi-select feeds into.
      `kalshi.markets_watchlist` already existed as a config field and
      `_fetch_markets` already preferred it over automatic discovery when
      non-empty — it just had no UI, only hand-editing `settings.yaml`.
      Now Markets and Whale Watch both show pinned markets consistently
      (they already shared `renderMarketCards`, so this required no
      rendering changes, only the config-editing UI). Still a prerequisite
      for anything Canvas-like (multiple pinned markets, each with its own
      book/chart) later, as originally noted.
- [ ] Reassess the Markets vs. Whale Watch split now that both share
      `renderMarketCards` — give them a genuinely distinct job (e.g. Whale
      Watch leans into the trade-tape/screener angle, Markets becomes the
      per-market book+chart view) or fold them into one tab with a filter,
      rather than two tabs showing near-identical cards today. Simple/
      Advanced modes reduce some of the pressure to split by density, but
      they're still duplicated content either way.
- [x] Bounded-height, scrollable list panels. Not a data problem — the
      backend already caps every feed sent to the frontend (paper trades
      to 25, decisions to 50, shadow trades to 25) — it was purely a
      layout one: none of `.trades-list`/`.positions-list`/`.shadow-list`/
      `#signal-feed`/`#decision-feed` had any `max-height`/`overflow`, so
      even a capped 25-50-row feed rendered as 25-50 full-height DOM rows
      stacked directly in the page flow. Fixed with a new shared
      `.scroll-panel` class (`max-height: 420px; overflow-y: auto`),
      applied to all five — same pattern the codebase already used for
      `.col`/`.raw-json`, just not yet applied here. Pagination/"load
      more" remains a reasonable stretch on top, not required for this.
- [ ] Revisit the 5s polling model (`setInterval(refresh, 5000)` in
      `static/index.html`) once any Advanced view lands — a live order
      book and trade tape read as much less "live" on a 5s full-state poll
      than Kalshi Pro's presumably-pushed updates. Not blocking for the
      items above, but likely the next bottleneck once they're in, and
      probably an Advanced-mode-only concern (Simple panels don't need
      sub-5s freshness).

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
- [ ] Whale-size threshold relative to each market, not a flat number.
      Confirmed by reading the actual detection code, not assumed: both
      `whale_simulator.py` (a flat configured `size_range` tuple) and
      `whalewatchers/generic_rest.py` (`confidence: min(size / 50000,
      1.0)` — its own comment already calls this "naive... tune once you
      see real data") use one absolute size cutoff across every market
      regardless of that market's typical trade size. Dedicated Kalshi/
      Polymarket whale trackers (WhaleScanr, researched directly) don't do
      this — their methodology is relative: "roughly the size only the top
      few percent of [that market's] trades reach, plus an absolute dollar
      floor." A print that's huge for a thin market can be unremarkable
      for a liquid one; a flat threshold treats both the same.
- [ ] Composite confidence scoring, same source comparison. Polywhaler's
      "Insider Score" weighs four factors: trade size relative to market
      depth, how unusual the price/timing is, proximity to the market's
      resolution/close time, and broader market context — this app's
      `confidence` is currently a single factor (size only, in both the
      simulator and the real provider). Worth enriching once the relative-
      sizing item above lands, since "unusual for this market" is the
      shared prerequisite for most of Polywhaler's other factors too.
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

- [x] Add an automated test suite. 35 tests in `tests/` cover
      `paper_broker.py` (fill/cost-capping/P&L math, cooldowns, persistence
      across a simulated restart, reset), `risk_manager.py` (daily-loss
      kill-switch trip/stay-halted/resume, persistence), `strategy_engine.py`
      (every skip/trade branch), and `signal_log.py` (series grouping,
      win-rate math, time-window filtering). Each test isolates its own
      SQLite file via `monkeypatch`-ing the module's `DB_PATH` — none of them
      touch the real, live `data/*.db` files. Spot-verified the suite isn't
      vacuous by deliberately breaking the "no"-side P&L direction math and
      confirming the right test failed, then reverting.
- [x] Basic CI. `.github/workflows/tests.yml` runs the suite via GitHub
      Actions on every push to `main` and every PR.
- [x] Migrated fully to Kalshi's official `kalshi_python_async` SDK —
      **supersedes the "decided against it" finding below the line from
      earlier the same day.** That finding was real but was an artifact of
      `pip index versions` silently resolving to a stale, Python-3.11-
      compatible release (3.2.0) without warning that newer ones existed;
      every release past 3.2.0 requires Python ≥3.13 and the real latest
      (3.27.0) matches Kalshi's live API exactly — `get_positions`/
      `get_fills`/`get_balance`/`get_orders`/`get_exchange_status` and order
      placement (`create_order_v2`/`cancel_order_v2`) all verified working
      against the real connected account with zero `ValidationError`s.
      `Dockerfile` bumped to `python:3.13-slim`; `requirements.txt` pins the
      exact SDK version and `urllib3==2.7.0` (an undeclared dependency the
      SDK needs but doesn't list). `services/kalshi_client.py` and
      `services/kalshi_account_client.py` were rewritten to hand signing,
      endpoint paths, and request/response schemas entirely to the SDK,
      keeping the same public method signatures and dict-shaped returns so
      `main.py` didn't need to change. `_request_timeout` is accepted by
      `create_order_v2` but rejected outright by every read endpoint and by
      `cancel_order_v2` — verified by reading each method's generated
      source, not assumed; get it wrong and every read call throws instead
      of just being slow. Old note, still true: its own auth code hardcodes
      the exact same `/trade-api/v2` signed-path prefix this session's
      earlier hand-rolled-auth fix added — outside confirmation that fix was
      correct.
      <details><summary>Original "decided against it" finding (2026-08-07, superseded above)</summary>
      Investigated migrating to Kalshi's official `kalshi_python_async` SDK
      (async-native, would eliminate hand-rolled request-signing as a bug
      surface) — decided against it, and verified why empirically rather
      than from docs alone. Installed it, pointed it at the real connected
      account, and `get_positions()`/`get_fills()` both threw Pydantic
      `ValidationError`: the SDK's models require integer fields
      (`position`, `market_exposure`, `count`, `price`, ...) that Kalshi's
      live API no longer returns, only the `_fp`/`_dollars` string variants
      this project's own hand-rolled client already handles correctly
      (confirmed by reading the raw HTTP response through the SDK's own
      `_without_preload_content` escape hatch). `get_balance` and
      `get_exchange_status` work fine through the SDK; positions and fills
      don't, at the latest available version (3.2.0) as of 2026-08-07.
      Kalshi's own docs warn "SDKs are updated periodically and may lag the
      API" — this is that, hit directly. Worth re-evaluating once Kalshi
      patches it, but adopting it today would have been a regression, not
      an improvement.
      </details>
- [x] Exponential backoff on `429 Too Many Requests`, per Kalshi's own rate
      limit guidance (`docs.kalshi.com/getting_started/rate_limits` — no
      `Retry-After` header is provided, backoff is the documented
      expectation). Originally implemented as `services/http_client.py`'s
      `request_with_backoff` wrapping raw `httpx` calls; became dead code
      the moment the SDK migration above landed (Kalshi calls no longer go
      through raw `httpx` — the SDK owns the request), so it was replaced
      with `call_with_backoff`, which wraps arbitrary async SDK client
      methods instead and detects a 429 via the SDK's own exception shape
      (`.status`). The SDK's own built-in retry support doesn't cover 429 at
      all (only 5xx/connection errors), so this is still load-bearing, not
      redundant with the SDK. Only 429 triggers a retry; every other
      exception re-raises immediately. 5 tests, no real network calls or
      real sleeping.
- [x] Surface Kalshi's real exchange open/closed status
      (`GET /exchange/status`, public/unauthenticated) in the dashboard, so
      a quiet signal feed reads as "the market's closed," not "the strategy
      is stuck." Small badge in the header, only visually loud when closed.
- [x] Fixed the intermittent 403/404 errors on
      `https://kalshi-whale-poc.ddev.site/` that recurred repeatedly during
      development, and, in the same pass, actually separated the frontend
      from the API rather than papering over the symptom. Root cause:
      `ddev`'s default (unused, empty-docroot) `web` service and the custom
      `fastapi` service both auto-register a Traefik router for the exact
      same hostname (confirmed by reading ddev-router's generated
      `<project>_merged.yaml` directly: two routers, identical
      `HostRegexp`, same `https` entrypoint); Traefik's tie-break between
      two equal-priority routers isn't stable across reloads, so the site
      would randomly route to `web`'s empty docroot (→ 403) instead of
      `fastapi`. Clearing `router_http_port`/`router_https_port` in
      `.ddev/config.yaml` (an earlier fix attempt, that config's own
      comment described the symptom accurately) didn't actually fix this —
      those only control the ports ddev-router itself listens on, not which
      services get routers generated for them.
      <details><summary>First real fix (superseded below): blank out web's exposure</summary>
      Blanked out `web`'s own `HTTP_EXPOSE`/`HTTPS_EXPOSE`/`VIRTUAL_HOST`
      via `.ddev/docker-compose.web-override.yaml` so ddev's router-config
      generator never creates a `web` router for this hostname at all —
      verified by re-reading the generated config after a restart (`web`
      routers: 0, was 3) and 10/10 real external requests through the
      actual router path returning 200. Worked, but left `web` sitting
      there unused and the "fix" was a container-level workaround rather
      than addressing why `main.py` was serving the dashboard AND the API
      out of one process in the first place.
      </details>
      Real fix: `main.py` is API-only now — the `/`, `/status`, `/login`,
      `/accounts` FileResponse routes and the `/static` mount were removed
      entirely. `web` (nginx, `docroot: static` in `.ddev/config.yaml`) is
      now the one and only public entrypoint, serving `static/*.html`
      directly and reverse-proxying `/api/` + `/auth/` back to `fastapi`
      (`.ddev/nginx/kalshi-proxy.conf`); `fastapi` dropped its
      `HTTP_EXPOSE`/`HTTPS_EXPOSE`/`VIRTUAL_HOST` entirely and is reachable
      only inside the project's docker network as `fastapi:8000` — it has
      no public URL of its own anymore. This doesn't just avoid the router
      collision, it makes it structurally impossible: there is now exactly
      one service ddev-router can register for this hostname. Also means
      a separate frontend could genuinely be built against this API later,
      hitting `/api/*` directly, without touching `main.py`. Uvicorn picked
      up `--proxy-headers --forwarded-allow-ips=*` since it now sits behind
      two hops (ddev-router → nginx → fastapi) instead of one, so
      `request.url_for()` (the OAuth `redirect_uri`) still resolves the
      right scheme/host — safe to trust from any IP here specifically
      because `fastapi` has no public exposure at all, only other
      containers on this project's network can reach it. Verified: every
      page (`/`, `/status`, `/login`, `/accounts`), `/api/*` and `/auth/*`
      proxying, the `Cache-Control: no-store` behavior the old
      `NO_CACHE_HEADERS` used to provide (a real regression risk — nginx's
      `try_files <file> =404` serves a matched file within its own
      location block rather than re-entering location matching, so the
      header had to be set directly on each page's location block, not
      inherited from a shared regex block), and the full test suite, all
      against the real running ddev project, not just reasoned about.
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
