# Domain: kalshi (integration boundary, contracts, fixtures)

Canonical: `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`,
`docs/superpowers/plans/2026-08-24-kalshi-integration-{dual-phase,phase-a,phase-c}.md`
(both phases merged 2026-08-25), `services/kalshi/CHEATSHEET.md` (the add/change-an-endpoint workflow).

- `docs/kalshi/CHEATSHEET.md` titles first, then the exact mirrored page; `kalshi-contract-review` for any field, message type, lifecycle, rate-limit, or fixture change. Never memory.
- Semantic interpretation lives only in `services/kalshi/` (the architecture audit's `kalshi_boundary` scanner enforces it); raw payloads are preserved for archival.
- Report the exact local doc pages consulted; measure before changing hot-path behavior; migrate compatibly and incrementally, never a big-bang rewrite.
- For multi-file Kalshi boundary work, run `python -m tools.kalshi_census --repo-root . --json-out docs/kalshi/used-contracts.json` first — gives the mechanical blast radius (SDK imports, host strings, deprecated field reads, hot/cold classification) so you don't rely on memory.
