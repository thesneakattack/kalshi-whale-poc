"""
Common interface every whale-watcher provider implements. The strategy engine,
main.py's trading loop, and the dashboard only ever talk to this interface —
never to a specific provider — so adding a new one is a new file, not a rewrite.
"""
from abc import ABC, abstractmethod

from services.whale_simulator import WhaleSignal


class WhaleWatcherProvider(ABC):
    name: str = "unnamed"

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this provider has what it needs (API key, URL, ...) to run."""

    @abstractmethod
    async def fetch_signals(self, since_ts: float | None = None) -> list[WhaleSignal]:
        """Return whatever new whale prints exist since since_ts (or 'recent' if None)."""
