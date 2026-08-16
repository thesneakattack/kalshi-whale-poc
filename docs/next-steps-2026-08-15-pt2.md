# Next steps — 2026-08-15, continued (post rate-limit incident + API audit)

This picks up after `docs/next-steps-2026-08-15.md` (the profit-maximization
assessment) and the same day's advisory-significance-scores commit
(`8b0a7c9`). That earlier doc is now fully superseded — everything in it
shipped. This one covers a second, separate incident that happened later
the same day, plus what it left open.

## What happened (for context on why the open items below look the way they do)

A real live incident, found and root-caused in two stages:

1. **Signal resolution was head-of-line blocked.** `signal_log.
   unresolved_batch()` always re-selected the same oldest unresolved rows
   with no memory of ever having tried them — a handful of genuinely
   long-horizon signals (a market closing in 2027, a Fed decision weeks
   out) sorted to the front of every batch forever. Confirmed live: 31,833
   of 32,101 logged signals unresolved, most >24h old. Fixed with a
   `last_checked_at` cooldown column (see `services/signal_log.py`).

2. **The rate-limit model was wrong.** Fixing (1) made `_check_signal_
   resolutions` do real, sustained work for the first time, competing with
   discovery in the same tick. tick_duration/rate_limit_hits spiked to
   ~40s/200+. First fix attempt (a concurrency `Semaphore`) did NOT work —
   confirmed live. Root cause, found by actually reading `docs.kalshi.com/
   getting_started/rate_limits`: Kalshi's limit is a **token-bucket
   throughput system** (tokens/sec), not concurrency-based — a concurrency
   cap does nothing to slow the *rate* new requests fire. Replaced with a
   real token-bucket limiter in `services/http_client.py`, split into
   independent read/write buckets matching Kalshi's own architecture.

Fixing (2) *correctly* then exposed a third, bigger problem: **discovery's
per-refresh `get_candidate_markets` REST fetch (12-120 series depending on
config) now took proportionally longer under a correctly-conservative rate
limit** — the old "fast" behavior had only ever been fast because it was
silently violating Kalshi's real limit. This blocked the whole tick loop
every time a refresh was due, and markets visibly stopped appearing
("grind to a halt," confirmed live: watchlist collapsed to 7 markets).
Direct instruction: *"review the api documentation, implement best and
recommended practices, and do a full audit of the api usage for the
application. no stone unturned. maximum efficiency and maximum speed for
position management and whale watching."*

That audit is what shipped in the second, uncommitted-as-of-this-doc round:

- **Discovery, catalog-scanning, and signal-resolution are now independent
  background tasks** (`_maybe_refresh_discovery_cache`, `_maybe_scan_
  catalog_batch`, `_maybe_check_signal_resolutions` in `main.py`), each
  with its own cadence and an overlap guard (`refreshing`/`scanning`/
  `checking` flags on their respective `state[...]` dicts) — none of them
  can block the fast tick path even occasionally now.
- **`services/market_catalog.py` gained `open_candidates(categories,
  min_volume)`** — a pure SQLite read, no network call. Discovery now
  reads from this instead of calling `get_candidate_markets` fresh every
  refresh. Once the catalog is warm, discovery costs **zero REST calls**.
- **`_scan_catalog_batch` is no longer gated behind `kalshi.
  live_markets_only`** (which is off) — it always runs now, scoped to
  `kalshi.categories` (not all ~9,400 series), keeping the catalog
  continuously warm regardless of that flag. It turned out to already have
  4,160+ series / 18,000+ markets scanned from an earlier period when the
  flag was on — that data was just sitting unused before this fix.
- **`_fetch_account_snapshot` (balance/positions/fills) was making 3
  uncached REST calls every single tick**, unconditionally — the one real
  REST call site the earlier rate-limit work hadn't touched. Now interval-
  cached (`_ACCOUNT_SNAPSHOT_REFRESH_SEC = 20`).

Verified live: markets recovered from the crisis low of 7 to a stable ~32,
zero rate-limit errors throughout. Full test suite (899 tests) passing.

**Known open gap, not yet root-caused**: tick_duration stabilized around
~27s, not back to the pre-incident ~3s baseline. Leading hypothesis (55
uncached open-position tickers needing individual REST hydration) was
directly checked and ruled out — actual open position count at the time
was only 7 (1 paper + 6 real), not 55 (that number was stale, from much
earlier in the same session). No confirmed replacement hypothesis yet.
**First thing to check in a future session**: instrument or log which
specific piece of `_fetch_markets`/the post-gather `_fetch_event_titles`/
`_fetch_live_status`/`_fetch_event_live_data` block is actually consuming
the time — none of the obvious candidates (discovery, catalog-scan,
signal-resolution, account-snapshot) are still inline/uncached, so
whatever's left is something this audit didn't identify. Given zero
rate-limit errors throughout, this is a performance question, not a
correctness or safety one.

## Open item 1: WS-based position/fill streaming — needs a decision

Direct request: *"the open positions should feed from the websocket
stream and analysis trigger api calls for position management."*

Kalshi's WS API (confirmed via `docs.kalshi.com/websockets/websocket-
connection.md`, saved locally at `docs/kalshi/websocket-connection.md`)
supports far more channels than this app currently subscribes to
(`trade`, `ticker` only, in `services/kalshi_trade_ws.py`):
`orderbook_delta`, `fill`, `market_positions`, `market_lifecycle_v2`,
`multivariate_market_lifecycle`, `user_orders`, plus a few others. The
websocket connection is **already authenticated** with the same
`KALSHI_API_KEY_ID`/`KALSHI_PRIVATE_KEY_PATH` credentials
`kalshi_account_client.py` uses for real trading — no new auth mechanism
would be needed to subscribe to `fill`/`market_positions`.

**Why this wasn't built yet**: `fill` events fire only on a real order
fill. `kalshi_account.trading_enabled` is `false` (the standing P0 safety
gate — see `CLAUDE.md`), so no real order can ever be placed, so **no
`fill` message can ever arrive to verify this app's parsing of its real
shape against**. The docs list the channel name and generic subscribe
params, but no message-body schema. Shipping parsing logic for real-
account financial data that can't be verified against a live message felt
like exactly the risk `CLAUDE.md`'s safety posture warns against — a wrong
field name would silently *misreport* real positions, not just crash
loudly and obviously.

Three ways forward, needs a decision (asked directly, not yet answered as
of this doc):

1. **Best-effort parsing now, with a REST reconciliation safety net.**
   Subscribe to `fill`/`market_positions`, parse defensively (`.get()`
   everywhere, never assume a field exists), log the raw message shape the
   first time one arrives (so it can be verified after the fact), and keep
   a slower REST reconciliation poll (e.g. every 2-5 min) running
   regardless, to catch drift or a parsing mismatch. Real risk: could ship
   silently-wrong parsing for a while before anyone notices, if the
   reconciliation poll's own diffing isn't built carefully.
2. **Wait for a real fill to verify against.** Lowest-risk, but blocked on
   either enabling real trading (a much bigger decision than this feature)
   or Kalshi's docs eventually publishing a message-body schema.
3. **Leave it on REST** (now properly interval-cached at 20s, a 15x-ish
   reduction from the uncached every-6s-tick baseline this incident
   found). Simplest, already shipped, doesn't fully satisfy the original
   "feed from the websocket stream" request but isn't actively wasteful
   anymore either.

Whichever direction: `services/kalshi_trade_ws.py`'s `_sync_subscriptions`
currently only subscribes `trade`/`ticker`, scoped per-ticker via
`set_market_tickers()`. `fill`/`market_positions` are account-wide, not
per-ticker — they'd need their own one-time subscription in `run()`'s
initial-connect flow, not the per-ticker desired-set logic.

## Open item 2: services/event_schedule.py — built, not wired

From the *original* profit-maximization work this same day (before the
incident above): a 4-source waterfall (`Event.strike_date` → Kalshi's
milestone API → rules-text regex → keyless DuckDuckGo web search) that
resolves a real event's actual start time, closing a real bug where
multi-day tournament whale signals got skipped as "close time is not
within the trade window" mid-tournament. Every source confirmed against
live Kalshi data (see the module's own docstring for the specifics -
KXPGATOUR-FESJC26's milestone `start_date` matched the real FedEx St. Jude
Championship's actual start, `KXFED-26SEP`'s `strike_date` matched the
real FOMC date, etc.).

**What's missing**: the module is fully built and unit-tested at the
function level, but there is no `tests/test_event_schedule.py` file yet
(a real gap — every other module this size in this codebase has one), and
critically **it is not called anywhere in `main.py`'s tick loop**. `state
["event_schedules"]` is seeded from `event_schedule.load_all()` at
startup and then never touched again. The two integration points that
were planned but never built:

1. A per-tick (or, given everything else in this doc, more likely a
   background-task-decoupled) call to `event_schedule.resolve_one(...)`
   for events in the current watchlist that haven't been resolved yet -
   bounded per cycle (`event_schedule.max_resolutions_per_tick` config
   already exists, unused).
2. `main.py`'s `_handle_signal` needs a third `is_live` check (alongside
   the existing milestone-based and `event_lifecycle.MID_SERIES`-based
   ones): if `event_schedule` has a resolved `start_ts`/`end_ts` for this
   signal's event, treat `now` in `[start_ts - pre_event_hours*3600,
   end_ts]` as live, bypassing `strategy.close_window_sec`'s rejection the
   same way the other two `is_live` sources already do.

Direct scope boundary from when this was planned, still applies: *"leave
the way series are filtered by the market dates alone for now"* — this
only ever affects the `is_live` gate, never discovery/series filtering.

## Open item 3: GET /api/markets/search — decision-market granularity

Direct request, not yet started: manual market search currently returns
every individual outcome market for a multi-outcome event (e.g. all ~51
golfers in a tournament search, separately) instead of the event/series
level. Should return one row per event/series (the "primary" market or a
synthetic series-level row), and "add to list" should pin at that same
granularity — either the series' current full market set, or some
representative subset — rather than requiring one-by-one manual pinning
of every decision market. Affects both search UIs (`static/index.html`'s
Config-tab search and the standalone Markets-tab search, `searchMarkets`/
`searchMarketsTab` and their render functions) since both hit the same
`GET /api/markets/search` endpoint.

## Also still open from before this incident (lower priority, unrelated)

`services/event_lifecycle.py`'s remaining 2 of 4 planned integration
points (from `docs/hardening-and-accuracy-roadmap-2026-08-11.md` Part 1) -
`series_evaluator` shouldn't force-reject a series still in its pre-tail
phase with no mid-series exposure yet, and watchlist eviction should use a
current-activity tie-breaker against stale 24h cumulative volume. Paused
when the rate-limit incident hit this same day and not revisited since.

## Test count

899 passing as of the API-audit round (was 875 before the advisory-
significance-scores commit, 893 after it, 899 after the API audit's own
new `market_catalog` tests).
