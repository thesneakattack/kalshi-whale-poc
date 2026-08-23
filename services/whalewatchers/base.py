"""
Common interface every whale-watcher provider implements. The strategy engine,
main.py's trading loop, and the dashboard only ever talk to this interface —
never to a specific provider — so adding a new one is a new file, not a rewrite.
"""
from abc import ABC, abstractmethod

from services.confidence_scoring import WhaleSignal


class WhaleWatcherProvider(ABC):
    name: str = "unnamed"

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this provider has what it needs (API key, URL, ...) to run."""

    @abstractmethod
    async def fetch_signals(
        self, since_ts: float | None = None, market_context: dict | None = None,
    ) -> list[WhaleSignal]:
        """Return whatever new whale prints exist since since_ts (or 'recent' if None).

        market_context, when passed, is this tick's already-fetched data —
        {"markets": [...], "trade_tape": [...], "cfg": {...}} — for a
        provider that derives signals from data this app already fetched
        rather than calling an external source (see
        services/whalewatchers/kalshi_trade_tape.py). Providers that fetch
        from an independent external API (generic_rest, template) ignore it."""
