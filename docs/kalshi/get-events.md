Source: https://docs.kalshi.com/api-reference/events/get-events.md

# Get Events (list/batch)

Get all events. Excludes multivariate events (use GET /events/multivariate
for those). All events are accessible even if their markets are older than
the historical cutoff.

## Route

`GET /events`

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `limit` | integer | No | results per page, default 200, max 200 |
| `cursor` | string | No | pagination cursor from previous response |
| `with_nested_markets` | boolean | No | include Market objects per event (default false) |
| `with_milestones` | boolean | No | include related milestones (default false) |
| `status` | string | No | unopened / open / closed / settled |
| `series_ticker` | string | No | filter by series ticker |
| `tickers` | string | No | **comma-separated list of event tickers** |
| `min_close_ts` | integer | No | events with markets closing after this ts |
| `min_updated_ts` | integer | No | incremental-poll watermark |

No `category` filter exists - `EventData.category` is deprecated (see
get-event.md).

## Response shape

```json
{
  "events": [
    {
      "event_ticker": "string", "series_ticker": "string",
      "title": "string", "sub_title": "string",
      "collateral_return_type": "string", "mutually_exclusive": "boolean",
      "available_on_brokers": "boolean",
      "settlement_sources": [{"name": "string", "url": "string"}],
      "strike_date": "ISO 8601 (nullable)", "strike_period": "string (nullable)",
      "markets": "[Market] (only if with_nested_markets=true)",
      "product_metadata": "object (nullable)",
      "last_updated_ts": "ISO 8601",
      "fee_type_override": "string (nullable)", "fee_multiplier_override": "number (nullable)",
      "exchange_index": "integer"
    }
  ],
  "milestones": "[Milestone] (only if with_milestones=true)",
  "cursor": "string"
}
```

## Live verification (2026-08-15, real account)

Fetched 3 real current NFL event tickers both individually (3x
`get_event()`) and batched (1x `get_events(tickers="A,B,C")`):

- individual: 3 calls, 0.36s wall
- batched: 1 call, 0.02s wall
- batched response returned all 3 requested events, no misses

**Real opportunity**: `main.py`'s `_fetch_event_titles` currently calls
`client.get_event(et)` once per not-yet-cached event ticker via
`asyncio.gather`. This confirms it could be rewritten as a single
`get_events(tickers=",".join(to_fetch))` call instead, cutting N REST
calls (and N rate-limit tokens) down to 1 regardless of how many events
need fetching in a given tick. Not yet implemented - flagged as an audit
finding, not applied automatically given the tick loop's REST-fetching
architecture was already substantially rewritten once this same session
(the rate-limit incident response).

This file is a local copy of the fetched page content used during this
session.
