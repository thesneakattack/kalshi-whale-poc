import asyncio
import json
import logging

from services.kalshi.websocket import KalshiStreamGateway


def _client():
    # No real credentials needed for _handle_message tests - that method
    # doesn't touch auth at all (only run()'s connection setup does).
    return KalshiStreamGateway("https://external-api.kalshi.com/trade-api/v2")


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


def test_market_position_message_dispatches_to_on_position_callback():
    # 2026-08-24 correction (QCP Task 13): the real per-message `type` is
    # "market_position" SINGULAR (docs/kalshi/market-positions.md's own
    # schema - `const: market_position`), not the plural channel name this
    # test used to assert against. That old fixture matched a real bug in
    # _handle_message's own dispatch (fixed the same day) rather than the
    # real wire format - see test_kalshi_contracts.py for the fixture-file
    # version of this same coverage.
    client = _client()
    received = []

    async def on_position(msg):
        received.append(msg)

    raw = json.dumps({"type": "market_position", "msg": {"ticker": "TICK-A", "position_fp": "5"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_position=on_position))

    assert received == [{"ticker": "TICK-A", "position_fp": "5"}]


def test_market_positions_plural_type_no_longer_dispatches():
    # Regression guard for the bug test_market_position_message_dispatches_
    # to_on_position_callback's fix corrected - the plural channel name is
    # never a real per-message `type` value, so it must not match.
    client = _client()
    received = []

    async def on_position(msg):
        received.append(msg)

    raw = json.dumps({"type": "market_positions", "msg": {"ticker": "TICK-A", "position_fp": "5"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_position=on_position))

    assert received == []


def test_fill_message_is_a_noop_when_no_callback_given():
    client = _client()
    raw = json.dumps({"type": "fill", "msg": {"ticker": "TICK-A"}})
    # Must not raise even though on_fill is omitted entirely.
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None))


def test_market_position_message_is_a_noop_when_no_callback_given():
    client = _client()
    raw = json.dumps({"type": "market_position", "msg": {"ticker": "TICK-A"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None))


def test_fill_shape_is_logged_only_once(caplog):
    caplog.set_level(logging.INFO)
    client = _client()

    async def on_fill(msg):
        pass

    raw = json.dumps({"type": "fill", "msg": {"ticker": "TICK-A"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_fill=on_fill))
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_fill=on_fill))

    assert caplog.text.count("first real 'fill' message shape") == 1


def test_position_shape_is_logged_only_once(caplog):
    caplog.set_level(logging.INFO)
    client = _client()

    async def on_position(msg):
        pass

    raw = json.dumps({"type": "market_position", "msg": {"ticker": "TICK-A"}})
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_position=on_position))
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_position=on_position))

    assert caplog.text.count("first real 'market_position' message shape") == 1


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
# _trade_subscribed/_ticker_subscribed docstring for the full writeup) ----

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
    client._trade_subscribed = False
    client._ticker_subscribed = False
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
    assert client._trade_subscribed is True
    assert client._ticker_subscribed is True


def test_incremental_update_only_targets_trade_ticker_sids():
    # Once real trade/ticker sids exist alongside the unrelated fill/
    # market_positions ones, growing the watchlist must only ever send
    # update_subscription against the trade/ticker sids.
    client = _client()
    client._ws = _FakeWebSocket()
    client._subscription_sids = {"fill": 1, "market_positions": 2, "trade": 3, "ticker": 4}
    client._trade_subscribed = True
    client._ticker_subscribed = True
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


# --- exchange-wide trade subscription (2026-08-17, "realtime data across
# everything"). docs/kalshi/public-trades.md: "market specification
# optional" - omitting market_tickers streams every trade on the exchange,
# which is the fix for the measured ~98% coverage loss. --------------------

def _wide_client():
    return KalshiStreamGateway(
        "https://external-api.kalshi.com/trade-api/v2", exchange_wide_trades=True,
    )


def test_exchange_wide_trade_subscribe_omits_market_tickers():
    client = _wide_client()
    client._ws = _FakeWebSocket()
    client._desired_tickers = {"TICK-A", "TICK-B"}

    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    trade = [m for m in client._ws.sent if m["params"]["channels"] == ["trade"]]
    ticker = [m for m in client._ws.sent if m["params"]["channels"] == ["ticker"]]
    assert len(trade) == 1
    # The whole point: no market list at all on the trade channel.
    assert "market_tickers" not in trade[0]["params"]
    # ticker stays scoped - exchange-wide price updates would be a firehose
    # for data this app only needs on markets it might actually trade.
    assert ticker[0]["params"]["market_tickers"] == ["TICK-A", "TICK-B"]


def test_exchange_wide_trade_subscribes_before_any_watchlist_exists():
    """The scoped path deliberately no-ops with an empty desired set (a
    per-ticker subscribe with no tickers is meaningless). Exchange-wide has
    no such dependency, and must not inherit that wait - the raw websocket
    handshake always beats the REST market-discovery pipeline, so waiting
    would cost real prints on every single connect."""
    client = _wide_client()
    client._ws = _FakeWebSocket()
    client._desired_tickers = set()

    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    channels = [c for m in client._ws.sent for c in m["params"]["channels"]]
    assert channels == ["trade"]
    assert client._trade_subscribed is True
    assert client._ticker_subscribed is False


def test_trade_channel_is_not_resubscribed_when_the_watchlist_arrives_later():
    """The regression the two flags exist to prevent: with one shared
    flag, the ticker subscribe that arrives with the first watchlist would
    drag a second trade subscribe along with it - two exchange-wide
    firehoses on one connection."""
    client = _wide_client()
    client._ws = _FakeWebSocket()
    client._desired_tickers = set()
    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    client._desired_tickers = {"TICK-A"}
    asyncio.run(client._sync_subscriptions())

    trade_subs = [m for m in client._ws.sent
                  if m["cmd"] == "subscribe" and m["params"]["channels"] == ["trade"]]
    assert len(trade_subs) == 1
    assert client._ticker_subscribed is True


def test_exchange_wide_never_sends_add_markets_against_the_trade_sid():
    """update_subscription has no documented action for switching a
    subscription between scoped and unscoped (websocket-connection.md lists
    only add_markets/delete_markets/get_snapshot), so an add_markets against
    the exchange-wide trade sid would at best error and at worst silently
    narrow the firehose back down to a watchlist."""
    client = _wide_client()
    client._ws = _FakeWebSocket()
    client._subscription_sids = {"trade": 3, "ticker": 4}
    client._trade_subscribed = True
    client._ticker_subscribed = True
    client._subscribed_tickers = {"TICK-A"}
    client._desired_tickers = {"TICK-A", "TICK-B"}

    asyncio.run(client._sync_subscriptions())

    sids_used = {m["params"]["sid"] for m in client._ws.sent}
    assert sids_used == {4}, "only the ticker sid may take market_tickers updates"


def test_scoped_mode_is_unchanged_by_the_new_flag():
    client = _client()
    client._ws = _FakeWebSocket()
    client._desired_tickers = {"TICK-A"}

    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    for m in client._ws.sent:
        assert m["params"]["market_tickers"] == ["TICK-A"]


def test_normalize_trade_preserves_fields_it_does_not_know_about():
    """Direct, repeated instruction (2026-08-17): stop shaving fields off
    the shapes Kalshi sends.

    This mattered more than it looked: normalize_trade runs UPSTREAM of
    series_watcher.record_trade, so the raw_json column added specifically
    to preserve unknown fields was, for every websocket trade, storing an
    already-shaved dict rather than the real payload."""
    msg = {
        "trade_id": "t1", "market_ticker": "TICK-A",
        "yes_price_dollars": "0.61", "no_price_dollars": "0.39",
        "count_fp": "100.00", "taker_outcome_side": "yes",
        "taker_book_side": "bid", "is_block_trade": False,
        "ts": 1, "ts_ms": 1000,
        # Neither of these has ever existed in this app's schema.
        "some_new_kalshi_field": "keep me",
        "price_level_structure": "deci_cent",
    }
    out = KalshiStreamGateway.normalize_trade(msg)

    assert out["some_new_kalshi_field"] == "keep me"
    assert out["price_level_structure"] == "deci_cent"
    # The original key survives alongside the normalised alias.
    assert out["market_ticker"] == "TICK-A"
    assert out["ticker"] == "TICK-A"
    # Normalisation still wins where the two overlap.
    assert out["taker_side"] == "yes"
    assert out["created_time"] == "1970-01-01T00:00:01Z"


# --- market_lifecycle_v2 (2026-08-17, docs/next-session-pickup-2026-08-17.md
# item #2 of the REST-vs-websocket architecture finding) -------------------

def _lifecycle_client():
    return KalshiStreamGateway(
        "https://external-api.kalshi.com/trade-api/v2", subscribe_lifecycle=True,
    )


def test_lifecycle_message_dispatches_to_on_lifecycle_callback():
    client = _client()
    received = []

    async def on_lifecycle(msg):
        received.append(msg)

    raw = json.dumps({
        "type": "market_lifecycle_v2",
        "msg": {"event_type": "close_date_updated", "market_ticker": "TICK-A", "close_ts": 123},
    })
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None, on_lifecycle=on_lifecycle))

    # A10: the callback receives the boundary-normalized message - raw
    # fields intact plus the canonical `ticker` alias overlay.
    assert received == [{"event_type": "close_date_updated", "market_ticker": "TICK-A", "close_ts": 123, "ticker": "TICK-A"}]


def test_lifecycle_message_is_a_noop_when_no_callback_given():
    client = _client()
    raw = json.dumps({"type": "market_lifecycle_v2", "msg": {"event_type": "created", "market_ticker": "TICK-A"}})
    # Must not raise even though on_lifecycle is omitted entirely.
    asyncio.run(client._handle_message(raw, on_trade=None, on_ticker=None, on_status=None))


def test_lifecycle_shape_is_logged_once_per_event_type(caplog):
    caplog.set_level(logging.INFO)
    client = _client()

    async def on_lifecycle(msg):
        pass

    created = json.dumps({"type": "market_lifecycle_v2", "msg": {"event_type": "created", "market_ticker": "TICK-A"}})
    settled = json.dumps({"type": "market_lifecycle_v2", "msg": {"event_type": "settled", "market_ticker": "TICK-A"}})
    asyncio.run(client._handle_message(created, on_trade=None, on_ticker=None, on_status=None, on_lifecycle=on_lifecycle))
    asyncio.run(client._handle_message(created, on_trade=None, on_ticker=None, on_status=None, on_lifecycle=on_lifecycle))
    asyncio.run(client._handle_message(settled, on_trade=None, on_ticker=None, on_status=None, on_lifecycle=on_lifecycle))

    # 'created' logged exactly once despite two messages...
    assert caplog.text.count("first real 'market_lifecycle_v2' 'created' shape") == 1
    # ...but 'settled' gets its own first-time log, since each event_type
    # is a genuinely different shape (docs/kalshi/market-and-event-
    # lifecycle.md - most fields are conditional on which event_type this is).
    assert caplog.text.count("first real 'market_lifecycle_v2' 'settled' shape") == 1


def test_lifecycle_subscribe_is_opt_in_and_exchange_wide():
    client = _lifecycle_client()
    client._ws = _FakeWebSocket()
    client._desired_tickers = set()  # no watchlist at all - must not block this

    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    lifecycle_subs = [m for m in client._ws.sent if m["params"]["channels"] == ["market_lifecycle_v2"]]
    assert len(lifecycle_subs) == 1
    assert "market_tickers" not in lifecycle_subs[0]["params"]
    assert client._lifecycle_subscribed is True


def test_lifecycle_subscribe_is_off_by_default():
    client = _client()  # subscribe_lifecycle defaults False
    client._ws = _FakeWebSocket()

    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    assert all(m["params"]["channels"] != ["market_lifecycle_v2"] for m in client._ws.sent)


def test_lifecycle_not_resubscribed_on_a_later_sync():
    client = _lifecycle_client()
    client._ws = _FakeWebSocket()
    asyncio.run(client._sync_subscriptions(force_subscribe=True))

    client._desired_tickers = {"TICK-A"}
    asyncio.run(client._sync_subscriptions())

    lifecycle_subs = [m for m in client._ws.sent if m["params"]["channels"] == ["market_lifecycle_v2"]]
    assert len(lifecycle_subs) == 1


# ---- A11: stream gateway behind the integration boundary -------------------


# (test_ws_client_is_a_compatibility_facade_over_the_boundary_gateway
# retired at C8 with the facade itself - services/kalshi_trade_ws.py is
# deleted; the import-path-stays-dead guarantee lives in
# tests/test_kalshi_public_gateway.py's
# test_legacy_facade_import_paths_are_gone.)
