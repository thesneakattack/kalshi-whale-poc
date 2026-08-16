# Next steps — 2026-08-15, part 3 (tick_duration root cause + full API audit)

Picks up from `docs/next-steps-2026-08-15-pt2.md`'s open item: "tick_duration
stabilized around ~27s, not back to the pre-incident ~3s baseline...no
confirmed replacement hypothesis yet." This doc covers finding and fixing
that, then a second, broader round: direct instruction to review Kalshi's
full documentation (`docs.kalshi.com/llms.txt`) and audit the API calls,
their parsing, and where "a reapproach might be better" - with helper
functions built to verify findings against the real live API rather than
guess from prose docs alone.

## Part A: tick_duration root cause, found and fixed

Instrumented the tick loop directly (temporary per-phase timing, removed
once done) rather than continuing to guess. Found four independent,
real, uncached/uncapped REST call sites - all shipped:

1. **`propagate_milestone_winners`** called `get_milestones_for_event()`
   for every unique event on the watchlist, every tick, forever,
   unconditionally - most events (crypto, politics, ...) never have a
   milestone at all, so this was pure waste. Fixed with a repoll cache
   (`state["milestone_cache"]`, `_MILESTONE_REPOLL_SEC = 60`) - once a
   winner is found for an event it's cached permanently (a real-world
   outcome doesn't change); until then, repolled at most once/minute
   instead of every tick. Reapplies already-known winners from cache each
   tick so `market_results` stays exactly as complete as before - only the
   network cost was cut.
2. **`_fetch_event_live_data`** called `get_event_live_data()` for every
   unique event, every tick, unconditionally. Confirmed live: **every
   single event on the real watchlist 404s from this endpoint** - turned
   out to be the wrong endpoint entirely for sports (see Part B). Fixed
   with the same repoll-cache shape (`state["event_live_data_cache"]`,
   `_EVENT_LIVE_DATA_REPOLL_SEC = 60`).
3. **`_fetch_live_status`'s `to_poll` had no per-tick batch cap** - when a
   large fraction of the in-window candidate pool (measured up to 2,549
   rows / 100+ distinct events) became simultaneously due for its 5-minute
   repoll, this fired dozens-to-hundreds of concurrent calls in one tick,
   measured up to **48.78s alone**. Fixed with `_LIVE_STATUS_MAX_POLL_PER_TICK
   = 10`, oldest-checked-first (same shape `_check_signal_resolutions`
   already used) - a large backlog now drains gradually across ticks
   instead of spiking once.
4. **Catalog-scan's background batch size (40 series/batch) was the real
   dominant cost** even after fixing 1-3: it runs as an independent
   background task (`_maybe_scan_catalog_batch`), so it never blocks the
   tick's own await chain, but it draws from the *same shared* Kalshi rate
   limiter the tick's own reads need - backgrounding via
   `asyncio.create_task` decouples control flow, not resource contention.
   With the overlap guard meaning a new batch starts again the moment the
   previous one finishes draining, this ran back-to-back continuously,
   confirmed live consuming close to the entire 3.0 tokens/sec read budget
   by itself and starving `_fetch_account_snapshot` (measured stalling to
   19-33s on affected ticks despite its own already-working 20s interval
   cache). Cut to `_CATALOG_SCAN_BATCH_SIZE = 10`, matching signal-
   resolution's own precedent.

**Verified live, repeatedly, with clean before/after measurement**:
tick_duration dropped from a stable ~24-33s plateau to mostly 1-5s, with
occasional spikes to ~14s (residual contention from the now-much-smaller
catalog-scan/signal-resolution background batches) - down from up to
51.14s measured on a single `_fetch_markets` call mid-investigation.
Zero rate-limit errors throughout. 917 tests passing (was 899 - 6 new
tests for the repoll caches, 2 for the batch cap; 10 pre-existing tests
unaffected).

## Part B: full API documentation audit + live verification

Direct instruction: "use llm.txt to review the documentation and then
audit the api calls themselves, the data it's fetching, its parsing, etc,
see where a reapproach might be better" + "write any helper functions for
the audit to eliminate guesswork." The local `docs/kalshi/llms.txt`
snapshot was explicitly partial (session-local); fetched the full current
index fresh and pulled every page relevant to this app's real call sites
(all saved to `docs/kalshi/*.md` - see that directory's `README.md` for
the full list added 2026-08-15).

Built `kalshi_api_audit.py` (scratchpad, not committed - see "why not
committed" below) that uses the app's own real `KalshiClient`/
`KalshiAccountClient` against real credentials to empirically check
documented-but-unverified behavior rather than trust prose alone. Ran it
live. Findings:

### B1. Fixed: wrong base URL

`config/settings.yaml`'s `kalshi.base_url` was
`https://api.elections.kalshi.com/trade-api/v2` - the *legacy* host name.
`docs/kalshi/api_environments.md` confirms **no category-specific hosts
exist at all** (resolves the open question from earlier the same session:
"test out a sports, crypto, mentions, politics, etc endpoints") - the
"elections" name is a backward-compatible alias for the exact same
general-purpose backend, not scoped to election markets. The documented
recommended default is `external-api.kalshi.com`. Direct instruction: "i
insist you use the default api endpoint whenever possible not this
elections one." Switched live via `POST /api/config` (takes effect next
tick, no restart) and in `tests/test_kalshi_trade_ws.py`'s fixture.
Verified live afterward: markets/account/exchange-status all continued
working normally, zero errors, zero rate-limit hits.

### B2. Found but NOT fixed: `get_event_live_data` is the wrong endpoint for sports

`docs/kalshi/get-event-live-data.md` documents this endpoint (event-
ticker-keyed) as serving "crypto price charts, commodity price
timeseries, weather observations" - confirmed live: called against 3 real
crypto tickers (KXBTC15M-*), it returned real BTC candlestick data. Called
against sports tickers, it 404s for 100% of them - not a bug, just
structurally the wrong data source. The right source for sports is the
*milestone*-keyed `get_live_data`/`get_live_datas`
(`docs/kalshi/get-live-data.md`) - which `_fetch_live_status` and
`propagate_milestone_winners` **already call**, but only ever extract
`details.widget_status`/`details.winner` from. The full real payload
(verified live against a real NFL milestone) includes:

```json
{
  "away_points": 24, "home_points": 20, "clock": "00:00", "quarter": 4,
  "status": "closed", "widget_status": "finished", "winner": "",
  "last_play": {"description": "End Game", "occurence_ts": 1786835667},
  "situation": {"down": 3, "goal_to_go": false, "yardline": 2, "yfd": 2}
}
```

Real score, quarter, clock, down/distance, last play - all already being
fetched, all currently discarded. Surfacing this (e.g. a new
`state["live_game_state"]`) would be pure value-add at zero extra API
cost. Whether `_fetch_event_live_data`/`get_event_live_data` is worth
keeping at all for the Crypto category (vs. relying on ordinary market
bid/ask, which the app already has) is a separate, smaller question.

### B3. Found but NOT fixed: three real batching opportunities, all live-verified

- **`get_events(tickers="A,B,C,...")`** (`docs/kalshi/get-events.md`)
  replaces N individual `get_event()` calls with 1. Verified: 3
  individual calls = 0.36s wall; 1 batched call = 0.02s wall, all 3
  events returned correctly. `_fetch_event_titles` currently does the
  N-individual-calls version.
- **`get_live_datas(milestone_ids=[...])`** (up to 100 per call,
  `docs/kalshi/get-live-data.md`) replaces N individual `get_live_data()`
  calls with 1. Verified: 3 individual = 0.99s wall; 1 batched = 0.02s
  wall. `_fetch_live_status` and `propagate_milestone_winners` both
  currently do the N-individual-calls version.
- **`get_milestones(category=X, min_updated_ts=Y)`** with *no* per-event
  filter (`docs/kalshi/get-milestones.md`) - verified live: one call
  (`category="Sports"`, updated in the last 6h) returned 200 milestones
  covering **1,483 distinct `related_event_tickers`**. This is a
  genuinely different strategy from the current per-event
  `get_milestones_for_event(et)` pattern (which can't itself be batched -
  `related_event_ticker` only accepts one value): a periodic bulk sync
  into a local `event_ticker -> milestone` map would let
  `_fetch_live_status`/`propagate_milestone_winners` read locally (zero
  API cost per event) instead of each doing its own per-event round trip.
  Bigger architectural change than the other two - not attempted this
  session.

None of these three are implemented. All were deliberately left as
findings rather than applied blind, given this exact code path
(milestone/live-data fetching in the tick loop) was already substantially
rewritten once earlier the same day for the rate-limit incident - a
second unreviewed rewrite of the same area in one session felt like
exactly the kind of compounding risk worth a pause for direction, not a
unilateral call.

### B4. Found: the rate limiter is far more conservative than the real, confirmed account limits justify

`docs/kalshi/rate_limits.md` has the full detail. Summary: called
`get_account_api_limits()` and `get_account_endpoint_costs()` live
against this app's own real connected account (not assumed from generic
docs):

```json
{"usage_tier": "basic", "read": {"refill_rate": 200, "bucket_capacity": 600},
 "write": {"refill_rate": 100, "bucket_capacity": 100}, "grants": []}
```

Default cost is 10 tokens/request; the real non-default-cost endpoint
list (12 entries, all fetched live) contains **none** of the endpoints
this app calls - every one of them (markets, market, event, events,
milestones, live_data, live_datas, event_live_data, trades, candlesticks,
exchange_status, balance, positions, fills, orders, create/cancel_order,
search endpoints) costs the flat default.

Real confirmed sustainable rate: 200 ÷ 10 = **20 read-requests/sec**, with
a 600-token (~3 second) burst pool - not the 1-second-max framing the
generic tier table implies for Basic reads; account-specific numbers
override the generic prose. `services/http_client.py`'s limiter is
currently `_KALSHI_READ_RATE_PER_SEC = 3.0`, burst `2.0` - about 15% of
the real sustained budget and a burst pool ~300x smaller than what's
actually available.

The earlier same-day empirical result ("6/sec and 8/sec both caused
429s") was most likely an artifact of the four uncapped/uncached bugs
fixed in Part A above - each one created bursts far larger than its
nominal configured rate implied - rather than proof that 6-8 req/sec
itself exceeds Kalshi's real limit (60-80 tokens/sec is well under the
real 200/sec ceiling). Raising the limiter meaningfully is a live,
evidence-backed candidate, not applied this session - it's the one
finding here that most directly touches the exact code that caused this
session's earlier incident, so it's flagged for an explicit decision
rather than re-tuned unilaterally a second time in one day.

### B5. Found, informational only: `event.category` deprecation, unused Market fields

- `docs/kalshi/get-event.md` marks `EventData.category` "deprecated."
  Live-verified it's still populated correctly right now (`'Sports'`,
  matching the series-level category from `get_series_list`) - not an
  active bug, just a forward-compatibility risk worth knowing about if
  Kalshi ever stops populating it.
- A real `get_market()` response has 53 fields; `_MARKET_FIELDS`/
  `_slim_market` keeps 9 (deliberately, to cut `/api/state`'s payload
  size - see that constant's own comment). Of the 44 dropped fields, the
  more plausibly useful ones going forward: `rules_primary`/
  `rules_secondary` (full rules text), `open_interest_fp`,
  `liquidity_dollars`, `no_bid_dollars`/`no_ask_dollars`,
  `previous_yes_bid_dollars`/`previous_yes_ask_dollars`/
  `previous_price_dollars`. Not a bug - already fetched, already
  discarded on purpose - just noted in case any of it becomes useful
  later (e.g. `previous_*` fields for a "how much did this just move"
  signal).

## Why the audit script isn't committed

`kalshi_api_audit.py` (and its follow-up `kalshi_api_audit2.py`, mainly
fixing a `.env`-loading gap when run via stdin-piping rather than as
`main.py`) live in the session scratchpad, not the repo - they're one-off
diagnostic tools that make real, if read-only, calls against the live
account, not something that should run automatically or live in the
watched project tree (saving a `.py` file under the repo root triggers
`uvicorn --reload`, confirmed live when a routine test-file edit this
same session caused an unplanned restart mid-verification). If repeat use
turns out to be valuable, promoting a trimmed version into `scripts/` (a
directory that doesn't exist yet) would be the right move - not done
speculatively here.

## Test count

917 passing (899 at the start of this doc, +18 across the tick_duration
fix's new caching/batching tests).
