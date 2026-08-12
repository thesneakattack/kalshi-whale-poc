Source: https://docs.kalshi.com/getting_started/quick_start_websockets.md

# Quick Start: WebSockets

## Overview

Kalshi's WebSocket API provides real-time updates for:
- Order book changes
- Trade executions
- Market status updates
- Fill notifications

## Connection URL

Production:
`wss://external-api-ws.kalshi.com/trade-api/ws/v2`

Demo:
`wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`

Shared hosts remain supported:
- `wss://api.elections.kalshi.com/trade-api/ws/v2`
- `wss://demo-api.kalshi.co/trade-api/ws/v2`

## Authentication

Required handshake headers:

```http
KALSHI-ACCESS-KEY: your_api_key_id
KALSHI-ACCESS-SIGNATURE: request_signature
KALSHI-ACCESS-TIMESTAMP: unix_timestamp_in_milliseconds
```

Signature input:

```text
timestamp + "GET" + "/trade-api/ws/v2"
```

Even public-market channels use an authenticated websocket session.

Private channels:
- `orderbook_delta`
- `fill`
- `market_positions`
- `communications`
- `order_group_updates`

Public market-data channels:
- `ticker`
- `trade`
- `market_lifecycle_v2`
- `multivariate_market_lifecycle`
- `multivariate`

## Establishing a Connection

Python example from the fetched page:

```python
import websockets
import asyncio

ws_url = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
auth_headers = {
    "KALSHI-ACCESS-KEY": "your_api_key_id",
    "KALSHI-ACCESS-SIGNATURE": "generated_signature",
    "KALSHI-ACCESS-TIMESTAMP": "timestamp_in_milliseconds"
}

async def connect():
    async with websockets.connect(ws_url, additional_headers=auth_headers) as websocket:
        print("Connected to Kalshi WebSocket")
        async for message in websocket:
            print(f"Received: {message}")

asyncio.run(connect())
```

## Subscribing to Data

```python
subscription = {
    "id": 1,
    "cmd": "subscribe",
    "params": {
        "channels": ["ticker"]
    }
}
```

Specific markets:

```python
subscription_message = {
    "id": self.message_id,
    "cmd": "subscribe",
    "params": {
        "channels": channels,
        "market_tickers": market_tickers
    }
}
```

Example:
- Subscribe to orderbook updates on selected markets
- Subscribe to trade feed on selected markets

## Processing Messages

Examples shown in fetched content:
- `ticker`
- `orderbook_snapshot`
- `orderbook_delta`
- `error`

## Keep-Alive

The fetched page notes that the Python `websockets` library handles ping/pong automatically.

## Connection Lifecycle

1. Initial connection
2. Subscribe
3. Receive updates
4. Reconnect with exponential backoff

## Error Handling

Documented websocket error codes surfaced in the fetch include:
- `1` Unable to process message
- `2` Params required
- `3` Channels required
- `4` Subscription IDs required
- `5` Unknown command
- `6` Already subscribed
- `7` Unknown subscription ID
- `8` Unknown channel name
- `9` Authentication required
- `10` Channel error
- `11` Invalid parameter
- `12` Exactly one subscription ID is required
- `13` Unsupported action
- `14` Market Ticker required
- `15` Action required
- `16` Market not found
- `17` Internal error
- `18` Command timeout
- `19-22` communications shard errors
- `25` Subscription buffer overflow

## Best Practices

The fetched page emphasized:
- automatic reconnection with exponential backoff
- asynchronous message processing
- secure key handling
- subscribing only to markets you need
- buffering for high-frequency updates

## Complete Example

The page included a complete Python example using `websockets`, RSA-PSS signing, and a subscribe call for `orderbook_delta`.

This file is a session-local copy of the fetched page content, condensed to the material surfaced by the tool.
