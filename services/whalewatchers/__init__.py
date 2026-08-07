"""
Whale-watcher provider library. Exactly one provider is active at a time,
selected by WHALE_WATCHER_PROVIDER in .env (defaults to "generic_rest").
Add a new provider by copying template_provider.py and registering it below —
main.py and the strategy engine only ever talk to the WhaleWatcherProvider
interface (see base.py), so nothing else needs to change.
"""
import os

from services.whalewatchers.base import WhaleWatcherProvider
from services.whalewatchers.generic_rest import GenericRestProvider

PROVIDERS: dict[str, type[WhaleWatcherProvider]] = {
    "generic_rest": GenericRestProvider,
    # "flowalgo": FlowAlgoProvider,   <- example of what adding a real one looks like
}


def get_active_provider() -> WhaleWatcherProvider:
    name = os.getenv("WHALE_WATCHER_PROVIDER", "generic_rest").strip()
    provider_cls = PROVIDERS.get(name, GenericRestProvider)
    return provider_cls()
