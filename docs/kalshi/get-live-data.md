Source: https://docs.kalshi.com/api-reference/live-data/get-live-data-with-type.md
Source: https://docs.kalshi.com/api-reference/live-data/get-multiple-live-data.md

# Get Live Data (single + batch) - the MILESTONE-based live data endpoints

Distinct from Get Event Live Data (get-event-live-data.md, event-ticker-
keyed, crypto/commodity/weather only) - these are milestone-id-keyed and
are the real source for sports game state. `services/kalshi_client.py`'s
`get_live_data(milestone_type, milestone_id)` already wraps the single
form; the batch form is not yet wired in anywhere.

## Single: Get Live Data (with type)

`GET /live_data/{type}/milestone/{milestone_id}`

- Path params: `type` (required), `milestone_id` (required)
- Query param: `include_player_stats` (bool, default false) - Pro
  Football/Basketball/College Men's Basketball only

```
GetLiveDataResponse { live_data: { type, details, milestone_id } }
```

## Batch: Get Multiple Live Data

`GET /live_data/batch`

- Required: `milestone_ids` (array of strings, **max 100 per call**)
- Optional: `include_player_stats` (bool, default false)

```json
{"live_datas": [{"type": "string", "details": {}, "milestone_id": "string"}]}
```

SDK method name (kalshi_python_async 3.27.0): `client.get_live_datas(milestone_ids=[...])` -
not `get_multiple_live_data`/`get_live_data_batch` (neither of those names
exist on the SDK; found by introspecting the real installed client rather
than guessing from the doc page's title).

## Live verification (2026-08-15, real account, real NFL milestones)

3 individual `get_live_data()` calls: 0.99s wall.
1 batched `get_live_datas(milestone_ids=[...])` call: 0.02s wall.

Real response `details` for a football_game milestone (fields this app
currently has never extracted):

```json
{
  "away_points": 24, "home_points": 20, "clock": "00:00", "quarter": 4,
  "status": "closed", "widget_status": "finished", "winner": "",
  "last_play": {"description": "End Game", "occurence_ts": 1786835667},
  "last_updated_ts": 1786837766,
  "situation": {
    "down": 3, "goal_to_go": false, "yardline": 2, "yfd": 2,
    "possession_team_id": "...", "side_team_id": "..."
  }
}
```

## Real architectural finding

`main.py`'s `_fetch_live_status` and `propagate_milestone_winners` ALREADY
call this exact endpoint (the single form) for every event with a
milestone - but only ever extract `details.widget_status` (and, in
`propagate_milestone_winners`, `details.winner`). The rest of this real,
already-being-fetched payload - live score, quarter, clock, down/distance,
last play - is currently discarded. Surfacing it (e.g. into a new
`state["live_game_state"]` the dashboard could show alongside a
watchlisted market) would be pure value-add at zero extra API cost, since
the fetch already happens; only the parsing/storage would need to change.
Separately, switching both call sites to the batch form
(`get_live_datas`) instead of N individual `get_live_data` calls is a
direct, low-risk efficiency win once more than one milestone needs
refreshing in the same tick.

Neither change implemented yet - both flagged as concrete, evidence-backed
audit findings pending prioritization.

This file is a local copy of the fetched page content used during this
session.
