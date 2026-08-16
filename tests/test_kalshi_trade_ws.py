import asyncio
import json

from services.kalshi_trade_ws import KalshiTradeWebSocketClient


def _client():
    # No real credentials needed for _handle_message tests - that method
    # doesn't touch auth at all (only run()'s connection setup does).
    return KalshiTradeWebSocketClient("https://external-api.kalshi.com/trade-api/v2")


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


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


# --- _sync_subscriptions vs. the account-wide fill/market_positions sids
# (2026-08-16, live-confirmed incident: "the whale watching stream has
# halted completely, no signals at all" - see kalshi_trade_ws.py's own
# _market_channels_subscribed docstring for the full root-cause writeup) ----

def test_real_ticker_subscribe_not_swallowed_by_unrelated_fill_sids():
    # Reproduces the exact failure sequence: the connection's initial
    # force-subscribe fires before the trading loop's first
    # set_market_tickers() call (desired tickers still empty, so it
    # correctly no-ops), then fill/market_positions get their sids - which
    # used to be misread as "trade/ticker already subscribed" by the old
    # `not self._subscription_sids` check.
    client = _client()
    client._ws = _FakeWebSocket()
    client._subscription_sids = {"fill": 1, "market_positions": 2}
    client._market_channels_subscribed = False
    client._desired_tickers = {"TICK-A", "TICK-B"}

    asyncio.run(client._sync_subscriptions(force_subscribe=False))

    sent = client._ws.sent
    subscribe_cmds = [m for m in sent if m["cmd"] == "subscribe"]
    update_cmds = [m for m in sent if m["cmd"] == "update_subscription"]
    assert not update_cmds, "must not send update_subscription against unrelated fill/market_positions sids"
    channels_subscribed = {c for m in subscribe_cmds for c in m["params"]["channels"]}
    assert channels_subscribed == {"trade", "ticker"}
    for m in subscribe_cmds:
        assert m["params"]["market_tickers"] == ["TICK-A", "TICK-B"]
    assert client._market_channels_subscribed is True


def test_incremental_update_only_targets_trade_ticker_sids():
    # Once real trade/ticker sids exist alongside the unrelated fill/
    # market_positions ones, growing the watchlist must only ever send
    # update_subscription against the trade/ticker sids.
    client = _client()
    client._ws = _FakeWebSocket()
    client._subscription_sids = {"fill": 1, "market_positions": 2, "trade": 3, "ticker": 4}
    client._market_channels_subscribed = True
    client._subscribed_tickers = {"TICK-A"}
    client._desired_tickers = {"TICK-A", "TICK-B"}

    asyncio.run(client._sync_subscriptions(force_subscribe=False))

    sent = client._ws.sent
    assert all(m["cmd"] == "update_subscription" for m in sent)
    sids_used = {m["params"]["sid"] for m in sent}
    assert sids_used == {3, 4}
    for m in sent:
        assert m["params"]["market_tickers"] == ["TICK-B"]
        assert m["params"]["action"] == "add_markets"
