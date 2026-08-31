# Kalshi API cheat sheet — known answers, checked before re-deriving

Living, append-only reference of specific "what field/endpoint answers
this" questions already resolved by actually reading `docs/kalshi/`, kept
here so they don't get re-derived (or re-guessed) from scratch next
session. **Check this file first** for anything it already covers before
grepping the full 215-page mirror cold — and add a new entry any time a
`docs/kalshi/` page resolves a real data question, especially one that
took a wrong guess to get to. See `CLAUDE.md`'s "Kalshi API documentation"
section for the full standing rule this file backs.

### Entry format

Each real entry below is an `##` heading (a short question), so
`.claude/hooks/orient.sh` can list titles at session start via
`grep '^## '` — keep this section itself at `###` so it doesn't get
matched as an entry. Each entry then has `**Answer:**`, `**Source:**`,
`**Gotcha:**` (if there is one), `**Found:**` (date + one-line context).

---

## What sport/league does an event belong to? ("subcategory" grouping)
**Answer:** `event.competition` (e.g. `"Pro Baseball"`) is the real
per-event field — confirmed on a live event object and documented as
`competition (string, nullable)` on the event-metadata response. For the
coarser SPORT grouping (e.g. `"Baseball"`), reverse-map through
`filters_by_sports`: `{sport: {scopes, competitions: {competition:
{scopes}}}}` — several competitions nest under one sport (`"Baseball"` →
`"Pro Baseball"`, `"Japan NPB"`, `"Korea KBO"`, `"Mexico LMB"`).
**Source:** `get-event-metadata.md` (`competition` field),
`get-filters-for-sports.md` (`filters_by_sports` sport→competition shape).
**Gotcha:** `category_tags` on an event object LOOKS like per-event tag
data but isn't — it's the identical full facet-filter vocabulary listed on
every event in a category (every Sports event lists all ~20 sports),
carrying zero per-event signal. Confirmed live, not derivable from that
field no matter how it's parsed.
**Found:** 2026-08-16, building whale-confidence subcategory segmentation
(`services/trade_category.py`, `main.py`'s `_sport_for_event`).

---

## Can a market's `close_time`/status change after it's already been read?
**Answer:** Yes. `close_time` can move (`close_date_updated` event) when a
market is closed ahead of its scheduled close time, including before
determination. Full real lifecycle: `active` ↔ `inactive`
(`deactivated`/`activated`) → `closed` → `determined` (result set) →
`finalized` (paid out, via `settled`) — `amended`/`disputed` also possible.
A `market_lifecycle_v2` WS event fires for every one of these transitions
(`created`, `activated`, `deactivated`, `close_date_updated`, `determined`,
`settled`, `metadata_updated`).
**Source:** `market_lifecycle.md` (full status table + WS event list).
**Gotcha:** A locally-cached/scanned row (e.g. `market_catalog.db`) can
keep showing a stale `close_time`/`status` indefinitely until its next
scan — a `close_ts > now` filter alone can't catch this, since it's
trusting the same stale column that's wrong. Any status past `active`
(`closed`/`determined`/`disputed`/`amended`/`finalized`) means the
catalog's belief about that market is no longer trustworthy regardless of
what `close_ts` says.
**Found:** 2026-08-16 investigation (see `main.py`'s
`_DISCOVERY_TERMINAL_STATUSES`), first surfaced by a real finalized MLB
market whose catalog row still showed `status=active` 24 minutes after
Kalshi's own API had moved it to `finalized`.

## Why don't whale trades on most markets ever reach the app?
**Answer:** Because the `trade` websocket channel is subscribed with an
explicit `market_tickers` list (the discovery watchlist), so trades on every
other market are never delivered at all — not filtered, not logged, never
received. `public-trades.md` states market specification is **optional** on
this channel: subscribing without `market_tickers` streams the entire
exchange's trades. Measured 2026-08-17T00:37Z: 425 distinct markets traded
in a 30-second window, only 8 were in the 15-market watchlist, and 5 of 5
whale prints >=$2,500 were invisible (100% miss, ~1,000 qualifying prints/hr
exchange-wide at ~7,400 trades/min).
**Source:** `public-trades.md` ("market specification optional"),
`websocket-connection.md` (`update_subscription` + `add_markets`/
`delete_markets` actions; error list includes "subscription market limit
exceeded" — a real cap exists but the mirror does not state its number, so
raise subscription counts incrementally and watch for that error).
**Gotcha:** Going exchange-wide breaks `kalshi_trade_tape.py`'s
`markets_by_ticker.get(ticker)` lookup — it `continue`s on any unknown
ticker (silently, with no `candidate_log` rejection row), so full coverage
needs an on-demand market fetch or a degraded-confidence path, not just the
subscription change.
**Found:** 2026-08-17, user report: "on kalshi ill see at least 20 whale
worthy trades that should appear prior to decision making that dont appear
at all."

## Is a trade's "size" its contract count or its dollar value?
**Answer:** They differ by up to 100x and picking wrong silently biases every
notional gate. `count_fp` is contracts; the dollars the taker actually paid
is `count_fp * <taker's side>_price_dollars` (what
`kalshi_trade_tape._notional_usd` computes). Kalshi's own `dollar_volume`
(market-ticker channel) follows the same one-side convention — the example
shows `volume_fp 33896` / `dollar_volume 16948`, i.e. count x ~average
price, not count x $1.00 collateral.
**Gotcha:** A fixed *dollar* gate is structurally biased toward near-certain
trades: at $0.99/contract, $2,500 is only 2,525 contracts (max gain 1c),
while at $0.30 it takes 8,333 (max gain 70c). Measured 2026-08-17: 33 trades
in a 30s window had >=2,500 contracts but only 9 cleared $2,500 taker-paid.
A contract-count floor, or a gate scoped to a price band, does not have this
bias.
**Source:** `public-trades.md` (trade fields), `market-ticker.md`
(`dollar_volume` vs `volume_fp`).
**Found:** 2026-08-17, investigating "signals not coming in at all when they
should, like over 2500 dollar positions."

## Which field gives a trade's direction? (`taker_side` is deprecated)
**Answer:** `taker_outcome_side` ('yes'/'no') is canonical; `taker_book_side`
carries the same bit in book vocabulary ('bid' == yes, 'ask' == no).
`taker_side` is **deprecated** — the docs say "will not be removed before
May 14, 2026", a guarantee that has already expired. Read outcome →ptbook →
legacy, in that order.
**Gotcha:** the old code read only `taker_side` and defaulted anything
unreadable to `"no"`, which would silently give every signal the wrong
direction AND the wrong notional (`no_price` instead of `yes_price`) the day
Kalshi drops the field. Return None and skip instead — for a system whose
whole output is a directional call, guessing a side is worse than skipping
the trade. Live-verified 2026-08-17: all 500 sampled trades carry all three
fields and canonical resolution agrees with legacy 500/500, so migrating is
free right now.
**Source:** `get-trades.md`, `get-historical-trades.md` (field descriptions),
`public-trades.md` (the WS message carries the same fields).

## Documented market fields this app does not read
**Answer (2026-08-17 audit, `get-market.md`):** `latest_expiration_time`,
`settlement_timer_seconds`, `early_close_condition`, `expiration_value`,
`fee_waiver_expiration_time` — all zero references in the codebase.
**Why each matters:** `settlement_timer_seconds` is the gap between close and
settlement, i.e. when a position actually realises; `early_close_condition`
describes *when* a `can_close_early` market closes, while the app applies a
blanket grace period to all of them; `fee_waiver_expiration_time` means a
fee-inclusive P&L (which drives stop-loss/take-profit triggering) can
overstate cost on a waived market.
**Also:** `fee_rounding.md` defines net fee as **trade fee + rounding fee −
rebate**. `services/kalshi_fees.py` models only the trade fee (correctly, incl.
the ceil-to-$0.0001), so its output is a lower bound, not the net fee.

## Which `ticker`-channel fields does the app keep, and which did it drop?

`market-ticker.md` documents 15 fields on a `ticker` websocket message.
`main.py::_process_stream_ticker` reads exactly two: `yes_bid_dollars` (into
`state["latest_prices"]`) and `yes_ask_dollars` (onto the matching
`state["markets"]` row). The other thirteen were discarded on arrival and,
because `state` is in-memory only, were unrecoverable afterwards:

- `yes_bid_size_fp` / `yes_ask_size_fp` — resting depth. The only way to ask
  whether the book could actually have filled an entry at the quoted price,
  or whether the rest was paid up for.
- `open_interest_fp` / `dollar_open_interest` — market size, i.e. the
  denominator for any "how big was this print relative to the market"
  question (the 2026-08-17 volume-impact hypothesis could not be tested
  against real book state for exactly this reason).
- `volume_fp` / `dollar_volume` — cumulative traded size.
- `last_trade_size_fp` — size of the print that moved the quote.
- `ts` / `ts_ms` / `time` — exchange-side timestamps. Without these, every
  latency figure in the app is receive-time, not exchange-time, and so
  includes this app's own queueing.
- `market_id` — the UUID form of the identifier, alongside the ticker.
- `price_dollars` — last trade price (kept only as a fallback when
  `yes_bid_dollars` is absent).

`services/series_watcher.py` now persists all of them plus the whole raw
message as `raw_json`, for the series listed in `series_watcher.series`.
Same for trades: the provider reduces a print to a side and a notional, so
`raw_trades` keeps the full payload and all three direction fields
(`taker_outcome_side`, `taker_book_side`, the deprecated `taker_side`)
separately rather than only the resolved answer.

## How do you subscribe to the WHOLE exchange, not just a watchlist?

Confirmed 2026-08-17 against the mirror, for the "realtime data across
everything" goal — this is the fix for the ~98% coverage loss (the trade WS
is currently subscribed with an explicit `market_tickers` list, so a print
on any unwatched market is never received at all — not filtered, not
logged, not counted as rejected).

- **`public-trades.md`** (the `trade` channel) states **"market
  specification optional"** in its own requirements list. Subscribing with
  `{"channels": ["trade"]}` and NO `market_tickers` streams every trade on
  the exchange. `market-ticker.md` says the same for the `ticker` channel
  (but exchange-wide ticker is a genuine firehose and this app only needs
  prices for markets it might actually trade — keep that one scoped).
- **Sharding, for when one connection can't keep up**
  (`websocket-connection.md`'s optional params, and `changelog-index.md`
  for the semantics): `shard_factor` (1–100, must be > 0) and `shard_key`
  (`0 <= key < shard_factor`). *"Messages are sharded by `market_ticker`
  using consistent hashing. Clients can run multiple connections with
  different `shard_key` values to distribute load while ensuring complete
  coverage."* The changelog documents these for the `communications`
  channel specifically; `websocket-connection.md` lists them among the
  general subscribe params. Verify live before assuming `trade` honours
  them.
- **`update_subscription` actions** are `add_markets` / `delete_markets` /
  `get_snapshot` — there is no documented "switch to exchange-wide" action,
  so going exchange-wide means a fresh `subscribe` without
  `market_tickers`, not an update to the existing sid.

**Blocker to fix first, not an API question:**
`services/whalewatchers/kalshi_trade_tape.py::_process_trades_sync` does
`continue` when a print's ticker is absent from `markets_by_ticker` (the
watchlist-derived dict). Exchange-wide flow is overwhelmingly unknown
tickers, so subscribing without that path handled would raise the message
volume enormously while producing the same signals. Needs either an
on-demand market fetch or a degraded-confidence path that scores on the
print alone.

Other channels this app does not use yet, both relevant to "realtime
everything": `market_lifecycle_v2` (realtime open/close/settlement, versus
today's REST polling — directly relevant to the stale-`close_time` bug
class) and `orderbook_delta` (real depth, versus the sampled top-of-book
snapshots `services/series_watcher.py` now records).

## Is `docs/kalshi/`'s `FeeType` enum (`quadratic`/`quadratic_with_maker_fees`/`flat`) exhaustive?
**Answer:** No — live Kalshi data already returns a fourth value,
`quadratic_with_combo_maker_fees`, on at least 3 real series (e.g.
`KXMVECROSSCATEGORY`, category `Exotics`, combo/multivariate markets). This
isn't a stale-mirror problem: neither `docs/kalshi/` (fetched 2026-08-16)
nor the *latest published* `kalshi-python-async` SDK release (3.28.0,
checked directly — same 3-value enum as the pinned 3.27.0) document it
either. Kalshi's live API is ahead of both its own docs and its own SDK
here, not just this app's copy of either.
**Gotcha:** the SDK's `get_series_list` deserializes the full ~13,300-series
response into typed Pydantic `Series` objects internally — one series with
an enum value the installed `FeeType` doesn't recognise raises and fails
the **entire** call, every time, for every series, not just the combo ones.
Bumping the SDK version will not fix this (3.28.0 has the identical enum).
**Fix:** `services/kalshi_client.py::get_series_list` now fetches `/series`
raw via `_get_json` (bypassing the SDK's typed client entirely for this one
call) instead of `self._client.get_series_list`, so an unrecognised
`fee_type` is just a string in a dict, not a validation failure.
**Source:** installed vs. downloaded `kalshi_python_async/models/fee_type.py`
(3.27.0 and 3.28.0 wheels, both missing the value), `get-series-list.md`
(same 3-value enum documented).
**Found:** 2026-08-21, `fastapi` logs spamming `[market_catalog] background
scan batch failed entirely` every cycle.

---

## Is a market's `no_sub_title` always a real, informative label?
**Answer:** No — for the entire `KXBTC15M` series, live `no_sub_title` is
the literal placeholder string `"Target price: TBD"` while `yes_sub_title`
on the same market carries the real number (`"Target Price: $77,220.13"`).
Confirmed directly against a real running app's `/api/state` `market_titles`
cache, not assumed: 11/45 cached tickers had a `"tbd"`-containing
`no_sub_title`, every single one a `KXBTC15M-*` ticker. `docs/kalshi/
get-market.md` only lists `yes_sub_title`/`no_sub_title` as field names with
no semantic guidance — this is a live data quirk on Kalshi's side for this
market type, not a mirror gap.
**Gotcha:** this is a *different* shape from the already-known duplicate-
title quirk (`no_sub_title === yes_sub_title`, "subcategory" entry's
sibling case in `frontend/src/js/shared-utils.js`'s `marketContext()`) —
the two values here are different strings, just one of them is
uninformative. A `degenerate` check that only compares the two strings for
equality misses this case.
**Fix:** `marketContext()`'s `degenerate` check widened to also match
`/\btbd\b/i` against `no_sub_title`, reusing the same `"not {yes_sub_title}"`
fallback already used for the duplicate-title case.
**Found:** 2026-08-24, direct report ("no positions" section "very buggy" -
meant side=no positions specifically) after the dollar math itself
(cost_basis/mark_to_market/sideAdjustedPrice) was traced end to end against
live data and confirmed correct.

## Is `expected_expiration_time` the same field name on REST and the WS lifecycle stream?
**Answer:** No — two different names for the same concept in two different
places. The REST market object (`get-markets.md`/`get-market.md`/
`get-historical-market(s).md`) uses `expected_expiration_time`, an ISO-8601
string. The `market_lifecycle_v2` WS channel's `created`/`updated` event
payloads (`market-and-event-lifecycle.md`, `multivariate-market-and-event-
lifecycle.md`) use `expected_expiration_ts`, a unix-epoch integer — plus, on
`close_date_updated` events specifically, a live-updated `close_ts` (also
epoch integer) when Kalshi actually changes a market's close time.
**Source:** `docs/kalshi/market_lifecycle.md:62` (REST field, prose table);
`docs/kalshi/market-and-event-lifecycle.md:288,540,584` (WS field, schema +
example payload, `"expected_expiration_ts": 1694721600`); confirmed live
2026-08-24 in `ddev logs -s fastapi` — a real `market_lifecycle_v2` `created`
event for `KXATPGSPREAD-26AUG24NARCIN-CIN6` carried
`'expected_expiration_ts': 1787680800` inside `additional_metadata`.
**Gotcha:** `services/market_lookup.py`'s `effective_close_time()` (2026-08-24
close-time fix) reads the REST field only (`services/market_watch/
market_fetch.py`'s `_MARKET_FIELDS`) — this app does not currently consume
`kalshi_trade_ws.py`'s `market_lifecycle_v2` stream for this purpose at all
(that handler only logs "first real shape" samples for now, see
`services/kalshi_trade_ws.py`). If a future session wires the WS stream in as
a live-updating trigger (e.g. to invalidate `state["event_schedules"]`/
`market_object_cache` the instant `close_date_updated`/`expected_expiration_ts`
changes, instead of waiting for the next REST poll), use `expected_expiration_ts`
there, not `expected_expiration_time` — they are not interchangeable names for
the same wire field.
**Found:** 2026-08-24, second sub-unit of the close-time fix (`services/
market_events/event_schedule.py`'s background resolver) — noticed live in
`fastapi` logs while verifying the resolver was running, not assumed from
the REST docs alone.

## What's the real identity field on a WS fill message — `fill_id` or `trade_id`?
**Answer:** `trade_id` — the WS `user-fills.md` message has **no `fill_id`
field at all**, only `trade_id` ("Unique identifier for fills. This is
what you use to differentiate fills"). `fill_id` only exists on the REST
`GetFills` `Fill` schema (`get-fills.md`), which documents `trade_id`
there too as "same as fill_id" — so `trade_id` is present and
equal-valued on both surfaces, making it the correct shared identity key,
not `fill_id`.
**Gotcha:** `services/whale_stream/whale_stream_handlers.py`'s
`_process_stream_fill` was keyed on `fill_id` (and
`services/account_positions.py`'s `_FILL_FIELDS` didn't even keep
`trade_id` through the slim step) — `fill.get("fill_id")` was always
`None` for a real WS message, so every real fill silently no-opped, never
observed because `kalshi_account.trading_enabled` has always been `false`
(CLAUDE.md's P0 safety gate), so no real fill has ever occurred to reveal
this live. Same root shape as the already-known `taker_side`/
`market_position(s)` classes below.
**Source:** `get-fills.md` (REST `Fill` schema, both `fill_id` and
`trade_id` required, description text quoted above), `user-fills.md` (WS
schema's own required-field list — `trade_id`/`order_id`/... — has no
`fill_id`).
**Fix:** `trade_id` added to `_FILL_FIELDS`; `_process_stream_fill`'s
identity/dedup checks switched from `fill_id` to `trade_id`.
**Found:** 2026-08-24, building `tests/test_kalshi_contracts.py` (QCP
Task 13) — reading `user-fills.md` before writing the fixture, per this
file's own standing rule, rather than trusting the existing code's field
choice.

## Is the real per-message `type` for a position update `market_position` or `market_positions`?
**Answer:** `market_position` — **singular**. The WS `market-positions.md`
schema's own `type` field is `const: market_position`. The *subscription
channel name* (what you pass to `channels` when subscribing, and the key
`_subscription_sids` is keyed by) is `market_positions`, **plural** — a
different string for a different purpose, easy to conflate.
**Gotcha:** `services/kalshi_trade_ws.py`'s `_handle_message` dispatched
on `msg_type == "market_positions"` (the plural channel name) instead of
the real singular per-message type — so `on_position`
(`_process_stream_position`) was **never invoked at all** for any real
position update, not even a no-op reach-and-skip like the fill bug above.
The pre-existing test in `tests/test_kalshi_trade_ws.py` had encoded this
same wrong assumption as its own fixture input, so it passed for the
wrong reason rather than catching the bug.
**Source:** `market-positions.md` (WS schema `type: {const:
market_position}`, and the doc's own worked `example` block:
`"type": "market_position"`).
**Fix:** dispatch check corrected to `"market_position"`; the pre-existing
test's fixture corrected to match, plus a new regression test proving the
plural string no longer dispatches.
**Found:** 2026-08-24, same session as the `fill_id`/`trade_id` finding
above — the plan document for this task's own initiative
(`docs/superpowers/plans/2026-08-24-quality-control-plane.md`) named
`type: "market_position" singular` as a required fixture-encoding target,
which is what prompted re-checking this dispatch against the real docs
instead of assuming the existing code already had it right.

## Does a WS `market_position` message use `ticker` or `market_ticker`?
**Answer:** `market_ticker` — the WS `market-positions.md` schema has no
`ticker` field at all. The REST `GetPositions` `MarketPosition` schema
(`get-positions.md`) is the one that uses `ticker` — a genuine REST-vs-WS
naming split, same shape as `expected_expiration_time`/
`expected_expiration_ts` above.
**Gotcha:** `services/account_positions.py`'s `_POSITION_FIELDS` only
listed `ticker`, so `_slim_position` discarded a real WS position
update's only identifier before `_process_stream_position` ever saw it —
`position.get("ticker")` was always `None`, so the handler no-opped on
every message (compounding the dispatch bug above, which meant it was
never even reached in the first place).
**Source:** `market-positions.md` (WS schema, `market_ticker` required,
no `ticker` property), `get-positions.md` (REST schema, `ticker`
required, no `market_ticker` property).
**Fix:** `market_ticker` added to `_POSITION_FIELDS`;
`_process_stream_position` now reads `ticker` with a `market_ticker`
fallback, then normalizes the resolved value back onto `position["ticker"]`
so every downstream consumer (frontend, `_join_real_position_prices`,
`_real_account_position_tickers`) keeps reading the one key it already
expects regardless of source.
**Found:** 2026-08-24, same session as the two findings above.

---

## Is `services/kalshi_client.py`'s `get_live_data` on the current or legacy milestone-live-data endpoint?
**Answer:** Legacy. `get-live-data-with-type.md` (`GET
/live_data/{type}/milestone/{milestone_id}`, what the SDK's
`get_live_data(type=..., milestone_id=...)` calls) is documented as: "This
is the legacy endpoint that requires a type path parameter. Prefer using
`/live_data/milestone/{milestone_id}` instead." That preferred endpoint is
a *third*, separate page (`live-data-get-live-data.md`'s source,
`api-reference/live-data/get-live-data.md` — not `get-live-data-with-type`
or `get-multiple-live-data`, and not the same shape as either: no `type`
path param at all). Not migrated as part of this pass — a genuine
docs/live contract discrepancy recorded here per the boundary design
spec's "treat docs/live disagreement as an explicit contract discrepancy
rather than guessing," not silently fixed as an incidental refactor.
`get_live_datas` (the batched form, `get-multiple-live-data.md`) is not
flagged as legacy by its own doc page — only the single-item form is.
**Source:** `get-live-data-with-type.md`, `get-multiple-live-data.md`,
`live-data-get-live-data.md`, `llms.txt` (lines naming
`get-live-data-with-type.md` "the legacy endpoint").
**Found:** 2026-08-24, Kalshi Integration Phase A Task A1, while replacing
the old merged/curated `get-live-data.md` with verbatim per-source mirrors
(`docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md`).

---

## What does this app's real connected account's rate-limit tier actually look like, and is the current limiter tuned to it?
**Answer:** Real 2026-08-15 live values for this account (`GET
/account/api_limits`): `usage_tier: "basic"`, read `{refill_rate: 200,
bucket_capacity: 600}`, write `{refill_rate: 100, bucket_capacity: 100}` —
a 600-token (~3s) real read burst pool, larger than `rate_limits.md`'s own
generic one-second-of-budget framing for Basic-tier reads suggested (the
real page, re-verified 2026-08-24, actually documents Basic/Advanced as a
**two**-second burst bucket — see its own "Bucket capacity and bursting"
section — so the account data and the doc agree once the doc is read
precisely; the discrepancy was against an earlier, less careful reading,
not the doc itself). `GET /account/endpoint_costs` for this account: every
endpoint this app actually calls (markets, market, event, events,
series_list, milestones, live_data, live_datas, event_live_data, trades,
candlesticks, exchange_status, tags/filters search, balance, positions,
fills, orders, create_order, cancel_order) costs the flat default of 10
tokens — none of the non-default-cost endpoints listed in
`list-non-default-endpoint-costs.md` apply to this app.
**Gotcha:** `services/http_client.py`'s limiter
(`_KALSHI_READ_RATE_PER_SEC = 3.0`, burst 2.0) uses roughly 15% of this
account's real sustained read budget (200 ÷ 10 = 20 req/sec) and a burst
pool roughly 300x smaller than the real 600-token pool. Confirmed
2026-08-15 that the same-day 429s motivating the conservative tuning were
more likely caused by several now-fixed uncapped/uncached call sites
producing outsized real bursts than by the nominal rate itself being
unsafe. Raising the limiter toward the real ceiling (with a safety margin)
is a live, evidence-backed candidate — flagged here, not applied
automatically, since it touches the same code that caused that incident.
**Source:** `rate_limits.md`, `list-non-default-endpoint-costs.md`, this
account's own live `get_account_api_limits()`/`get_account_endpoint_costs()`
responses.
**Found:** 2026-08-15 (original live-account verification); moved here
from inside `docs/kalshi/rate_limits.md`'s own mirrored body 2026-08-24,
Kalshi Integration Phase A Task A1, per the boundary design spec's "keep
application commentary in CHEATSHEET rather than inside the mirrored
body" — the doc mirror itself is verbatim upstream prose now, not a place
for this app's own account-specific numbers.

---

## Does this app's WebSocket client follow Kalshi's documented ping/pong guidance?
**Answer:** Yes, already compliant — confirmed, not assumed.
`quick_start_websockets.md`'s own "Connection Keep-Alive" section: "The
Python `websockets` library automatically handles WebSocket ping/pong
frames to keep connections alive. No manual heartbeat handling is
required... Other WebSocket libraries may require manual ping/pong
implementation." `services/kalshi_trade_ws.py` uses the `websockets`
library's own `connect(..., ping_interval=20, ping_timeout=20)` and does
no manual ping/pong frame handling of its own — exactly the documented
recommended pattern. No client-side interval/timeout values are documented
upstream (just "the library handles it automatically"), so 20s/20s is
this app's own choice within the library's supported knobs.
**Correction (2026-08-25, I9):** `connection-keep-alive.md` — a page not
read when this entry was written — documents the *server* side: "Kalshi
sends Ping frames (0x9) every 10 seconds with body `heartbeat`"; clients
must answer with Pong. And the `websockets` 17.x client's own keepalive
closes the socket with 1011 "keepalive ping timeout" when *its* pong wait
(`ping_timeout`) expires — which is exactly what an event-loop stall
≥ 20 s produces (observed live in I7). So the 20 s knobs are also a
loop-hygiene deadline, not merely a network setting.
**Source:** `quick_start_websockets.md`, `connection-keep-alive.md`.
**Found:** 2026-08-24, Kalshi Integration Phase A Task A1, verifying
`services/kalshi_trade_ws.py` against documented suggested WS practices.

## On a REST Fill, is `market_ticker` the real name or an alias? (and `ticker` on WS?)

Resolved 2026-08-25 (Phase A Task A14), from `docs/kalshi/get-fills.md`'s Fill schema:
the REST Fill object REQUIRES **both** `ticker` and `market_ticker`, and documents
`market_ticker` as "legacy field name, same as ticker" — mirroring the same schema's
`trade_id` being "legacy field name, same as fill_id" on REST. The WS user-fills message
(`docs/kalshi/user-fills.md`) is the inverse world: it carries `market_ticker` (and
`trade_id`) only. Net: every fill this app stores now carries the canonical `ticker` —
REST natively, WS via `services/kalshi/contracts/fill.py`'s gateway normalization — so
presentation code (`services/state_view.py`) reads `ticker` alone, with no
alias fallback. Don't reintroduce per-consumer `or market_ticker` fallbacks; the alias
knowledge lives at the boundary.

## Can `GET /markets/trades` fetch a bounded exchange-time window, and how big can a page be?
**Answer:** Yes. `get-trades.md` documents `min_ts` **and** `max_ts` (both
"Unix timestamp", `integer/int64` seconds — "after"/"before", inclusivity
unstated, so enforce the window locally on each Trade's own `created_time`
too), `limit` 1–1000 (default 100 — the SDK default this app used, 25, is
its own choice), and cursor paging where an **empty** cursor means no more
pages. Omitting `ticker` returns "all trades for all markets". Each REST
`Trade` carries `trade_id`, `ticker`, `count_fp`, ISO `created_time`; the
WS `trade` message carries the same `trade_id` plus `ts_ms` — so REST and
WS records of one print share an identity key and a comparable clock
(`services/kalshi/contracts/trade.py::trade_exchange_ts`).
**Gotcha:** `services/kalshi/public.py`'s `get_trades` only passed `min_ts`
until 2026-08-25 (I4); the installed SDK 3.27.0 `MarketApi.get_trades`
accepts `max_ts` (and `is_block_trade`) — verified by introspection, not
assumed — so the passthrough is a one-line addition, not a raw-HTTP bypass.
**Source:** `get-trades.md` (`MinTsQuery`/`MaxTsQuery`/`MarketLimitQuery`/
`CursorQuery`, `GetTradesResponse`, `Trade` schema), `pagination.md`,
`public-trades.md` (WS field list).
**Found:** 2026-08-25, realtime data-plane task I4 (REST-vs-WS capture
reconciliation, `services/diagnostics/trade_capture_reconciliation.py`).

## Is a `GET /markets?tickers=…` request billed per ticker, and what does this account's budget really allow?
**Answer:** No per-ticker billing. `rate_limits.md`'s "Batch endpoints don't
save tokens ... every item in the batch is billed separately" is written for
the batch **order** endpoints (`25 orders = 25 × 10 tokens`); nothing in the
mirror says a list filter is billed per item, and this account's own
`GET /account/endpoint_costs` (`list-non-default-endpoint-costs.md`) reports
`default_cost` 10 with none of the app's endpoints priced differently.
Measured 2026-08-25 (I8 probe): 50-, 100- and 200-ticker requests each
returned complete in one request (41 / 73 / 106 ms, no 429). This account
(`GET /account/limits`, `get-account-api-limits.md`): tier `basic`, read
200 tokens/s with a 600-token capacity → **20 req/s sustained, 60-request
burst**; write 100/100.
**Gotcha:** `services/kalshi/public.py`'s 50-per-chunk `get_markets_by_tickers`
sizing was justified as "500 tokens under the 600 ceiling" — that arithmetic
assumes per-item billing the docs don't state for this endpoint. The chunk is
now an explicit `batch_size` parameter (default unchanged) pending the REST
solution comparison (I11); don't re-derive the 500-token reasoning.
**Source:** `rate_limits.md` ("Batch endpoints don't save tokens"),
`list-non-default-endpoint-costs.md`, `get-account-api-limits.md`,
`get-markets.md` (`TickersQuery`: "Comma-separated list", no cap).
**Found:** 2026-08-25, realtime data-plane task I8
(`tools/kalshi_rate_limit_probe.py`, `docs/superpowers/research/2026-08-25-rest-demand-study.md`).
**Discrepancy (2026-08-25, I12 review):** `rate_limits.md` says Basic-tier
Read buckets "hold up to two seconds of budget" (= 400 tokens / 40 requests
at 200 tokens/s), but this account's live `GET /account/limits` reported a
600-token capacity. Also, every rate-limit sentence in the mirror is written
for **authenticated** requests; the app's market-data client is
unauthenticated, and the mirror says nothing about the anonymous ceiling —
treat 20 req/s / 60-burst as the account's numbers, not this traffic's, until
the anonymous ceiling is probed (carried as an open verification into the
remediation design). Docs/live disagreement recorded here rather than
resolved by guessing.

## Does the lifecycle `settled` WS message carry the market's result?
**Answer:** No. `market-and-event-lifecycle.md` (the `market_lifecycle_v2`
channel) gives the `determined` message a `result` field (plus the
determination timestamp and settlement value), while the `settled` message
carries only `settled_ts`. `market_lifecycle.md` adds that the WS `settled`
event "corresponds to settlement being processed" and that in REST a settled
market ends at status `finalized` with `settlement_ts` populated — so a REST
read issued the instant `settled` arrives can still see a not-yet-`finalized`
market. The channel lists no `amended`/`disputed` event; whether a
re-determination re-fires `determined` is not stated.
**Gotcha:** `services/whale_stream/whale_stream_handlers.py`'s settled
handler re-reads the market immediately and drops the ticker if it isn't
`finalized` yet (leaving it to "the REST-tick fallback path ... if it ever
resurfaces on the watchlist") — that immediate read was 23% of all REST
demand in I8, and the settlement-processing race means part of it is wasted.
The remediation design (I13) batches and defers it (`GET /markets?tickers=…`
for N settlements after a delay, with retry) rather than removing it: **shipped 2026-08-29** (`services/settlement_resolver.py`; the settled handler only enqueues now) — the app
deliberately grades on `finalized`, not `determined` (2026-08-23 correction,
"disputed-and-reversed-result gap"), so some REST read stays necessary.
**Source:** `market-and-event-lifecycle.md` (message field tables),
`market_lifecycle.md` ("Transitions", "Settlement").
**Found:** 2026-08-25, realtime data-plane task I12 (adversarial review of the
REST demand-reduction item).

## What is the unauthenticated market-data REST ceiling?
**Answer:** Measured live (not documented anywhere in the mirror -
`rate_limits.md` only states authenticated per-tier read/write token
buckets): a single `GET /markets/{ticker}` client with no credentials
sustained **~51.3 req/s** cleanly, then hit its first 429 when ramped to
**~76.9 req/s**. This is the real anonymous ceiling, not this app's own
locally configured rate limiter - the probe used a raw, unauthenticated
`kalshi_python_async` client (`services.kalshi.transport.build_public_client`)
that bypasses `call_with_backoff`/the app's token bucket entirely.
**Gotcha:** This app never runs anywhere near this ceiling in production
(it goes through the authenticated account's own token bucket for
everything, including market-data reads, per `services/kalshi/public.py`),
so this number is a ceiling on what an anonymous/public integration could
sustain, not a live operating constraint - useful context if a future
anonymous-only tool (this probe itself, a public dashboard, etc.) is ever
built against this endpoint family.
**Source:** Live measurement, `python -m tools.kalshi_rate_limit_probe
--anonymous-ceiling` (realtime data-plane remediation plan P0 Task 4).
**Found:** 2026-08-26, realtime data-plane remediation P0 Task 4.

## Does a dispute/re-determination (`disputed`/`amended`) re-fire `determined` on `market_lifecycle_v2`?
**Answer:** No — and the gap is wider than "undocumented." `disputed` and
`amended` are documented REST-visible market statuses
(`market_lifecycle.md`'s Statuses table: `disputed` = "Result has been
challenged. May be re-determined."; `amended` = "Re-determined after a
dispute. Settlement timer restarts."), but neither appears anywhere in
`market_lifecycle_v2`'s event-type vocabulary. Confirmed by exhaustive grep
of both lifecycle pages (`grep -in "amended\|disputed" market-and-event-
lifecycle.md` → zero hits; same page's full WS `event_type` enum is
`created, deactivated, activated, close_date_updated, determined, settled,
price_level_structure_updated, metadata_updated` — 8 values, none of them
dispute/amendment-related). `market_lifecycle.md`'s own "Explicit (WebSocket
event emitted)" transition list is the same story: it enumerates
`closed → determined` (event `determined`) and `determined`/`amended` →
`finalized` (event `settled`), but has **no entry at all** for
`determined → disputed` or `disputed → amended` — those two REST status
transitions are simply absent from the list of transitions that emit a WS
event. So this isn't just "`determined` doesn't re-fire" — a dispute or
amendment produces **no `market_lifecycle_v2` event whatsoever**; the only
observable signal a WS-only consumer ever gets is the original `determined`
(with the pre-dispute `result`) and, later, one `settled`.
**Gotcha (the actual risk for Task 21, the settled resolver):** because
`settled` carries no `result` field itself (see the "Does the lifecycle
`settled` WS message carry the market's result?" entry above) and no
distinct event exists for `amended`, a consumer that caches
`determined.result` the moment it arrives and treats `settled` merely as
"go pay out using the cached result" can silently ship a stale, pre-dispute
result if the market was disputed and amended in between — there is no WS
signal to invalidate the cache. Task 21 must therefore treat a cached
`determined.result` as **provisional until the ticker's REST status reaches
`finalized`**, and re-read the market via REST at/after `settled` rather
than trusting the cached WS value as a terminal decision — this app already
does the REST re-read (`services/whale_stream/whale_stream_handlers.py`'s
settled handler), so the fix is keeping that re-read (not removing it as a
"redundant" REST call), plus explicitly never short-circuiting on the
cached `determined.result` alone anywhere else settlement is decided.
**Source:** `market-and-event-lifecycle.md` (full `event_type` enum + field
table, lines 62-115 and 340-410), `market_lifecycle.md` (Statuses table and
"Transitions" section, lines 11-52).
**Found:** 2026-08-26, realtime data-plane remediation P0 Task 5.
**Found:** 2026-08-26, realtime data-plane remediation P0 Task 4.

## Can several channels go in one `subscribe` command, and how does the server answer?
**Answer:** Yes for channels that share the same (or no) params. `websocket-
connection.md`'s Subscribe Command schema takes `params.channels` as an array,
but every channel-specific param (`market_tickers`, `index_ids`,
`underlying_tickers`, `send_initial_snapshot`) sits at the **top level of the
one `params` object** and therefore applies to every channel in that message -
so only channels with no channel-specific params (exchange-wide `trade`,
`market_lifecycle_v2`, `fill`, `market_positions`) can safely share a message;
`ticker` (needs `market_tickers`) and the index feeds cannot. The server replies
with **one `subscribed` message per channel** (`{"channel": ..., "sid": ...}` -
Subscribed Response schema, not a combined list), so a per-message handler
needs no change. Whether a rejected channel fails the whole command or only
itself is **not documented** either way. Separately, `send_initial_snapshot`
is available on `update_subscription`/`add_markets` too (default `false`),
scoped to "newly added market tickers on the ticker channel."
**Source:** `websocket-connection.md` (Subscribe Command, Update Subscription -
Add Markets, Subscribed Response, Error Response schemas).
**Found:** 2026-08-27/28, P7 Task 33 + its adversarial review (R3/R5).

## What `result` values can a finalized market carry — is it only yes/no?
**Answer:** No — `scalar` is a third documented value, and it currently
arrives as an empty string. `market_lifecycle.md:68`: "After a market closes
and the outcome is known, the market is determined and `result` is set to
`yes`, `no`, or `scalar`." `market-settlement.md:23` says the same on the FIX
side (tag 20107 `MarketResult`, "Result of the market when determined: `yes`,
`no`, or `scalar`", Required=Yes). `changelog-index.md:3245-3246` adds the
spelling trap: "Currently, a market settled to a scalar result will return
`""` in the `market_result` field. Starting in the next release, this value
will read `"scalar"` instead." So a scalar market is `""` today and `scalar`
after that release ships — code must handle both, which is why the resolver's
condition stays `result not in ("yes", "no")` rather than testing for either
spelling.
**Gotcha:** `services/settlement_resolver.py` skipped those markets silently
and counted them in the same `dropped_total` as retry give-ups, so one number
meant both "correctly skipped a market with no binary outcome" (expected) and
"gave up and lost a settlement outcome" (a completeness defect) — and
`docs/next-action.md` gated a soak on that number being 0, which no scalar
settlement can ever satisfy. All 64 drops observed live on 2026-08-30 came
from the skip branch: `data/fault_log.db` held zero `settlement_resolver` rows
across 8 days of retention and the give-up branch always fault-logs. Which
values they actually carried was unrecoverable, because the branch recorded
neither ticker nor result. Split into `dropped_after_max_attempts` (defect,
expect 0) and `skipped_non_binary_result` (expected), with a bounded
count-by-observed-value map so `""` vs `scalar` vs an absent field stay
distinguishable; `dropped_total` survives as the conservation sum only.
**Source:** `market_lifecycle.md` (Determination and settlement, line 68),
`market-settlement.md` (Message Structure, tag 20107, line 23),
`changelog-index.md` ("Get markets may return scalar result", lines 3245-3246).
**Found:** 2026-08-30, #208.

## Is a NO contract's per-contract cost really `1 - yes_price`, and where does the app compute it?
**Answer:** Yes. Kalshi quotes every price in yes terms and the two sides
of a binary market are complements: "a bid for yes at price X is equivalent
to an ask for no at price (100-X)" — a yes bid at 7¢ is a no ask at 93¢.
So a NO buyer at yes price X pays (1 - X) per contract, and a NO buyer's
fill price in yes terms is the yes *bid*, not the ask. The app computes it
in exactly one place, `services/kalshi_fees.unit_cost(side, yes_price)`;
`tools/quality_audit/unit_cost.py` fails CI on any inline copy.
**Gotcha:** the trade feed also carries `no_price_dollars` directly
(`get-trades.md`), which `services/kalshi/contracts/trade.py`'s
`taker_notional_usd` reads as sent rather than deriving — fidelity at the
boundary; derivation only where the exchange sent nothing to read.
**Source:** `get-market-orderbook.md`, `get-multiple-market-orderbooks.md`
(endpoint description), `get-trades.md` (`yes_price_dollars` /
`no_price_dollars`), `orderbook_responses.md` §"complementary opposite".
**Found:** 2026-08-30, issue #212 — consolidating 26 inline `1 - price`
re-derivations (the no-side inversion class CLAUDE.md names twice) behind
one helper; needed the documented statement, not the convention, to cite.

## Which portfolio objects now carry a required `exchange_index`, and do this app's readers keep it?
**Answer:** Trade API 3.29.0 added `exchange_index` (integer, "Identifier
for the exchange shard where the fill occurred") as a **required** field of
the WS fill message (`user-fills.md:207` required list, property at :241,
`"exchange_index": 2` in the example at :326), the WS order message
(`user-orders.md:250`, property :284), the REST Fill (`get-fills.md:194`,
property :210), the REST MarketPosition (`get-positions.md:189`), and the
REST Settlement (`get-settlements.md:171`). The same name is also a new
optional query *filter* on GetFills/GetPositions/GetOrders
(`get-fills.md:166`, `get-positions.md:149`, `get-orders.md:174`) — same
spelling, different role.
**Gotcha:** No file under `services/` or `tests/` mentions `exchange_index`
at all (grepped 2026-08-30). `services/kalshi/contracts/fill.py`'s
`normalize_fill` spreads `**msg` so the field survives the normalizer
itself, but the module docstring's field inventory ("every documented field
survives intact") predates it, `services/position/account_positions.py`'s
`_FILL_FIELDS` whitelist (line 48) silently strips it from `/api/state`,
and `tests/fixtures/kalshi/fill.json` lacks a field the message schema now
marks required — fixture tests exercise a pre-3.29.0 shape. With crypto on
shard 2 and tennis/baseball on shard 3 (`exchange_sharding.md:22,81-82`),
two positions with the same ticker on different shards would be
indistinguishable to every downstream consumer. App fix tracked in its own
issue; do not hand-edit the fixture without the reader change.
**Source:** `user-fills.md` (lines 99/207/241/326), `user-orders.md`
(97/250/284/424), `get-fills.md` (166/194/210), `get-positions.md`
(149/189), `get-settlements.md` (171), `exchange_sharding.md` (22, 81-82).
**Found:** 2026-08-30, issue #248 re-sync to Trade API 3.29.0.

## What do GET /portfolio/balance's `balance` and `portfolio_value` aggregate over?
**Answer:** All exchange shards, unless the (new) `exchange_index` query
param is passed: "Both values include all exchange indexes unless
`exchange_index` is provided" (`get-balance.md:7`). This is a 3.29.0
**semantics flip**: the pre-3.29.0 mirror of the same page said the
opposite — "`portfolio_value` is always scoped to the requested
`exchange_index` (defaulting to 0)" (old `get-balance.md:7`, see
`git log -p -- docs/kalshi/get-balance.md`).
**Gotcha:** `services/kalshi/account.py` `get_balance()` (line 70) passes
no index, so the number this app displays and records **changed meaning
underneath it**: previously shard-0-scoped, now a cross-shard aggregate —
and shards 2 (crypto) and 3 (tennis/baseball) have been live since
2026-08-24 (`exchange_sharding.md:22`). `main.py`'s real-balance /
real-portfolio-value session series (~line 993-1010) and the header strip
therefore changed scope without any code change here, and recorded history
spanning the flip mixes two scopes. "A displayed value must match its
label" — at the ground-truth layer. App-side decision tracked in its own
issue (this may be the *desired* scope, but it must be a decision, not an
accident).
**Source:** `get-balance.md:7` (new mirror) vs the pre-3.29.0 mirror's
line 7; `exchange_sharding.md:22,81-82`; `services/kalshi/account.py:70-72`.
**Found:** 2026-08-30, issue #248 re-sync to Trade API 3.29.0.

## What precision is a trade fee rounded (up) to?
**Answer:** `$0.000001` — six-decimal dollar amounts: "Fees are six-decimal
dollar amounts (`$0.000001` granularity) — the finest precision a fill's
revenue (price × quantity) can occupy" (`fee_rounding.md:18`); the trade
fee component is "rounded up to the nearest `$0.000001`"
(`fee_rounding.md:22`). Pre-3.29.0 the same lines said `$0.0001`
(centicent). Unchanged: direct-member *balances* still align to `$0.0001`
(:13) and rebates still use target balance precision — `$0.0001` direct,
`$0.01` non-direct (:42, :72).
**Gotcha:** `services/kalshi_fees.py` still ceils per fill to `$0.0001`
(`math.ceil(raw * 10000) / 10000`, lines 117/137), overstating a fill's
trade fee by up to $0.000099. PR #224's convergence argument (ceiling
applied once per fill, not per contract) still holds — a finer ceiling only
*tightens* that bound — so this is a constant-precision update, not a
mechanism change. App fix tracked in its own issue.
**Source:** `fee_rounding.md` (lines 13, 18, 22, 42, 72).
**Found:** 2026-08-30, issue #248 re-sync to Trade API 3.29.0.

## Where does an order route when `exchange_index` is omitted, and what does a single REST write cost?
**Answer:** 3.29.0 changed the omitted-field default from "shard 0" to
**auto-routing**: "Exchange shard index. If omitted, auto-routes when
ticker is provided; otherwise defaults to 0. Use -1 to require
auto-routing" (`create-order-v2.md:215-216`; cancel's `ticker` param is
"Market ticker used for auto-routing when exchange_index is omitted",
`cancel-order-v2.md:90`; REST rule spelled out at
`exchange_sharding.md:60-62`). Billing: auto-routed single REST order
writes are billed to the unscoped Write bucket **and every nonzero shard's
Write bucket**; explicitly targeting a nonzero shard bills only that
shard's budget; batch writes and explicit shard-0 writes use only the
unscoped budget (`exchange_sharding.md:88`). Auto-routing "will incur an
additional latency cost" (:91). Related 3.29.0 note: `cancel-order-v2.md:10`
and `get-order.md:10` now state "**Rate limit:** 2 tokens per request".
**Gotcha:** This app never passes `exchange_index` anywhere (no hit in
`services/`), so a future real order would be auto-routed and billed
against *every* nonzero shard's write budget — a per-shard rate-limit and
latency consideration for the live order path. Paper mode is unaffected
and real trading stays gated (`kalshi_account.trading_enabled` default
false); this is a Program 3 (live execution) item, deliberately not acted
on now.
**Source:** `create-order-v2.md:215-216`, `cancel-order-v2.md:10,79-90`,
`get-order.md:10`, `exchange_sharding.md:60-62,88,91`.
**Found:** 2026-08-30, issue #248 re-sync to Trade API 3.29.0.

## How do you actually discover multivariate (combo) markets, and why did the categories widening not fix KXMVECROSSCATEGORY?
**Answer:** Two distinct endpoints, neither reachable through the regular
series/market browsing this app already uses. `GET
/multivariate_event_collections` (`get-multivariate-event-collections.md`)
returns the static *template* a combo is generated from
(`collection_ticker`, `series_ticker`, `associated_events`, `is_ordered`,
`size_min`/`size_max`) — filterable by `status`/`series_ticker`/
`associated_event_ticker`, paginated. `GET /events/multivariate`
(`get-multivariate-events.md`) returns the dynamically-created *instances*
that actually trade — filterable by `series_ticker` XOR
`collection_ticker` (mutually exclusive per the doc), with
`with_nested_markets=true` embedding each event's own `Market` objects in
one call. `GET /markets` also has an `mve_filter` param (`only`/`exclude`)
that includes/excludes combos, but plain `GET /events` explicitly
"excludes multivariate events" — a combo's own `title`/`sub_title`/
`mutually_exclusive` is ONLY ever available from `/events/multivariate`.
**Root cause (live-verified 2026-08-30, not assumed):** every
multivariate-producing series reports `volume_fp: "0.00"` on its own
`/series` (`get-series-list.md`) entry — confirmed on all 16 real series
sampled, `KXMVECROSSCATEGORY`/`KXMVECROSSCATEGORY-SHARD1` included — even
while its dynamically-created markets carry real trading activity. This
app's `_get_series_cache` (`services/market_watch/catalog_scan.py`)
filters `get_series_list()` down to `volume_fp > 0` before any category
logic ever runs, so an MVE series is silently excluded regardless of which
categories are configured. The 2026-08-30 `kalshi.categories` widening
(`docs/open-decisions.md`) could not have fixed this no matter which
categories it added — the series never reaches the category bucket at
all.
**Gotcha 1 (occurrence_datetime):** a multivariate market's own
`occurrence_datetime` is **always** null — 2,000+ real
`KXMVECROSSCATEGORY-SHARD1` markets sampled via `with_nested_markets=true`,
zero exceptions. A combo has no single "occurrence" moment by
construction (its legs can span unrelated events/times). `close_time` is
always populated and is the right near-term-horizon anchor instead (same
"close_time is the one signal every market shape agrees means trading has
stopped" reasoning already used for `KXBTC15M`'s own
occurrence/close mismatch).
**Gotcha 2 (no recency/status filter, and shards):** `/events/multivariate`
has no timestamp or status query param at all (only
`limit`/`cursor`/`series_ticker`/`collection_ticker`/
`with_nested_markets`), and its pagination order is not simply
chronological or status-correlated. The **base** (unsharded) series
ticker `KXMVECROSSCATEGORY` was 100% `finalized` across 2,000 sampled
events with zero `occurrence_ts`/active rows, while its sharded sibling
`KXMVECROSSCATEGORY-SHARD1` (also a real, distinct `/series` entry) was
1,818/2,000 `active` on page 1 of the identical query — Kalshi appears to
retire a base series ticker once cardinality grows and route new combos to
a `-SHARDn` sibling, with no documented signal saying which is "current."
`get_markets(status="open", mve_filter="only", series_ticker=X)` looked
promising (regular `get_markets` already supports real status filtering)
but returned `status: "closed"` rows for the base ticker despite the
explicit `status="open"` filter — the filter IS honored correctly for
`-SHARD1` (`status: "active"` rows with real near-future `close_time`),
so this is the base ticker's own history being mixed in, not a broken
filter.
**Gotcha 3 (series discovery heuristics don't work):** `get_series_list()`
ticker-naming (`"MVE" in ticker`) or category (`category == "Exotics"`)
heuristics silently **miss real cases** — `KXCITIESWEATHER` appeared as a
real collection's `series_ticker` (confirmed live) with neither an
"MVE"-shaped name nor category `"Exotics"`. `get_multivariate_event_collections`
(no filter, ~1,389 rows / 7 pages at the documented 200-row max) is the
only reliable discovery source for "which series currently produce MVE
events."
**Gotcha 4 (always-empty/placeholder fields):** on every real multivariate
event sampled, `sub_title` is the literal string `"MVE"` (not a real
subtitle), `collateral_return_type` is `""` (empty string, not the
populated value a regular event carries), `product_metadata` is `null`,
and `last_updated_ts` is `"0001-01-01T00:00:00Z"` (Go's zero-value
timestamp, not a real update time). None of these are fixture/mirror
gaps — confirmed directly against live production responses.
**Fix:** `services/market_watch/mve_scan.py` (issue #268) — its own
discovery path, independent of `kalshi.categories`, using
`get_multivariate_event_collections` (TTL-cached) to find MVE series and
`get_multivariate_events(series_ticker=X, with_nested_markets=True)` (one
page per series per cycle) to populate both `market_catalog.db`
(`market_catalog.upsert_mve_markets`, anchored on `close_ts`) and
`title_cache`'s `market_titles`/`event_titles`.
**Source:** `get-multivariate-events.md`, `get-multivariate-event-collections.md`,
`get-markets.md` (`MveFilterQuery`), `get-events.md` ("excludes
multivariate events"), `get-series-list.md`; live-verified 2026-08-30
directly against `https://external-api.kalshi.com/trade-api/v2` (public,
unauthenticated GET endpoints).
**Found:** 2026-08-30, issue #268 (KXMVECROSSCATEGORY: 13,841 of 95,535
logged signals, 14.5%, with zero rows in `market_catalog.db`).

## How does the CF Benchmarks REST passthrough's history endpoint actually work, and what does it cost?
**Answer:** `GET /trade-api/v2/cfbenchmarks/history/values?id=<index>&
timespan=<span>&timestamp=<ISO8601>` forwards verbatim (query string
included) to CF Benchmarks' own `/api/v1/history/values`
(`rest-passthrough.md`). It is a **bucketed lookup, not an arbitrary
[start, end) range query**: CF Benchmarks' own docs (docs.cfbenchmarks.com/
api/rest/historical-values - NOT mirrored under `docs/kalshi/`, fetched
live 2026-08-30 since the mirror explicitly defers index/parameter detail
to it) state "the timestamp must be truncated to the timespan granularity"
- `timespan=HOUR` fetches the whole UTC hour containing `timestamp`, so a
gap spanning an hour boundary needs one request per hour touched, filtered
client-side to the actual window afterward (`services/index_feed/
backfill.py`'s `_hour_bucket_starts`/window filter). Cost: 50 tokens/
request from the Read bucket vs this app's usual default 10
(`rest-passthrough.md`'s "Rate limit" section) - a real 5x outlier, worth
its own caller class if measuring where read-bucket budget goes.
**Gotcha:** "the most recent values may not be immediately available, and
could be delayed by up to 15 minutes" (CF Benchmarks docs, same fetch) - a
backfill that runs seconds after a WS reconnect can legitimately get back
fewer points than the gap actually contains, with no error to signal it.
Also requires "authorization for both the target index and the
STREAM_HISTORICAL_VALUES data stream" - the same account entitlement gate
`rest-passthrough.md`'s own "Access" section names generically
("available only to accounts with the appropriate entitlement"); this
repo's own credentials have not been confirmed to hold it.
**Also unresolved:** the history endpoint's response *body* schema inside
`data.payload` was not retrievable through available fetch tooling (only
the generic envelope example and the *live* WS frame shape are confirmed -
`cfbenchmarks-value.md`'s AsyncAPI example: `{"type":"value","id":"BRTI",
"time":<ms>,"value":"<str>"}`). Treated as UNVERIFIED, not guessed: parsing
is defensive (several plausible payload shapes, a point with no derivable
timestamp is dropped and logged rather than stored under a wrong time) and
the first real response is logged in full for a human to check
(`services/kalshi/websocket.py`'s own `_logged_fill_shape` idiom).
**Source:** `rest-passthrough.md`, `cfbenchmarks-value.md`; CF Benchmarks'
own `docs.cfbenchmarks.com/api/rest/historical-values` (not mirrored).
**Found:** 2026-08-30, issue #260 (index_feed reconnect-gap backfill).

## What do GET /exchange/user_data_timestamp and GET /api_keys return?
**Answer:** `GetUserDataTimestampResponse` (`get-user-data-timestamp.md`) is
one required field: `as_of_time`, an RFC3339 date-time string - "an
approximate indication of when the data reflected in this endpoint is
likely as of" for GetBalance/GetOrder(s)/GetFills/GetPositions.
`GetApiKeysResponse` (`get-api-keys.md`) is `api_keys` (required, a list of
`{api_key_id, name, scopes, subaccount?}`) plus `api_key_region_expiration_ts`
(optional int64 unix seconds, nullable): "Once this date has passed, API
keys are not valid for trading Sports, Elections, and Entertainment
markets... Absent when the account has never attested."
**Gotcha:** the installed `kalshi_python_async` SDK (3.27.0, confirmed live
in the fastapi container 2026-08-30) predates
`api_key_region_expiration_ts` entirely - its `GetApiKeysResponse` Pydantic
model has no such member, and both `.from_dict()` and `.model_validate()`
silently drop the key even when the raw HTTP response body carries it
(confirmed directly: `GetApiKeysResponse.model_validate({"api_keys": [],
"api_key_region_expiration_ts": 123}).model_dump()` comes back with only
`api_keys`). Trusting `.model_dump()` here - the pattern every other read
in `services/kalshi/account.py` uses - would silently drop exactly the
field issue #261 exists to surface. `KalshiAccountGateway.get_api_keys()`
calls `get_api_keys_with_http_info` instead of `get_api_keys` and recovers
the field from `ApiResponse.raw_data` (the real response bytes) rather
than the parsed model - CLAUDE.md's "no lossy normalization on the way in"
applied to a vendored-SDK/doc version gap, not a call-site preference.
**Source:** `get-user-data-timestamp.md`, `get-api-keys.md`.
**Found:** 2026-08-30, issues #266/#261.

## How does an event-level fee override interact with the series-level fee table, and what does `quadratic_with_combo_maker_fees` actually change?
**Answer:** `get-event-fee-changes.md`: "Event fees are an override layered
on top of the parent series' fee structure. If `fee_type_override` and
`fee_multiplier_override` are null, that indicates the override is
cleared" — each of the two columns falls back to the series' own value
**independently** (an event can override just the multiplier, just the
type, both, or neither). `get-series-list.md`'s `FeeType` schema:
`quadratic`/`quadratic_with_maker_fees`/`quadratic_with_combo_maker_fees`
all reference the *same* General Trading Fees Table for the taker rate —
only `quadratic_with_combo_maker_fees` changes anything, and only the
maker multiplier (0.5 instead of the standard 0.25). Corroborated
independently by `changelog-index.md`'s 2026-08-22 "Combo RFQ fee
assignment for briefly resting orders" entry: "The maker fee uses a fee
multiplier of `0.5`, rather than the standard `0.25`" for a combo quote
crossing a resting order under five seconds old — background on *why*
Kalshi assigns this fee_type, not a different rule; the schema's own
description is the persistent per-market contract this app can actually
read. `flat` (the fourth enum value) is unmodeled anywhere in this app —
no cached market/event has ever resolved to it, and its formula ("Specific
Trading Fees Table") isn't documented in a page this repo mirrors.
**Gotcha:** `services/kalshi_fees.py` never read `fee_type` or either
override column at all before issue #264 — every fee went through
`_multiplier(ticker)`, a per-SERIES lookup only, even though
`services/title_cache.py` had already been persisting both override
columns (from every `get_event()` fetch) since 2026-08-15. Fixed by
`title_cache.fee_override_for_ticker()` (a single indexed
market_titles->event_titles join) feeding `kalshi_fees._event_fee_override()`.
**Source:** `get-event-fee-changes.md`, `get-series-list.md`'s `FeeType`
schema, `changelog-index.md` (2026-08-22 entry).
**Found:** 2026-08-30, issues #264/#258.

## Where does the KXMVECROSSCATEGORY0-SHARD1 NFL-combo maker-fee exemption live, and why can't `series_of()` find it?
**Answer:** `changelog-index.md`'s 2026-08-20 "Maker fee exemption for
independent NFL combo markets" entry: combo markets created after 11:59 PM
ET on 2026-08-19, composed entirely of independent NFL components ("every
component ties to a different milestone (NFL game)"), are created under
series `KXMVECROSSCATEGORY0-SHARD1` and have **no maker fee** — the
changelog says nothing about the taker fee for this series.
**Gotcha:** the series ticker itself contains a hyphen
(`KXMVECROSSCATEGORY0-SHARD1`) — every other entry in
`_FEE_MULTIPLIER_BY_SERIES` is hyphen-free.
`services/signal_log.py::series_of()` splits on the **first** hyphen only,
so `series_of("KXMVECROSSCATEGORY0-SHARD1-25NOV02-X")` returns just
`"KXMVECROSSCATEGORY0"`, silently dropping `-SHARD1` — adding this ticker
to `_FEE_MULTIPLIER_BY_SERIES` keyed by `series_of()` (a literal reading of
issue #258's own suggested fix) would never have matched a single real
market. It also can't share that table at all even with a correct key:
`_FEE_MULTIPLIER_BY_SERIES` is applied identically to `taker_fee()` and
`maker_fee()` via `_multiplier()`, and a 0.0 entry there would have zeroed
the taker fee too, which the changelog never says. Fixed as its own
explicit maker-only exemption
(`kalshi_fees._is_nfl_combo_maker_exempt()`, matched by literal ticker
prefix, not `series_of()`).
**Source:** `changelog-index.md` (2026-08-20 entry, "Maker fee exemption
for independent NFL combo markets").
**Found:** 2026-08-30, issue #258.

## Can a `min_updated_ts` watermark be a float (`time.time()`) or must it be an int?
**Answer:** It must be an **integer** — Unix seconds, no fractional part.
`get-milestones.md` types the parameter `type: integer, format: int64`
("Filter milestones with metadata updated after this Unix timestamp (in
seconds)"), and the same int64 typing appears on every other endpoint that
takes this filter: `get-events.md`, `get-markets.md`, `get-series-list.md`.
So `min_updated_ts=time.time()` is wrong everywhere the app uses a
watermark, not just on `/milestones`.
**Gotcha:** nothing rejects a float client-side. The vendored SDK's
`MilestoneApi.get_milestones` (the method `services/kalshi/public.py`
actually calls) types every parameter `Any`; only its stricter
`get_milestones_with_http_info` sibling types this one
`Annotated[Optional[StrictInt], ...]`. The float reaches Kalshi and comes
back **HTTP 400** — live-verified 2026-08-30 against
`GET /trade-api/v2/milestones?limit=1&category=Sports&min_updated_ts=...`:
`1756500000` → 200 with real milestones, `1756500000.123` → 400,
`{"msg":"Invalid format for parameter min_updated_ts: error binding string
parameter: strconv.ParseInt: parsing \"1756500000.123\": invalid syntax"}`.
That is a plain per-request failure with no local exception, so a caller
that catches-and-continues per category (as
`services/market_watch/milestone_scan.py` does) would log a generic error
and silently never advance past its cold-start run — the failure looks
like an empty result set, not a type error.
**Source:** `get-milestones.md` (`min_updated_ts` schema), corroborated by
`get-events.md`/`get-markets.md`/`get-series-list.md`; live probe above.
**Found:** 2026-08-30, `milestone_scan.py`'s first watermarked scan
(entry-gate-me-pairing-and-netting-remediation Part 3).
