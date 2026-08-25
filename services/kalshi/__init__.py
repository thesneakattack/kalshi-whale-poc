"""Kalshi integration boundary package (Kalshi Integration Phase A,
docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md).

All vendor-specific Kalshi semantic interpretation - REST/WS request and
response shapes, field aliases, direction/side semantics, lifecycle states,
fixed-point rules - migrates behind this package over Phase A tasks A4-A14.
Application policy (market selection, strategy, execution orchestration)
stays outside; raw payload archival is preserved end to end.

Every public adapter/normalizer operation added here must declare its exact
mirrored-doc sources in a module-level CONTRACT_DOCS mapping (see
provenance.ContractDocs). Two guards enforce that, one static and one
importable:
- tools/quality_audit/kalshi_contract_docs.py (CI, Task A3) AST-scans this
  package on every push;
- services/kalshi/provenance.py (Task A4) aggregates and validates the
  declarations at runtime/test time, including cross-module duplicates the
  per-module static scan can't see.

Migrated so far: transport/SDK-client construction (A5, transport.py),
the public read gateway (A6, public.py — selection policy moved out to
services/market_watch/ at A7), and the authenticated account split (A8):
account.py owns balance/positions/fills/orders READS, orders.py owns the
create/cancel WRITE primitives plus the trading_enabled and risk kill-
switch gates. The compatibility facades were deleted at zero callers
(Phase C Task C8) - production imports the gateways directly, and the
composing account connection lives at services/kalshi/account_client.py.
"""
