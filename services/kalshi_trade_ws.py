import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone

import websockets

logger = logging.getLogger(__name__)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

_PROD_WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
_DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"

# Bound on the reader->worker handoff queue (see run()). Sized to absorb a
# real burst without becoming an unbounded memory sink: at exchange-wide
# trade volume this is a few seconds of backlog. Overflow increments
# dropped_messages rather than blocking the reader, because blocking the
# reader is exactly the failure this split exists to prevent.
_INGEST_QUEUE_MAX = 20000


class KalshiTradeWebSocketClient:
    def __init__(self, base_url: str, exchange_wide_trades: bool = False,
                 index_ids: list[str] | None = None,
                 underlying_tickers: list[str] | None = None,
                 subscribe_lifecycle: bool = False):
        # exchange_wide_trades (2026-08-17, direct goal: "realtime data
        # across everything" / "zero latency and maximum insight"):
        # subscribe the `trade` channel with NO market_tickers, which
        # docs/kalshi/public-trades.md explicitly permits ("market
        # specification optional"), so every trade on the exchange arrives
        # instead of only those on this app's own rotating watchlist.
        #
        # That watchlist scoping was the single largest measured gap in the
        # system: a print on an unwatched market was never received at all -
        # not filtered, not logged, not counted as rejected - and 425
        # markets were observed trading in a 30-second window against 15
        # watched, with 5 of 5 whale prints >=$2,500 invisible.
        #
        # The `ticker` channel deliberately stays scoped to the desired set
        # even in this mode. market-ticker.md permits going wide there too,
        # but a price update on every market on the exchange fires on every
        # field change and would be a genuine firehose, for data this app
        # only needs on markets it might actually trade.
        self.exchange_wide_trades = exchange_wide_trades
        # market_lifecycle_v2 (2026-08-17, REST-vs-websocket architecture
        # finding, docs/next-session-pickup-2026-08-17.md item #2): push-
        # based open/close/settlement notifications, replacing the 6-second
        # REST poll's own close_time/result checks for the specific case of
        # a close date getting revised ahead of schedule - see
        # main.py._process_stream_lifecycle. docs/kalshi/market-and-event-
        # lifecycle.md is explicit that "market_ticker filters are not
        # supported" - like exchange-wide trade, there is no way to scope
        # this to a watchlist, so it's a constructor-level opt-in (default
        # off) rather than always-on, kept OFF the dedicated index_stream
        # connection specifically to preserve that connection's physical
        # isolation from any other channel's volume (see app_state.py).
        self.subscribe_lifecycle = subscribe_lifecycle
        # CF Benchmarks index IDs to stream (docs/kalshi/cfbenchmarks-value.md).
        # This channel is what several crypto series literally settle
        # against - KXBTC15M's own rules_primary, read live 2026-08-17:
        # "the simple average of the sixty seconds of CF Benchmarks' BRTI
        # before <close>" - so its last_60s_windowed_average_15min is not a
        # prediction of the outcome, it IS the outcome, accumulating one
        # observation per second. Empty list disables the subscription.
        #
        # NOTE the channel takes index_ids, NOT market_tickers - the page is
        # explicit that "market_ticker/market_tickers/market_id/market_ids
        # are not supported for this channel".
        self.index_ids = list(index_ids or [])
        # Pyth underlyings (docs/kalshi/pyth-value.md) - same shape, different
        # param name (underlying_tickers) and no windowed averages.
        self.underlying_tickers = list(underlying_tickers or [])
        self.base_url = (base_url or "").strip().lower()
        self.key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
        self.private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
        self.ws_url = _DEMO_WS_URL if "demo.kalshi" in self.base_url else _PROD_WS_URL
        self._private_key = None
        self._load_error = None
        self._ws = None
        self._lock = asyncio.Lock()
        self._desired_tickers: set[str] = set()
        self._subscribed_tickers: set[str] = set()
        self._subscription_sids: dict[str, int] = {}
        # Tracks specifically whether THIS connection has ever sent the
        # initial trade/ticker subscribe - kept separate from
        # _subscription_sids, which also accumulates the unrelated
        # account-wide fill/market_positions sids (see run()'s own subscribe
        # for those). Real, live-confirmed bug (2026-08-16, direct report:
        # "the whale watching stream has halted completely, no signals at
        # all"): using `not self._subscription_sids` to mean "no real
        # per-ticker subscribe sent yet" broke the moment fill/
        # market_positions started populating that same dict - once those
        # two sids landed, the check went false even though trade/ticker had
        # never actually been subscribed (their own force-subscribe attempt
        # fired before the trading loop's first set_market_tickers() call,
        # when desired tickers were still empty, so it no-opped). Every
        # later real ticker-set update then got misrouted through the
        # incremental to_add/to_remove path, sending update_subscription
        # against the fill/market_positions sids instead of ever subscribing
        # trade/ticker - deterministic on every fresh connect, since the raw
        # WS handshake always wins the race against the REST-based
        # market-discovery pipeline that feeds set_market_tickers().
        #
        # Split into one flag per channel 2026-08-17 (see
        # _sync_subscriptions): exchange-wide trade goes up on connect with
        # no watchlist, while ticker still waits for one, so a single shared
        # flag would either duplicate the trade subscription or block it.
        self._trade_subscribed = False
        self._ticker_subscribed = False
        self._index_subscribed = False
        self._lifecycle_subscribed = False
        self._message_id = 1
        self._update_event = asyncio.Event()
        self._stop = False
        self._logged_fill_shape = False
        self._logged_position_shape = False
        self._logged_lifecycle_event_types: set[str] = set()
        # Ingest counters - exposed through status so a stalled consumer
        # shows up as a number rather than as a market that looks quiet.
        self.messages_received = 0
        self.dropped_messages = 0
        if self.key_id and self.private_key_path:
            try:
                with open(self.private_key_path, "rb") as f:
                    self._private_key = serialization.load_pem_private_key(f.read(), password=None)
            except Exception as exc:
                self._load_error = str(exc)

    @property
    def enabled(self) -> bool:
        return self._private_key is not None and bool(self.key_id)

    @property
    def status(self) -> dict:
        if self.enabled:
            return {"enabled": True, "error": None, "ws_url": self.ws_url}
        if self.key_id or self.private_key_path:
            return {"enabled": False, "error": self._load_error or "incomplete websocket credentials", "ws_url": self.ws_url}
        return {"enabled": False, "error": None, "ws_url": self.ws_url}

    async def close(self) -> None:
        self._stop = True
        self._update_event.set()
        async with self._lock:
            ws = self._ws
        if ws is not None:
            await ws.close()

    async def set_market_tickers(self, tickers: list[str]) -> None:
        normalized = {t for t in tickers if t}
        if normalized == self._desired_tickers:
            return
        self._desired_tickers = normalized
        self._update_event.set()

    async def request_index_list(self) -> None:
        """Ask the server which CF Benchmarks index IDs actually exist
        (docs/kalshi/cfbenchmarks-value.md's `indexlist` action, which
        "returns the available index IDs ... without modifying the
        subscription"). The reply arrives as a cfbenchmarks_value_indexlist
        message.

        Worth having as a real call rather than a hardcoded list: a live
        sweep of settlement rules on 2026-08-17 found the SAME index named
        three different ways across series - "BRTI", "ETHUSDRTI" (no
        underscore) and "ERTI" - none of which is guaranteed to be the
        channel's own identifier. Asking is the only way to know."""
        sid = self._subscription_sids.get("cfbenchmarks_value")
        if sid is None:
            return
        await self._send({
            "id": self._next_message_id(),
            "cmd": "update_subscription",
            "params": {"sid": sid, "action": "indexlist"},
        })

    async def run(self, on_trade, on_ticker, on_status=None, on_fill=None, on_position=None,
                  on_index=None, on_lifecycle=None) -> None:
        backoff = 1.0
        while not self._stop:
            if not self.enabled:
                if on_status is not None:
                    await on_status({"connected": False, **self.status})
                await asyncio.sleep(5)
                continue
            try:
                headers = self._auth_headers()
                async with websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=1024,
                ) as websocket:
                    async with self._lock:
                        self._ws = websocket
                        self._subscription_sids = {}
                        self._subscribed_tickers = set()
                        self._trade_subscribed = False
                        self._ticker_subscribed = False
                        self._lifecycle_subscribed = False
                        self._message_id = 1
                    if on_status is not None:
                        await on_status({"connected": True, "error": None, "ws_url": self.ws_url})
                    await self._sync_subscriptions(force_subscribe=True)
                    if on_fill is not None or on_position is not None:
                        # Account-wide channels (2026-08-15 direct request: "the
                        # open positions should feed from the websocket
                        # stream") - deliberately NOT part of _sync_subscriptions
                        # above, which is entirely about the per-ticker desired
                        # set (trade/ticker channels only make sense scoped to
                        # specific markets; fill/market_positions are inherently
                        # scoped to this authenticated account as a whole, same
                        # credentials already used for trade/ticker - confirmed
                        # via docs/kalshi/websocket-connection.md's channel
                        # list, no market_ticker param involved). Subscribed
                        # once per connection here, never touched by
                        # _sync_subscriptions' add/remove-markets logic.
                        await self._send({
                            "id": self._next_message_id(),
                            "cmd": "subscribe",
                            "params": {"channels": ["fill", "market_positions"]},
                        })
                    backoff = 1.0
                    # Ingest and processing are separate tasks (2026-08-17).
                    #
                    # They used to be one loop: receive a message, then await
                    # its full handler before receiving the next. That was
                    # survivable while the trade channel was scoped to a
                    # ~150-market watchlist, and stopped being survivable the
                    # moment it went exchange-wide. Every trade message costs
                    # a thread hop (whale_provider.fetch_signals ->
                    # asyncio.to_thread) plus DB reads, so at exchange volume
                    # the socket was only drained as fast as the slowest
                    # handler - and the ~1/second index ticks, which decide
                    # settlement, ended up queued behind thousands of trades.
                    #
                    # Confirmed rather than theorised: a standalone
                    # connection subscribed only to cfbenchmarks_value
                    # received exactly 50 messages in 50 seconds (the
                    # documented 1/sec), while the same channel on the shared
                    # connection delivered ~20 and then appeared frozen for
                    # minutes.
                    #
                    # Now the reader does nothing but recv and enqueue, which
                    # is microseconds, and a worker drains the queue. The
                    # queue is BOUNDED and overflow is counted rather than
                    # silently absorbed - a stalled consumer must be visible
                    # (see self.dropped_messages), not disguised as a quiet
                    # market.
                    queue: asyncio.Queue = asyncio.Queue(maxsize=_INGEST_QUEUE_MAX)

                    async def _consume():
                        while True:
                            raw = await queue.get()
                            try:
                                await self._handle_message(
                                    raw, on_trade, on_ticker, on_status, on_fill,
                                    on_position, on_index, on_lifecycle,
                                )
                            except Exception:
                                # One malformed or mishandled message must not
                                # tear down the socket - the reader is still
                                # draining behind this.
                                pass
                            finally:
                                queue.task_done()

                    consumer = asyncio.create_task(_consume())
                    try:
                        while not self._stop:
                            update_task = asyncio.create_task(self._update_event.wait())
                            recv_task = asyncio.create_task(websocket.recv())
                            done, pending = await asyncio.wait(
                                {update_task, recv_task}, return_when=asyncio.FIRST_COMPLETED,
                            )
                            for task in pending:
                                task.cancel()
                            if update_task in done and self._update_event.is_set():
                                self._update_event.clear()
                                await self._sync_subscriptions()
                            if recv_task in done:
                                raw_message = recv_task.result()
                                self.messages_received += 1
                                try:
                                    queue.put_nowait(raw_message)
                                except asyncio.QueueFull:
                                    self.dropped_messages += 1
                    finally:
                        consumer.cancel()
            except Exception as exc:
                if on_status is not None:
                    await on_status({"connected": False, "error": str(exc), "ws_url": self.ws_url})
                async with self._lock:
                    self._ws = None
                    self._subscription_sids = {}
                    self._subscribed_tickers = set()
                    self._trade_subscribed = False
                    self._ticker_subscribed = False
                    self._lifecycle_subscribed = False
                if self._stop:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_message(self, raw_message: str, on_trade, on_ticker, on_status, on_fill=None, on_position=None, on_index=None, on_lifecycle=None) -> None:
        data = json.loads(raw_message)
        msg_type = data.get("type")
        if msg_type == "subscribed":
            msg = data.get("msg") or {}
            channel = msg.get("channel")
            sid = msg.get("sid")
            if channel and sid:
                self._subscription_sids[channel] = sid
            return
        if msg_type == "ok":
            return
        if msg_type == "error":
            if on_status is not None:
                err = data.get("msg") or {}
                await on_status({"connected": True, "error": f"Kalshi WS error {err.get('code')}: {err.get('msg')}", "ws_url": self.ws_url})
            return
        # Index feeds (docs/kalshi/cfbenchmarks-value.md, pyth-value.md).
        # Dispatched with the message type attached because the two payloads
        # are genuinely different shapes - cfbenchmarks carries the windowed
        # settlement averages, pyth is a bare price - and the handler has to
        # tell them apart rather than duck-type its way through.
        if msg_type in ("cfbenchmarks_value", "pyth_value"):
            if on_index is not None:
                await on_index(msg_type, data.get("msg") or {})
            return
        if msg_type in ("cfbenchmarks_value_indexlist", "pyth_value_underlying_list"):
            # Discovery reply - see request_index_list(). Printed rather than
            # routed anywhere, because its whole purpose is telling a human
            # which identifiers are real (a live sweep found the same index
            # named BRTI / ETHUSDRTI / ERTI across different series' rules).
            logger.info("%s: %r", msg_type, data.get('msg'))
            return
        if msg_type == "trade":
            await on_trade(self.normalize_trade(data.get("msg") or {}))
            return
        if msg_type == "market_lifecycle_v2":
            # docs/kalshi/market-and-event-lifecycle.md's msg.event_type
            # names which of created/activated/deactivated/close_date_
            # updated/determined/settled/price_level_structure_updated/
            # metadata_updated this is. Each shape is logged once (same
            # "verify against a real payload before trusting it" idiom as
            # fill/market_positions below) since only close_date_updated
            # has been wired to do anything yet - see main.py's
            # _process_stream_lifecycle.
            msg = data.get("msg") or {}
            event_type = msg.get("event_type")
            if event_type and event_type not in self._logged_lifecycle_event_types:
                self._logged_lifecycle_event_types.add(event_type)
                logger.info("first real 'market_lifecycle_v2' %r shape (verify parsing against this): %r", event_type, msg)
            if on_lifecycle is not None:
                await on_lifecycle(msg)
            return
        if msg_type == "ticker":
            await on_ticker(data.get("msg") or {})
            return
        if msg_type == "fill":
            # 2026-08-15 direct request: "the open positions should feed
            # from the websocket stream." Real message shape has never
            # been observed live (fill events only fire on a real order
            # fill, and kalshi_account.trading_enabled is off, the standing
            # P0 safety gate - see CLAUDE.md - so none can occur yet) - the
            # raw payload is logged once, the first time one ever arrives,
            # specifically so it can be verified/corrected against real
            # data rather than trusted blind. main.py's handler is
            # defensive (dict.get() throughout, never assumes a field
            # exists) for the same reason.
            if not self._logged_fill_shape:
                self._logged_fill_shape = True
                logger.info("first real 'fill' message shape (verify parsing against this): %r", data)
            if on_fill is not None:
                await on_fill(data.get("msg") or {})
            return
        if msg_type == "market_position":
            # Real per-message `type` is "market_position" (SINGULAR) per
            # docs/kalshi/market-positions.md's own schema
            # (`const: market_position`) - confirmed 2026-08-24 building
            # tests/test_kalshi_contracts.py (QCP Task 13). The
            # *subscription channel* name is "market_positions" (plural,
            # see run()'s subscribe params a few lines up) - easy to
            # confuse the two, and this dispatch previously checked the
            # plural channel name here by mistake, so a real position
            # update's `type` field never matched and on_position was
            # never invoked at all, for any real account.
            if not self._logged_position_shape:
                self._logged_position_shape = True
                logger.info("first real 'market_position' message shape (verify parsing against this): %r", data)
            if on_position is not None:
                await on_position(data.get("msg") or {})

    async def _sync_subscriptions(self, force_subscribe: bool = False) -> None:
        # force_subscribe is now implied rather than read: run() resets both
        # per-channel flags before calling this on a fresh connection, which
        # is exactly what the parameter used to express. Kept in the
        # signature so the call site still reads as intentional.
        async with self._lock:
            ws = self._ws
        if ws is None:
            return
        desired = set(self._desired_tickers)

        # trade and ticker are tracked separately (2026-08-17). They used to
        # share one _market_channels_subscribed flag, which was fine only
        # because both were subscribed in the same breath off the same
        # watchlist. In exchange-wide mode they have genuinely different
        # lifecycles: trade can and should go up immediately on connect with
        # no watchlist at all, while ticker still has to wait for one. One
        # shared flag would either re-subscribe trade every time a watchlist
        # finally arrived (duplicate firehose) or block trade until it did
        # (defeating the point).
        if not self._trade_subscribed:
            trade_params: dict = {"channels": ["trade"]}
            if not self.exchange_wide_trades:
                trade_params["market_tickers"] = sorted(desired)
            if self.exchange_wide_trades or desired:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": trade_params,
                })
                self._trade_subscribed = True
                if not self.exchange_wide_trades:
                    self._subscribed_tickers = desired

        # market_lifecycle_v2 - opt-in (see __init__), unconditionally
        # exchange-wide like trade above, so it goes up once on connect and
        # never participates in add_markets/delete_markets either.
        if self.subscribe_lifecycle and not self._lifecycle_subscribed:
            await self._send({
                "id": self._next_message_id(),
                "cmd": "subscribe",
                "params": {"channels": ["market_lifecycle_v2"]},
            })
            self._lifecycle_subscribed = True

        # Index feeds are wholly independent of the watchlist - they take
        # index_ids/underlying_tickers, and the docs are explicit that
        # market_ticker(s)/market_id(s) "are not supported for this
        # channel". So, like exchange-wide trades, they go up on connect
        # and never participate in the add_markets/delete_markets path.
        if not self._index_subscribed and (self.index_ids or self.underlying_tickers):
            if self.index_ids:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": {"channels": ["cfbenchmarks_value"], "index_ids": list(self.index_ids)},
                })
            if self.underlying_tickers:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": {"channels": ["pyth_value"],
                               "underlying_tickers": list(self.underlying_tickers)},
                })
            self._index_subscribed = True

        if not self._ticker_subscribed:
            if desired:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "subscribe",
                    "params": {"channels": ["ticker"], "market_tickers": sorted(desired), "send_initial_snapshot": True},
                })
                self._ticker_subscribed = True
                self._subscribed_tickers = desired
            return

        to_add = sorted(desired - self._subscribed_tickers)
        to_remove = sorted(self._subscribed_tickers - desired)
        # Only the trade/ticker sids - self._subscription_sids also holds the
        # unrelated account-wide fill/market_positions sids (see run()), which
        # don't take a market_tickers add/remove payload at all.
        #
        # And in exchange-wide mode, not `trade` either: that subscription
        # has no market list to add to or delete from, and
        # docs/kalshi/websocket-connection.md documents no action for
        # switching one between scoped and unscoped. Sending add_markets
        # against it would either error or, worse, silently narrow the
        # firehose back down to a watchlist.
        # cfbenchmarks_value/pyth_value are excluded for a stronger reason
        # than exchange-wide trade is: they don't accept market_tickers at
        # all, so an add_markets against their sid is malformed by
        # construction, not merely unwanted.
        market_channels = ("ticker",) if self.exchange_wide_trades else ("trade", "ticker")
        market_channel_sids = [self._subscription_sids[c] for c in market_channels if c in self._subscription_sids]
        if to_add:
            for sid in market_channel_sids:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "update_subscription",
                    "params": {"sid": sid, "market_tickers": to_add, "action": "add_markets"},
                })
        if to_remove:
            for sid in market_channel_sids:
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "update_subscription",
                    "params": {"sid": sid, "market_tickers": to_remove, "action": "delete_markets"},
                })
        self._subscribed_tickers = desired

    async def _send(self, payload: dict) -> None:
        async with self._lock:
            if self._ws is None:
                return
            await self._ws.send(json.dumps(payload))

    def _auth_headers(self) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        message = (timestamp + "GET" + "/trade-api/ws/v2").encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    def _next_message_id(self) -> int:
        current = self._message_id
        self._message_id += 1
        return current

    @staticmethod
    def normalize_trade(msg: dict) -> dict:
        ts_ms = msg.get("ts_ms")
        created_time = None
        if ts_ms is not None:
            try:
                created_time = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except (TypeError, ValueError, OSError):
                created_time = None
        # PASS EVERYTHING THROUGH, then overlay the normalised names
        # (2026-08-17, direct and repeated instruction: "I keep insisting
        # that you stop shaving off fields and values from the various
        # shapes you get but you persist").
        #
        # This used to build a fixed dict of twelve keys and silently drop
        # anything else Kalshi sent. That was worse than it looked, because
        # it is UPSTREAM of series_watcher.record_trade: the `raw_json`
        # column added specifically to preserve unknown fields was, for
        # every websocket trade, storing this already-shaved dict rather
        # than the real payload. The capture built to stop field loss was
        # itself being fed pre-shaved data.
        #
        # `**msg` first means a field Kalshi adds tomorrow arrives intact,
        # reaches the raw store, and is queryable from the day it appears.
        # The explicit keys below still win, so every existing consumer sees
        # exactly what it saw before - `ticker` is still the normalised
        # alias for `market_ticker`, which itself now also survives.
        return {
            **msg,
            "trade_id": msg.get("trade_id"),
            "ticker": msg.get("market_ticker"),
            "yes_price_dollars": msg.get("yes_price_dollars"),
            "no_price_dollars": msg.get("no_price_dollars"),
            "count_fp": msg.get("count_fp"),
            # taker_outcome_side FIRST (2026-08-17 audit): docs/kalshi/
            # get-trades.md marks taker_side deprecated - "will not be
            # removed before May 14, 2026", a guarantee that has now
            # expired - and names taker_outcome_side/taker_book_side the
            # canonical way to determine trade direction. This used to
            # prefer the deprecated field, so the day Kalshi drops it every
            # trade would silently fall through to the "no" default
            # downstream rather than failing loudly.
            "taker_side": msg.get("taker_outcome_side") or msg.get("taker_side"),
            "taker_outcome_side": msg.get("taker_outcome_side") or msg.get("taker_side"),
            "taker_book_side": msg.get("taker_book_side"),
            "is_block_trade": msg.get("is_block_trade", False),
            "ts": msg.get("ts"),
            "ts_ms": msg.get("ts_ms"),
            "created_time": created_time,
        }
