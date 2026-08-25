"""
Compatibility facade over the Kalshi integration boundary's public read
gateway (services/kalshi/public.py) — Phase A Tasks A6/A7.

This file used to BE the public client. Its wire semantics (envelope
handling, SDK/raw-HTTP compatibility, batching) live in KalshiPublicGateway
since A6, and its market-selection policy (candidate filtering, series-level
round-robin, watchlist caps) lives in services/market_watch/selection.py
since A7 — the adapter batches and fetches efficiently, but the decision of
what the strategy should watch is application policy, made in market-watch
scope.

KalshiClient subclasses the gateway and adds nothing: every existing
constructor/caller keeps the exact same surface with zero re-implemented
semantics (the design spec's facade rule — see
tests/test_kalshi_public_gateway.py, which asserts the facade owns no
methods of its own). It exists so the many pre-boundary construction sites
keep working unchanged until their modules migrate (A13/A14), at which
point facade rule 4 applies: zero callers requires facade deletion.

Do not add new methods here, and avoid new imports of this facade — new
code constructs KalshiPublicGateway (or calls the market_watch selection
layer) directly; A15 turns that into a CI ratchet.
"""
from services.kalshi.public import KalshiPublicGateway


class KalshiClient(KalshiPublicGateway):
    pass
