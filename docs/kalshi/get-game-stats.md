Source: https://docs.kalshi.com/api-reference/live-data/get-game-stats.md

# Get Game Stats

## Route

`GET /live_data/milestone/{milestone_id}/game_stats`

- Required: `milestone_id` (path)
- Returns null for unsupported milestone types or milestones lacking a
  Sportradar ID

## Response shape

```
GetGameStatsResponse {
  pbp: PlayByPlay {
    periods: [{ events: [{ ...additionalProperties }] }]
  }
}
```

Supported sports: Pro Football, College Football, Pro Basketball, College
Men's/Women's Basketball, WNBA, Soccer, Pro Hockey, Pro Baseball.

## Live verification (2026-08-15, real NFL milestone)

Returned real, detailed play-by-play: every play in the game with
`description`, `clock`, `down`, `yfd`, `away_points`/`home_points` at
that point, `wall_clock` timestamp - full drive-by-drive history, not
just current state.

## Assessment

Much heavier/more detailed than anything this app currently needs (it
tracks whale order flow and position management, not a live scoreboard
UI) - `get_live_data`'s `details` block (current score, quarter, clock,
down/distance - see get-live-data.md) is the right-sized signal for "is
this actually live and what's the state," not full play-by-play. Noted
for completeness; not a recommended integration target right now.

This file is a local copy of the fetched page content used during this
session.
