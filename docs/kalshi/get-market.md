Source: https://docs.kalshi.com/api-reference/market/get-market.md

# Get Market

Endpoint for getting data about a specific market by ticker.

Fetched page summary:
- a market is a specific binary outcome within an event
- markets expose current prices, volume, and settlement rules

## Route

`GET /markets/{ticker}`

## Response Shape

```yaml
GetMarketResponse:
  market: Market
```

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
- `fee_waiver_expiration_time`
- `early_close_condition`
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
