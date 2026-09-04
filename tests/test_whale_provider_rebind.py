"""Issue #565 - a provider re-instantiation must reach every consumer.

POST /api/accounts/connect and POST /api/accounts/{provider}/disconnect
re-instantiate the active whale-watcher provider when the affected provider
is the active one. Before this fix, main.py rebound only its own
module-level `whale_provider` name via `global`; services/app_state.py's
original attribute and the `from services.app_state import whale_provider`
copies in whale_stream_handlers.py and diagnostics/routes.py all kept
pointing at the old instance.

That matters because each WhaleWatcherProvider owns its own
_seen_trade_ids / _seen_order / _seen_lock (issue #546's dedupe ledger,
services/whalewatchers/kalshi_trade_tape.py:225-273). Once the WS-trade
path and the candidate-retry path land on different instances they stop
sharing that ledger, silently defeating #546's actual guarantee - and
diagnostics' own `dedup_ids_held` starts reporting a different instance's
ledger than the one the live path is filling.

Imports services.app_state transitively - safe because tests/conftest.py
installs tests/support/runtime_isolation.py before any test module loads,
so every eager singleton is redirected away from the live data/*.db files.
"""
import main
from services import app_state
from services.diagnostics import routes as diag_routes
from services.whale_stream import whale_stream_handlers as wsh


class _StubProvider:
    """Stands in for a WhaleWatcherProvider. Only the attributes the
    consumers under test actually read."""

    def __init__(self, name="kalshi_trade_tape", seen=()):
        self.name = name
        self.enabled = True
        self._seen_trade_ids = set(seen)
        self.stats = {}


class _StubStream:
    """Stands in for the KalshiStreamGateway singleton, whose real `enabled`
    is a read-only property."""

    def __init__(self, enabled: bool):
        self.enabled = enabled


def test_reload_whale_provider_installs_the_new_instance(monkeypatch):
    """reload_whale_provider() is the single mutation point the account
    endpoints call - it must both return the new instance and make it the
    one every subsequent read resolves to."""
    first, second = _StubProvider(name="first"), _StubProvider(name="second")
    monkeypatch.setattr(app_state, "_whale_provider", first)
    monkeypatch.setattr(app_state, "get_active_provider", lambda: second)

    returned = app_state.reload_whale_provider()

    assert returned is second
    assert app_state.get_whale_provider() is second


def test_ws_trade_path_follows_a_provider_swap(monkeypatch):
    """_streaming_trade_tape_enabled() gates the entire WS-trade path on the
    active provider's name. With a copied binding this stayed pinned to the
    provider that existed at import time, so a reconnect could leave the
    stream path running against a provider that was no longer active."""
    # trade_stream.enabled is a read-only property on KalshiStreamGateway, so
    # the gateway itself is stubbed rather than the attribute patched. Unlike
    # whale_provider, this singleton is never reassigned at runtime, so its
    # own copied binding is not part of this bug.
    monkeypatch.setattr(wsh, "trade_stream", _StubStream(enabled=True))

    monkeypatch.setattr(app_state, "_whale_provider", _StubProvider(name="kalshi_trade_tape"))
    assert wsh._streaming_trade_tape_enabled() is True

    monkeypatch.setattr(app_state, "_whale_provider", _StubProvider(name="generic_rest"))
    assert wsh._streaming_trade_tape_enabled() is False


def test_every_consumer_resolves_the_same_live_instance(monkeypatch):
    """The invariant the whole issue is about: main.py's paths
    (_candidate_retry_loop, the tick's fetch_signals fallback), the WS-trade
    path, and diagnostics must all resolve to one object, so they share one
    #546 dedupe ledger."""
    swapped = _StubProvider(name="swapped", seen={"a", "b", "c"})
    monkeypatch.setattr(app_state, "get_active_provider", lambda: swapped)

    app_state.reload_whale_provider()

    resolved = {
        "app_state": app_state.get_whale_provider(),
        "main": main.get_whale_provider(),
        "whale_stream_handlers": wsh.get_whale_provider(),
        "diagnostics": diag_routes.get_whale_provider(),
    }
    assert all(p is swapped for p in resolved.values()), resolved
    # The ledger diagnostics reports is the same object the live path fills.
    assert len(resolved["diagnostics"]._seen_trade_ids) == 3


def test_no_module_holds_a_copied_whale_provider_binding():
    """The bug class itself, in one assertion. A module doing
    `from services.app_state import whale_provider` takes a snapshot that no
    later reload can update. app_state deliberately exposes no importable
    `whale_provider` name, so that import now fails loudly at import time
    instead of silently going stale at runtime."""
    assert not hasattr(app_state, "whale_provider"), (
        "services.app_state exposes an importable `whale_provider` again - "
        "that is the name whose copies caused issue #565"
    )
    for mod in (main, wsh, diag_routes):
        assert not hasattr(mod, "whale_provider"), (
            f"{mod.__name__} holds its own `whale_provider` binding again - "
            "issue #565 regression; read through app_state.get_whale_provider()"
        )
