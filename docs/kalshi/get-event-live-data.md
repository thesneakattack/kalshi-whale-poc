Source: https://docs.kalshi.com/api-reference/live-data/get-event-live-data.md

# Get Event Live Data

Get live data for an event by its event ticker.

The fetched page described this as serving event-keyed live data such as:
- crypto price charts
- commodity price timeseries
- weather observations

The `type` field names the schema of the `details` object.

## Route

`GET /live_data/events/{event_ticker}`

Optional query param surfaced in the fetch:
- `range` such as `15min`, `1h`, `1d`

## Response Shape

```yaml
GetEventLiveDataResponse:
  live_data:
    type: object
    required: [type, details]
```

Fields surfaced:
- `live_data.type`
- `live_data.details`
- `live_data.is_historical`
- `live_data.default_range`
- `live_data.range_options`

Important note from the fetched schema:
- `details` is intentionally flexible and its shape depends on `type`

This file is a local copy of the fetched page content used during this session.
