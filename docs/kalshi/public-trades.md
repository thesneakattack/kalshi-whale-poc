Source: https://docs.kalshi.com/websockets/public-trades.md

# Public Trades

Public trade notifications when trades occur.

Requirements surfaced in the fetch:
- no additional channel-level auth beyond the authenticated websocket connection
- market specification optional
- updates sent immediately after trade execution

## Message Shape

```json
{
  "type": "trade",
  "sid": 11,
  "msg": {
    "trade_id": "d91bc706-ee49-470d-82d8-11418bda6fed",
    "market_ticker": "HIGHNY-22DEC23-B53.5",
    "yes_price_dollars": "0.360",
    "no_price_dollars": "0.640",
    "count_fp": "136.00",
    "taker_side": "no",
    "taker_outcome_side": "no",
    "taker_book_side": "ask",
    "is_block_trade": false,
    "ts": 1669149841,
    "ts_ms": 1669149841000
  }
}
```

Fields surfaced in the fetched schema:
- `trade_id`
- `market_ticker`
- `yes_price_dollars`
- `no_price_dollars`
- `count_fp`
- `taker_side`
- `taker_outcome_side`
- `taker_book_side`
- `is_block_trade`
- `ts`
- `ts_ms`

Use case called out by the page:
- trade feed
- volume analysis

This file is a local copy of the fetched page content used during this session.
