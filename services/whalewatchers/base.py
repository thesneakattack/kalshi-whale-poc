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

    async def score_recovered_trade(
        self, trade: dict, market: dict, cfg: dict, now: float,
    ) -> list[WhaleSignal]:
        """Score a single already-resolved trade+market pair through this
        provider's normal per-trade scoring pipeline, for a candidate whose
        market lookup failed on the first attempt and is now being
        recovered (services/candidate_retry.py) instead of seen through a
        fresh fetch_signals() call. Called from main.py's tick loop, never
        from a specific-provider reach-through, so this stays the one place
        candidate_retry needs to know about a provider's scoring pipeline.

        Not abstract, and deliberately a no-op by default: most providers
        derive signals from a batch fetch they have no way to meaningfully
        re-enter for a single already-known item, so "nothing to recover
        here" (empty list) is the same outcome as if the print had simply
        never happened. Only KalshiTradeTapeProvider overrides this today —
        it is the only provider services.candidate_retry.enqueue() is ever
        called from (see services/whalewatchers/kalshi_trade_tape.py's own
        _resolve_unknown_markets).

        async (not a plain method) so candidate_retry.run_pending can
        `await provider.score_recovered_trade(...)` uniformly regardless of
        which provider is active - KalshiTradeTapeProvider's own override
        runs its scoring work on a dedicated thread pool (see
        kalshi_trade_tape.py), so the interface itself has to be async even
        though this default has nothing to await."""
        return []
