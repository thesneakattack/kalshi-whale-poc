Source: https://docs.kalshi.com/websockets/websocket-connection.md

# WebSocket Connection

Main websocket connection endpoint. Authentication is required during the handshake, even for public market-data channels.

## Subscribe Command

Structure surfaced during the fetch:

```json
{
  "id": 1,
  "cmd": "subscribe",
  "params": {
    "channels": ["orderbook_delta"],
    "market_ticker": "CPI-22DEC-TN0.1"
  }
}
```

Supported channels seen in the fetched schema:
- `orderbook_delta`
- `ticker`
- `trade`
- `fill`
- `market_positions`
- `market_lifecycle_v2`
- `multivariate_market_lifecycle`
- `communications`
- `order_group_updates`
- `user_orders`
- `cfbenchmarks_value`
- `pyth_value`

Optional subscribe params surfaced in the fetch:
- `market_ticker`
- `market_tickers`
- `market_id`
- `market_ids`
- `send_initial_snapshot`
- `skip_ticker_ack`
- `use_yes_price`
- `shard_factor`
- `shard_key`
- `index_ids`
- `underlying_tickers`

## Unsubscribe Command

```json
{
  "id": 124,
  "cmd": "unsubscribe",
  "params": {
    "sids": [1, 2]
  }
}
```

## List Subscriptions

```json
{
  "id": 3,
  "cmd": "list_subscriptions"
}
```

## Update Subscription

Supported actions surfaced in the fetch:
- `add_markets`
- `delete_markets`
- `get_snapshot`

Example:

```json
{
  "id": 124,
  "cmd": "update_subscription",
  "params": {
    "sid": 456,
    "market_tickers": ["NEW-MARKET-1", "NEW-MARKET-2"],
    "action": "add_markets"
  }
}
```

CF Benchmarks actions surfaced:
- `subscribe_indices`
- `unsubscribe_indices`
- `indexlist`

Pyth actions surfaced:
- `subscribe_underlyings`
- `unsubscribe_underlyings`
- `underlying_list`

## Responses

Fetched response types:
- `subscribed`
- `unsubscribed`
- `ok`
- `error`

Examples surfaced:

```json
{
  "id": 1,
  "type": "subscribed",
  "msg": { "channel": "orderbook_delta", "sid": 1 }
}
```

```json
{
  "id": 123,
  "sid": 456,
  "seq": 222,
  "type": "ok",
  "msg": { "market_tickers": ["MARKET-1", "MARKET-2", "MARKET-3"] }
}
```

## Error Codes

The fetched schema listed codes `1` through `28`, including:
- malformed message / missing params / missing channels
- authentication required
- invalid market ticker or market id
- command timeout
- subscription buffer overflow
- subscription market limit exceeded
- too many requests

## Notes

The fetched page explicitly notes:
- all communication happens through one websocket connection
- sequence numbers matter for consistency
- `use_yes_price` on orderbook updates is part of a migration path toward unified yes-leg pricing

This file is a local copy of the fetched schema/context used during this session.
