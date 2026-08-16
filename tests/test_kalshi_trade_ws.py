import asyncio
import json

from services.kalshi_trade_ws import KalshiTradeWebSocketClient


def _client():
    # No real credentials needed for _handle_message tests - that method
    # doesn't touch auth at all (only run()'s connection setup does).
    return KalshiTradeWebSocketClient("https://external-api.kalshi.com/trade-api/v2")


# --- fill/market_positions dispatch (2026-08-15 direct request: "the open
# positions should feed from the websocket stream") ------------------------

def test_fill_message_dispatches_to_on_fill_callback():
    client = _client()
    received = []

    async def on_fill(msg):
        received.append(msg)

    raw = json.dumps({"type": "fill", "msg": {"ticker": "TICK-A", "count_fp": "10"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_fill=on_fill))

    assert received == [{"ticker": "TICK-A", "count_fp": "10"}]


def test_market_positions_message_dispatches_to_on_position_callback():
    client = _client()
    received = []

    async def on_position(msg):
        received.append(msg)

    raw = json.dumps({"type": "market_positions", "msg": {"ticker": "TICK-A", "position_fp": "5"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_position=on_position))

    assert received == [{"ticker": "TICK-A", "position_fp": "5"}]


def test_fill_message_is_a_noop_when_no_callback_given():
    client = _client()
    raw = json.dumps({"type": "fill", "msg": {"ticker": "TICK-A"}})
    # Must not raise even though on_fill is omitted entirely.
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None))


def test_market_positions_message_is_a_noop_when_no_callback_given():
    client = _client()
    raw = json.dumps({"type": "market_positions", "msg": {"ticker": "TICK-A"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None))


def test_fill_shape_is_logged_only_once(capsys):
    client = _client()

    async def on_fill(msg):
        pass

    raw = json.dumps({"type": "fill", "msg": {"ticker": "TICK-A"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_fill=on_fill))
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_fill=on_fill))

    out = capsys.readouterr().out
    assert out.count("first real 'fill' message shape") == 1


def test_position_shape_is_logged_only_once(capsys):
    client = _client()

    async def on_position(msg):
        pass

    raw = json.dumps({"type": "market_positions", "msg": {"ticker": "TICK-A"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_position=on_position))
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_position=on_position))

    out = capsys.readouterr().out
    assert out.count("first real 'market_positions' message shape") == 1


def test_existing_trade_and_ticker_dispatch_still_work_with_new_optional_params():
    # Backward-compat check - every pre-existing call site omits
    # on_fill/on_position entirely.
    client = _client()
    trades, tickers = [], []

    async def on_trade(t):
        trades.append(t)

    async def on_ticker(t):
        tickers.append(t)

    trade_raw = json.dumps({"type": "trade", "msg": {"market_ticker": "TICK-A", "trade_id": "1"}})
    ticker_raw = json.dumps({"type": "ticker", "msg": {"market_ticker": "TICK-A"}})
    asyncio.run(client._handle_message(trade_raw, on_trade, on_ticker, None))
    asyncio.run(client._handle_message(ticker_raw, on_trade, on_ticker, None))

    assert len(trades) == 1
    assert len(tickers) == 1
