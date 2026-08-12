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
        self._message_id = 1
        self._update_event = asyncio.Event()
        self._stop = False
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

    async def run(self, on_trade, on_ticker, on_status=None) -> None:
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
                        self._message_id = 1
                    if on_status is not None:
                        await on_status({"connected": True, "error": None, "ws_url": self.ws_url})
                    await self._sync_subscriptions(force_subscribe=True)
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
                            await self._handle_message(raw_message, on_trade, on_ticker, on_status)
            except Exception as exc:
                if on_status is not None:
                    await on_status({"connected": False, "error": str(exc), "ws_url": self.ws_url})
                async with self._lock:
                    self._ws = None
                    self._subscription_sids = {}
                    self._subscribed_tickers = set()
                if self._stop:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _handle_message(self, raw_message: str, on_trade, on_ticker, on_status) -> None:
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

    async def _sync_subscriptions(self, force_subscribe: bool = False) -> None:
        async with self._lock:
            ws = self._ws
        if ws is None:
            return
        desired = set(self._desired_tickers)
        if force_subscribe or not self._subscription_sids:
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
            return
        to_add = sorted(desired - self._subscribed_tickers)
        to_remove = sorted(self._subscribed_tickers - desired)
        if to_add:
            for sid in self._subscription_sids.values():
                await self._send({
                    "id": self._next_message_id(),
                    "cmd": "update_subscription",
                    "params": {"sid": sid, "market_tickers": to_add, "action": "add_markets"},
                })
        if to_remove:
            for sid in self._subscription_sids.values():
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
            "taker_side": msg.get("taker_side") or msg.get("taker_outcome_side"),
            "taker_outcome_side": msg.get("taker_outcome_side") or msg.get("taker_side"),
            "taker_book_side": msg.get("taker_book_side"),
            "is_block_trade": msg.get("is_block_trade", False),
            "ts": msg.get("ts"),
            "ts_ms": msg.get("ts_ms"),
            "created_time": created_time,
        }
