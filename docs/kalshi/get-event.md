Source: https://docs.kalshi.com/api-reference/events/get-event.md

# Get Event

Endpoint for getting data about an event by ticker.

Fetched page summary:
- an event is a real-world occurrence such as an election, sports game, or economic indicator release
- events contain one or more markets
- all events are accessible through this endpoint even if associated markets are older than the historical cutoff

## Route

`GET /events/{event_ticker}`

Optional query param surfaced in the fetch:
- `with_nested_markets` (boolean)

## Response Shape

```yaml
GetEventResponse:
  event: EventData
  markets: [Market]
```

### EventData fields surfaced in the fetch
- `event_ticker`
- `series_ticker`
- `sub_title`
- `title`
- `collateral_return_type`
- `mutually_exclusive`
- `category` (deprecated)
- `strike_date`
- `strike_period`
- `available_on_brokers`
- `product_metadata`
- `settlement_sources`
- `last_updated_ts`
- `fee_type_override`
- `fee_multiplier_override`
- `exchange_index`

### Market fields surfaced in the fetch
- `ticker`
- `event_ticker`
- `market_type`
- `yes_sub_title`
- `no_sub_title`
- `created_time`
- `updated_time`
- `open_time`
- `close_time`
- `latest_expiration_time`
- `settlement_timer_seconds`
- `status`
- `yes_bid_dollars`
- `yes_ask_dollars`
- `no_bid_dollars`
- `no_ask_dollars`
- `yes_bid_size_fp`
- `yes_ask_size_fp`
- `last_price_dollars`
- `previous_yes_bid_dollars`
- `previous_yes_ask_dollars`
- `previous_price_dollars`
- `volume_fp`
- `volume_24h_fp`
- `open_interest_fp`
- `result`
- `can_close_early`
- `expiration_value`
- `occurrence_datetime`
- `strike_type`
- `floor_strike`
- `cap_strike`
- `functional_strike`
- `custom_strike`
- `rules_primary`
- `rules_secondary`
- `mve_collection_ticker`
- `mve_selected_legs`
- `primary_participant_key`
- `price_level_structure`
- `price_ranges`
- `is_provisional`
- `exchange_index`

This file is a local copy of the fetched page content used during this session.
