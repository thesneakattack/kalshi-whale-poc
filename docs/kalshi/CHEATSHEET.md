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
`.claude/hooks/session_orient.sh` can list titles at session start via
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
