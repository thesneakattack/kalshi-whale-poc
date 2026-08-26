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

WhaleSignal.id / dedup (code-review fix, finding #5 - /code-review high
pass against PR #23): candidate_ledger's trade_id dedup gate
(services/whale_stream/decision_bridge._handle_signal) needs a STABLE id -
the same underlying print must produce the same id if it's ever seen
twice (e.g. re-fetched on the next poll before this API's own window
rolls it off). If your provider's payload has a genuine id/trade-id field,
set WHALE_WATCHER_ID_FIELD to it (defaults to "id") and that value is used
directly. Not every third-party whale-tracker API exposes one, though -
this module's own example payload above has no id field at all - so when
the configured field is absent, fetch_signals falls back to a SHA-256 hash
of (ticker, side, size, price). That is a real, accepted limitation, not a
bug: it makes the SAME trade reproducibly dedupe across polls (fixing the
actual reported defect - a fresh random uuid every fetch meant dedup could
never work at all), but it CANNOT distinguish two genuinely different real
trades that happen to share the exact same ticker/side/size/price within
one poll window - those collide onto the same id, and candidate_ledger's
claim() will (correctly, given the information available) treat the
second one as a duplicate and skip it. This is an inherent property of a
data source with no true identity field, not something this module can
fix without one; if your provider's data matters enough to care about that
edge case, configure WHALE_WATCHER_ID_FIELD for it.

Config precedence: services/accounts_store.py (the "Connect" form on the
Accounts page) wins if this provider has been connected that way; otherwise
falls back to the WHALE_WATCHER_* vars in .env. Either path works standalone —
connecting via the UI doesn't require touching .env at all.
"""
import hashlib
import os
import time

from services import accounts_store
from services.http_client import get_client
from services.confidence_scoring import WhaleSignal
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
            # id (code-review fix, finding #5 - /code-review high pass
            # against PR #23): WhaleSignal.id used to be a fresh
            # uuid.uuid4() per fetch, guaranteeing candidate_ledger's
            # trade_id dedup could never work for this provider - the exact
            # same underlying print, fetched again on the very next poll
            # before this API's own window rolled it off, was treated as a
            # brand-new trade every single time. Configurable the same way
            # as the other four fields, defaulting to the common "id"
            # convention; see fetch_signals below for the fallback when no
            # such field is present in a given item.
            "id": stored.get("id_field") or os.getenv("WHALE_WATCHER_ID_FIELD", "id"),
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
                id=self._signal_id(item, ticker, side, size, price),
                ticker=ticker,
                side="yes" if side in ("yes", "buy_yes", "long") else "no",
                size=size,
                price=price,
                confidence=min(size / 50000, 1.0),  # naive size-based confidence; tune once you see real data
                timestamp=time.time(),
            ))
        return signals

    def _signal_id(self, item: dict, ticker: str, side: str, size: int, price: float) -> str:
        """A stable id for candidate_ledger's trade_id dedup gate - see this
        module's own docstring for the full reasoning. Prefers the
        configured id field when the payload actually has one; falls back
        to a content hash of the already-parsed, already-mapped fields
        (not the raw item, which may include per-fetch noise like a
        server-generated response wrapper) so the SAME print reproducibly
        gets the SAME id across polls."""
        raw_id = item.get(self.fields["id"]) if isinstance(item, dict) else None
        if raw_id not in (None, ""):
            return str(raw_id)
        digest = hashlib.sha256(f"{ticker}|{side}|{size}|{price}".encode()).hexdigest()
        return digest[:16]
