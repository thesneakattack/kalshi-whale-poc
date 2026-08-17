import asyncio
import base64
import json
import os
import time
from datetime import datetime, timezone

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

_PROD_WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
_DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"


class KalshiTradeWebSocketClient:
    def __init__(self, base_url: str):
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
        self._market_channels_subscribed = False
        self._message_id = 1
        self._update_event = asyncio.Event()
        self._stop = False
        self._logged_fill_shape = False
        self._logged_position_shape = False
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

    async def run(self, on_trade, on_ticker, on_status=None, on_fill=None, on_position=None) -> None:
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
                        self._market_channels_subscribed = False
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
                            await self._handle_message(raw_message, on_trade, on_ticker, on_status, on_fill, on_position)
            except Exception as exc:
                if on_status is not None:
                    await on_status({"connected": False, "error": str(exc), "ws_url": self.ws_url})
                async with self._lock:
                    self._ws = None
                    self._subscription_sids = {}
                    self._subscribed_tickers = set()
                    self._market_channels_subscribed = False
                if self._stop:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_message(self, raw_message: str, on_trade, on_ticker, on_status, on_fill=None, on_position=None) -> None:
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
        if msg_type == "trade":
            await on_trade(self.normalize_trade(data.get("msg") or {}))
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
                print(f"[kalshi_ws] first real 'fill' message shape (verify parsing against this): {data!r}")
            if on_fill is not None:
                await on_fill(data.get("msg") or {})
            return
        if msg_type == "market_positions":
            if not self._logged_position_shape:
                self._logged_position_shape = True
                print(f"[kalshi_ws] first real 'market_positions' message shape (verify parsing against this): {data!r}")
            if on_position is not None:
                await on_position(data.get("msg") or {})

    async def _sync_subscriptions(self, force_subscribe: bool = False) -> None:
        async with self._lock:
            ws = self._ws
        if ws is None:
            return
        desired = set(self._desired_tickers)
        if force_subscribe or not self._market_channels_subscribed:
            if not desired:
                return
            await self._send({
                "id": self._next_message_id(),
                "cmd": "subscribe",
                "params": {"channels": ["trade"], "market_tickers": sorted(desired)},
            })
            await self._send({
                "id": self._next_message_id(),
                "cmd": "subscribe",
                "params": {"channels": ["ticker"], "market_tickers": sorted(desired), "send_initial_snapshot": True},
            })
            self._subscribed_tickers = desired
            self._market_channels_subscribed = True
            return
        to_add = sorted(desired - self._subscribed_tickers)
        to_remove = sorted(self._subscribed_tickers - desired)
        # Only the trade/ticker sids - self._subscription_sids also holds the
        # unrelated account-wide fill/market_positions sids (see run()), which
        # don't take a market_tickers add/remove payload at all.
        market_channel_sids = [self._subscription_sids[c] for c in ("trade", "ticker") if c in self._subscription_sids]
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
        return {
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
