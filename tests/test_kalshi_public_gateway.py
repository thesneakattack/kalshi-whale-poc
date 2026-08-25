"""Tests for services/kalshi/public.py — the documented public read gateway
(Kalshi Integration Phase A Task A6).

Wire-semantic behavior tests live in tests/test_kalshi_client.py and keep
passing unchanged through the compatibility facade (KalshiClient subclasses
the gateway, so those tests exercise the gateway's own implementations).
This file proves the boundary structure itself: one implementation under
services/kalshi/, the facade adding policy only, and every gateway
operation carrying CONTRACT_DOCS provenance.
"""
from __future__ import annotations

import asyncio
import inspect

from services.kalshi.public import CONTRACT_DOCS, KalshiPublicGateway
from services.kalshi_client import KalshiClient


def test_facade_is_the_gateway_not_a_reimplementation():
    """Design-spec facade rule 5: 'facade methods delegate without
    re-implementing semantics.' Subclassing is that, provably: the facade's
    wire methods ARE the gateway's own functions."""
    assert issubclass(KalshiClient, KalshiPublicGateway)
    for name in ("get_markets", "get_series_list", "get_market", "get_markets_by_tickers",
                 "get_orderbook", "get_event", "get_events", "get_live_datas",
                 "get_trades", "get_exchange_status", "close"):
        assert getattr(KalshiClient, name) is getattr(KalshiPublicGateway, name), name


def test_facade_keeps_only_policy_methods_of_its_own():
    """Selection/watchlist policy (A7's migration target) is the only
    non-inherited surface the facade still owns."""
    own = {
        name for name, member in vars(KalshiClient).items()
        if callable(member) and not name.startswith("__")
    }
    assert own <= {"get_candidate_markets", "round_robin_select", "get_top_volume_markets"}


def test_gateway_covers_every_used_operation_with_contract_docs():
    """Every public coroutine the gateway exposes maps to exact mirrored
    docs — the A3 scanner enforces this statically in CI; this asserts it
    at the object level too (and catches a def the scanner's AST walk and
    a runtime consumer could ever disagree about)."""
    public_coros = {
        name for name, member in vars(KalshiPublicGateway).items()
        if inspect.iscoroutinefunction(member) and not name.startswith("_") and name != "close"
    }
    assert public_coros, "gateway unexpectedly empty"
    assert public_coros == set(CONTRACT_DOCS.keys())


def test_gateway_constructs_and_closes_standalone(monkeypatch):
    """The gateway is usable without the facade — A13/A14 consumers will
    construct it directly."""
    gateway = KalshiPublicGateway(base_url="https://example.invalid/trade-api/v2/", timeout=1.0)
    assert gateway.base_url == "https://example.invalid/trade-api/v2"

    closed = []

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(gateway._client, "close", fake_close)
    asyncio.run(gateway.close())
    assert closed == [True]
