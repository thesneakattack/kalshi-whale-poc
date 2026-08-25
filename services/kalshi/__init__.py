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

As of A4 this is the boundary skeleton only: no transport, gateway, or
normalizer code has migrated yet (that starts at A5), so nothing here is
imported by production code paths yet.
"""
