"""Tests for services/kalshi/public.py — the documented public read gateway
(Kalshi Integration Phase A Task A6; sole implementation AND sole import
path since the compatibility facades were deleted at zero callers,
Phase C Task C8).

Wire-semantic behavior tests live in tests/test_kalshi_client.py and
exercise the gateway directly. This file proves the boundary structure:
one implementation under services/kalshi/, no legacy import path left,
and every gateway operation carrying CONTRACT_DOCS provenance.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from services.kalshi.public import CONTRACT_DOCS, KalshiPublicGateway


def test_legacy_facade_import_paths_are_gone():
    """C8: the compatibility facades were deleted at zero callers - the
    legacy import paths must stay dead. A reintroduced facade module
    would make these imports succeed and this test fail (the runtime
    complement to the kalshi_boundary CI ratchet)."""
    with pytest.raises(ModuleNotFoundError):
        import services.kalshi_client  # noqa: F401
    with pytest.raises(ModuleNotFoundError):
        import services.kalshi_trade_ws  # noqa: F401
    with pytest.raises(ModuleNotFoundError):
        import services.kalshi_account_client  # noqa: F401


# (test_facade_owns_no_methods_of_its_own retired at C8 with the facade
# itself - superseded by test_legacy_facade_import_paths_are_gone.)


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
