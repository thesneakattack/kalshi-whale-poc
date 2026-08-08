"""
Generic REST whale-watcher provider. Only used when WHALE_WATCHER_API_URL is
set in .env — otherwise main.py falls back to services/whale_simulator.py
automatically.

This is written generically because "whale watcher" tools don't share one API
shape. Point WHALE_WATCHER_API_URL at whichever provider you pick, and if its
JSON field names differ from {ticker, side, size, price}, set the
WHALE_WATCHER_*_FIELD env vars to match rather than editing this file.

Expected response shape (after field mapping): a JSON array of objects, each
representing one recent large trade/print, e.g.:
    [{"ticker": "KXHIGHNY-...", "side": "yes", "size": 12000, "price": 0.63}, ...]

If your provider's shape is nested differently (e.g. {"data": {"trades": [...]}})
adjust `_extract_list` below to match.

Config precedence: services/accounts_store.py (the "Connect" form on the
Accounts page) wins if this provider has been connected that way; otherwise
falls back to the WHALE_WATCHER_* vars in .env. Either path works standalone —
connecting via the UI doesn't require touching .env at all.
"""
import os
import time
import uuid

from services import accounts_store
from services.http_client import get_client
from services.whale_simulator import WhaleSignal
from services.whalewatchers.base import WhaleWatcherProvider


class GenericRestProvider(WhaleWatcherProvider):
    name = "generic_rest"

    def __init__(self):
        stored = accounts_store.load(self.name) or {}
        self.api_url = stored.get("api_url") or os.getenv("WHALE_WATCHER_API_URL", "").strip()
        self.api_key = stored.get("api_key") or os.getenv("WHALE_WATCHER_API_KEY", "").strip()
        self.fields = {
            "ticker": stored.get("ticker_field") or os.getenv("WHALE_WATCHER_TICKER_FIELD", "ticker"),
            "side": stored.get("side_field") or os.getenv("WHALE_WATCHER_SIDE_FIELD", "side"),
            "size": stored.get("size_field") or os.getenv("WHALE_WATCHER_SIZE_FIELD", "size"),
            "price": stored.get("price_field") or os.getenv("WHALE_WATCHER_PRICE_FIELD", "price"),
        }

    @property
    def enabled(self) -> bool:
        return bool(self.api_url)

    def _extract_list(self, payload) -> list[dict]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("data", "results", "trades", "signals"):
                if isinstance(payload.get(key), list):
                    return payload[key]
        return []

    async def fetch_signals(
        self, since_ts: float | None = None, market_context: dict | None = None,
    ) -> list[WhaleSignal]:
        if not self.enabled:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = await get_client().get(self.api_url, headers=headers, timeout=10.0)
        resp.raise_for_status()
        raw_items = self._extract_list(resp.json())

        signals = []
        for item in raw_items:
            try:
                ticker = item[self.fields["ticker"]]
                side = str(item[self.fields["side"]]).lower()
                size = int(item[self.fields["size"]])
                price = float(item[self.fields["price"]])
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed entries rather than crash the loop

            signals.append(WhaleSignal(
                id=str(uuid.uuid4())[:8],
                ticker=ticker,
                side="yes" if side in ("yes", "buy_yes", "long") else "no",
                size=size,
                price=price,
                confidence=min(size / 50000, 1.0),  # naive size-based confidence; tune once you see real data
                timestamp=time.time(),
            ))
        return signals
