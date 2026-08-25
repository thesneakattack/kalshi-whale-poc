# quality-audit: kalshi-infrastructure
"""Consumer-facing capability Protocols for the Kalshi boundary — Phase C
Task C7.

Structural (typing.Protocol) contracts for what consumers actually
depend on, so application code and test fakes type against a CAPABILITY
rather than a transitional concrete class (the compatibility facades are
scheduled for removal at C8; nothing should be coupled to their names).
Infrastructure-marked: these are typing declarations, not vendor
adapters — the operations they mirror carry their CONTRACT_DOCS in the
gateway modules that implement them (account.py / orders.py), which is
also why no CONTRACT_DOCS mapping is declared here (provenance forbids
the same operation name in two boundary modules).

runtime_checkable so tests can assert a facade/fake satisfies a
capability with plain isinstance — method presence only, per Python's
Protocol runtime semantics; signatures are mypy's job.

Resource ownership stays where A8/A11 put it, explicitly: gateways
borrow their SDK client; whoever constructs the client owns close().
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AccountReads(Protocol):
    """Authenticated read capability - balance/positions/fills/orders.
    Cannot place or cancel anything."""

    async def get_balance(self) -> dict: ...
    async def get_positions(self) -> dict: ...
    async def get_fills(self, limit: int = 25) -> dict: ...
    async def get_orders(self, limit: int = 25, cursor: str | None = None, status: str | None = None) -> dict: ...


@runtime_checkable
class OrderWrites(Protocol):
    """Gated write capability. Implementations carry the trading_enabled
    and risk-halt gates themselves (services/kalshi/orders.py) - the
    Protocol describes the surface, never replaces the gates."""

    async def create_order(self, *args, **kwargs) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...


@runtime_checkable
class FlattenCapable(Protocol):
    """Exactly what the emergency flatten composes (services/execution.py):
    read positions, place gated closing orders. A fake satisfying this is
    all a flatten test needs - no concrete facade required."""

    async def get_positions(self) -> dict: ...
    async def create_order(self, *args, **kwargs) -> dict: ...
