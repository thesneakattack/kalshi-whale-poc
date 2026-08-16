Source: https://docs.kalshi.com/getting_started/rate_limits.md
Source: https://docs.kalshi.com/api-reference/account/list-non-default-endpoint-costs.md

# Rate Limits and Tiers (refetched + live-verified 2026-08-15)

Supersedes the understanding this app's rate-limit incident response was
built on earlier the same day - that response correctly identified
"token-bucket throughput, not concurrency" as the real model, but tuned
the actual numbers (3.0 tokens/sec, burst 2.0) empirically against
observed 429s rather than against real tier data, because several
uncapped/uncached call sites (since fixed - see ROADMAP.md) were
producing much larger real bursts than the nominal configured rate
implied at the time.

## Token costs

- Default cost: **10 tokens** for most requests.
- Some endpoints have a non-default cost - check `GET
  /account/endpoint_costs` (SDK: `get_account_endpoint_costs()`).
- Batch requests are billed per item, no volume discount.

## Tier read/write budgets (tokens/second)

| Tier | Read | Write |
|------|------|-------|
| Basic | 200 | 100 |
| Advanced | 300 | 300 |
| Expert | 600 | 600 |
| Premier | 1,000 | 1,000 |
| Paragon | 2,000 | 2,000 |
| Prime | 4,000 | 4,000 |
| Prestige | 10,000 | 8,000 |

Tier qualification: Basic on signup; Advanced via a manual upgrade call;
Expert+ auto-granted from trailing-30-day trading volume (or manual
Kalshi assignment), thresholds documented on the same page.

## REAL values for this app's actual connected account (live-verified, not assumed)

`GET /account/api_limits` (SDK: `get_account_api_limits()`):

```json
{
  "usage_tier": "basic",
  "read": {"refill_rate": 200, "bucket_capacity": 600},
  "write": {"refill_rate": 100, "bucket_capacity": 100},
  "grants": []
}
```

Real read burst capacity is **600 tokens** (3 seconds' worth at the 200/sec
refill rate) - larger than the generic docs page's "one second of budget
maximum" framing for Basic-tier reads suggested; the account-specific
numbers are authoritative over the generic prose.

`GET /account/endpoint_costs` (SDK: `get_account_endpoint_costs()`) -
**complete list of every non-default-cost endpoint on this account**:

| Method | Path | Cost |
|--------|------|------|
| POST | /account/api_usage_level/upgrade | 30 |
| GET | /cfbenchmarks | 50 |
| GET | /cfbenchmarks/*endpoint | 50 |
| POST | /communications/quotes | 2 |
| DELETE | /communications/quotes/:quote_id | 2 |
| GET | /communications/quotes/:quote_id | 2 |
| DELETE | /communications/rfqs/:rfq_id/quotes/:quote_id | 2 |
| GET | /communications/rfqs/:rfq_id/quotes/:quote_id | 2 |
| GET | /margin/balance | 5 |
| DELETE | /portfolio/events/orders/:order_id | 2 |
| DELETE | /portfolio/events/orders/batched | 2 |
| GET | /portfolio/orders/:order_id | 2 |

**None of these are endpoints this app calls.** Every endpoint this app
actually uses (markets, market, event, events, series_list, milestones,
live_data, live_datas, event_live_data, trades, candlesticks,
exchange_status, tags/filters search, balance, positions, fills, orders,
create_order, cancel_order) costs the flat default of 10 tokens.

## What this means for `services/http_client.py`'s rate limiter

Real, confirmed sustainable rate for this account: **200 ÷ 10 = 20
read-requests/sec**, with a 600-token (≈3-second) burst pool. The current
limiter (`_KALSHI_READ_RATE_PER_SEC = 3.0`, burst 2.0) uses roughly 15%
of the real sustained budget and a burst pool ~300x smaller than what's
actually available. The earlier same-day 6/sec and 8/sec tests that
produced 429s were run before catalog-scan's 40-per-batch, live_status's
uncapped per-tick poll, and the uncached milestone/event-live-data loops
were fixed (see ROADMAP.md 2026-08-15 entries) - those bugs created much
larger real instantaneous bursts than their nominal configured rate
implied, which is the more likely explanation for those 429s than the
nominal rate itself being unsafe. Raising the limiter meaningfully (e.g.
toward the confirmed real ceiling, with a sane safety margin) is a live,
evidence-backed candidate - flagged as an audit finding, not applied
automatically given how directly this touches the same code that caused
this session's earlier incident.

This file is a local copy of the fetched page content plus this app's own
live-verified account data, used during this session.
