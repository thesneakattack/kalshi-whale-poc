Source: https://docs.kalshi.com/websockets/market-ticker.md

# Market Ticker

Market price, volume, and open interest updates.

Requirements surfaced in the fetch:
- no additional channel-level auth beyond the authenticated websocket connection
- market specification optional
- supports `market_ticker` / `market_tickers` and `market_id` / `market_ids`
- updates sent whenever any ticker field changes

## Message Shape

```json
{
  "type": "ticker",
  "sid": 11,
  "msg": {
    "market_ticker": "FED-23DEC-T3.00",
    "market_id": "9b0f6b43-5b68-4f9f-9f02-9a2d1b8ac1a1",
    "price_dollars": "0.480",
    "yes_bid_dollars": "0.450",
    "yes_ask_dollars": "0.530",
    "volume_fp": "33896.00",
    "open_interest_fp": "20422.00",
    "dollar_volume": 16948,
    "dollar_open_interest": 10211,
    "yes_bid_size_fp": "300.00",
    "yes_ask_size_fp": "150.00",
    "last_trade_size_fp": "25.00",
    "ts": 1669149841,
    "ts_ms": 1669149841000,
    "time": "2022-11-22T20:44:01Z"
  }
}
```

Fields surfaced in the fetched schema:
- `market_ticker`
- `market_id`
- `price_dollars`
- `yes_bid_dollars`
- `yes_ask_dollars`
- `volume_fp`
- `open_interest_fp`
- `dollar_volume`
- `dollar_open_interest`
- `yes_bid_size_fp`
- `yes_ask_size_fp`
- `last_trade_size_fp`
- `ts`
- `ts_ms`
- `time`

Use case called out by the page:
- displaying current market prices and statistics

This file is a local copy of the fetched page content used during this session.
