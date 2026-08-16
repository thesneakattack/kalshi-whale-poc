Source: https://docs.kalshi.com/api-reference/milestone/get-milestones.md

# Get Milestones (list)

## Route

`GET /milestones`

## Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | integer | Yes | 1-500 per page |
| `minimum_start_date` | RFC3339 | No | filter by earliest start date |
| `category` | string | No | e.g. Sports, Elections, Esports, Crypto |
| `competition` | string | No | e.g. Pro Football, Pro Basketball |
| `source_id` | string | No | filter by source identifier |
| `type` | string | No | e.g. football_game, basketball_game |
| `related_event_ticker` | string | No | **single value only**, not a list |
| `cursor` | string | No | pagination cursor |
| `min_updated_ts` | integer (unix sec) | No | incremental-poll watermark |

## Response shape

```json
{
  "milestones": [
    {
      "id": "string", "category": "string", "type": "string",
      "start_date": "RFC3339", "end_date": "RFC3339 or null",
      "related_event_tickers": ["string"], "title": "string",
      "notification_message": "string", "source_id": "string or null",
      "source_ids": {"key": "value"}, "details": {"key": "value"},
      "primary_event_tickers": ["string"], "last_updated_ts": "RFC3339"
    }
  ],
  "cursor": "string"
}
```

## Live verification (2026-08-15, real account)

`get_milestones(limit=200, category="Sports", min_updated_ts=<6h ago>)` -
**one call, no per-event filter at all** - returned 200 milestones
covering **1,483 distinct `related_event_tickers`**.

## Real architectural finding

`main.py`'s current design calls `get_milestones_for_event(et)`
(`related_event_ticker=et`, one value) once per event ticker in the
watchlist/candidate pool - both in `_fetch_live_status` and
`propagate_milestone_winners`. Since `related_event_ticker` can't take a
list, that per-event pattern can't be batched directly the way
`get_events`/`get_live_datas` can. But the *category+min_updated_ts* form
above is a genuinely different, much cheaper strategy: a periodic (e.g.
every few minutes) single category-scoped call could build a local
`event_ticker -> milestone` map covering the *entire* candidate pool in
one request, which `_fetch_live_status`/`propagate_milestone_winners`
would then read from locally (zero API cost) instead of each doing its
own per-event REST round trip. Not yet implemented - a real "reapproach"
candidate, bigger in scope than the caching/batching fixes already
shipped this session, flagged for prioritization rather than applied
blind.

This file is a local copy of the fetched page content used during this
session.
