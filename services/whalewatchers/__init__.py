"""
Whale-watcher provider library. Exactly one provider is active at a time,
selected by WHALE_WATCHER_PROVIDER in .env. Defaults to "kalshi_trade_tape" —
real (size-based) whale signal from Kalshi's own public trade feed, needs no
credentials, verified live 2026-08-08 (see
docs/kalshi-whale-provider-and-strategy-porting-plan.md) — so there's no
reason to default to the simulator once a real source exists and works. Set
WHALE_WATCHER_PROVIDER=generic_rest (with WHALE_WATCHER_API_URL blank) to
fall back to simulator-only behavior instead. Add a new provider by copying
template_provider.py and registering it below — main.py and the strategy
engine only ever talk to the WhaleWatcherProvider interface (see base.py),
so nothing else needs to change.
"""
import os

from services.whalewatchers.base import WhaleWatcherProvider
from services.whalewatchers.generic_rest import GenericRestProvider
from services.whalewatchers.kalshi_trade_tape import KalshiTradeTapeProvider

PROVIDERS: dict[str, type[WhaleWatcherProvider]] = {
    "generic_rest": GenericRestProvider,
    "kalshi_trade_tape": KalshiTradeTapeProvider,
    # "flowalgo": FlowAlgoProvider,   <- example of what adding a real one looks like
}


def get_active_provider() -> WhaleWatcherProvider:
    name = os.getenv("WHALE_WATCHER_PROVIDER", "kalshi_trade_tape").strip()
    provider_cls = PROVIDERS.get(name, KalshiTradeTapeProvider)
    return provider_cls()
