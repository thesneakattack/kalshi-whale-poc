# Domain: quality (Quality Control Plane, tools/quality_audit, quality_ratchet)

Canonical: `docs/superpowers/specs/2026-08-24-quality-control-plane-design.md`,
`docs/superpowers/plans/2026-08-24-quality-control-plane.md`.

- Baseline ratchet: `tools/quality_audit/baseline.json` IDs are reviewed decisions with a dated note; remove an ID in the same commit its finding resolves.
- A deterministic recurring check is not integrated until CI invokes it and a deliberate isolated failure proves it (`ci-cd-guardrails`); runtime-only conditions go to diagnostics/observability instead.
- New persistence → `persistence-safety`; new routes → the `tools.quality_audit` routers/persistence/config-usage scanners must see them wired.
- Before calling a guard done, prove in an isolated fixture that it detects: an unmounted router, an unwired scheduler, test access to `data/*.db`, a missing route contract, docs drift, a stale manifest.
