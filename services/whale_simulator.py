"""
Generates fake "whale" order-flow prints against whatever markets are
currently on the watchlist. This is a stand-in for a real whale-watcher
feed/API — same output shape, so swapping it out later is a one-file change.

A real implementation would live at services/whale_live.py and expose the
same generate(markets) -> list[WhaleSignal] interface.
"""
import random
import time
import uuid
from dataclasses import dataclass, asdict


@dataclass
class WhaleSignal:
    id: str
    ticker: str
    side: str          # "yes" | "no"
    size: int          # fake contract count
    price: float        # 0-1 implied probability at time of print
    confidence: float   # 0-1 synthetic confidence score
    timestamp: float

    def to_dict(self):
        return asdict(self)


class WhaleSimulator:
    def __init__(self, size_range=(5000, 50000), bias="random"):
        self.size_range = size_range
        self.bias = bias
        self._last_emit = 0.0

    def maybe_generate(self, markets: list[dict], avg_interval_sec: float) -> WhaleSignal | None:
        """Randomly emits at most one signal per call, paced by avg_interval_sec."""
        now = time.time()
        if now - self._last_emit < random.expovariate(1 / max(avg_interval_sec, 1)):
            return None
        if not markets:
            return None

        market = random.choice(markets)
        side = self._pick_side(market)
        # yes_bid_dollars is Kalshi's real field (already 0-1) — "yes_bid" (cents)
        # doesn't exist on the live API, so this used to silently always fall
        # through to a random price instead of the market's actual one.
        yes_bid = float(market.get("yes_bid_dollars") or 0)
        price = yes_bid if yes_bid > 0 else random.uniform(0.05, 0.95)
        size = random.randint(*self.size_range)
        confidence = self._score_confidence(size)

        signal = WhaleSignal(
            id=str(uuid.uuid4())[:8],
            ticker=market.get("ticker", "UNKNOWN"),
            side=side,
            size=size,
            price=round(price, 2),
            confidence=round(confidence, 2),
            timestamp=now,
        )
        self._last_emit = now
        return signal

    def _pick_side(self, market: dict) -> str:
        yes_bid = float(market.get("yes_bid_dollars") or 0.5)
        if self.bias == "momentum":
            return "yes" if yes_bid >= 0.5 else "no"
        if self.bias == "contrarian":
            return "no" if yes_bid >= 0.5 else "yes"
        return random.choice(["yes", "no"])

    def _score_confidence(self, size: int) -> float:
        lo, hi = self.size_range
        span = max(hi - lo, 1)
        base = (size - lo) / span               # bigger size -> higher confidence
        noise = random.uniform(-0.15, 0.15)
        return min(max(base + noise, 0.0), 1.0)
