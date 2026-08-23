"""
TEMPLATE — not registered, not wired into anything. Copy this file to add a
named whale-watcher provider (e.g. services/whalewatchers/flowalgo.py) once you
know which real tool/account you're integrating.

Steps to turn this into a real provider:
  1. Rename the file and the class.
  2. Set `name` to something short and stable — it's what WHALE_WATCHER_PROVIDER
     in .env will select.
  3. Read whatever credentials this specific provider needs from its own env
     vars (prefixed with the provider name so multiple providers can each hold
     their own account credentials side by side, e.g. FLOWALGO_API_KEY,
     FLOWALGO_ACCOUNT_ID) rather than the shared generic WHALE_WATCHER_* vars.
  4. Implement fetch_signals() to call the provider's real API and return
     WhaleSignal objects — same contract every provider follows, so the
     strategy engine and dashboard don't need to know which one is active.
  5. Register it in services/whalewatchers/__init__.py's PROVIDERS dict.
"""
from services.confidence_scoring import WhaleSignal
from services.whalewatchers.base import WhaleWatcherProvider


class TemplateProvider(WhaleWatcherProvider):
    name = "template"

    def __init__(self):
        # e.g. self.api_key = os.getenv("TEMPLATE_API_KEY", "").strip()
        pass

    @property
    def enabled(self) -> bool:
        return False

    async def fetch_signals(
        self, since_ts: float | None = None, market_context: dict | None = None,
    ) -> list[WhaleSignal]:
        raise NotImplementedError("Copy this file into a real provider before using it")
