# Domain: frontend (Preact + signals + htm strangler-fig migration)

Canonical: `docs/superpowers/research/2026-08-25-frontend-modularization-research.md`,
`docs/superpowers/specs/2026-08-25-frontend-modularization-design.md`,
`docs/superpowers/plans/2026-08-25-frontend-modularization.md`, `frontend/CHEATSHEET.md`.

- Re-ground evidence: `ls frontend/src/js/{panels,legacy,core}`; `grep -c '^window\.' frontend/src/js/legacy/*.js`; the `frontend-*` entries in `tools/quality_audit/baseline.json`; `cd frontend && npm run check`.
- Always `frontend-verification`; Chrome DevTools MCP for browser checks; `dimensional-analysis` for any money math in a panel.
- Non-negotiable: one container id ↔ one renderer (delete the legacy writes in the same commit); nothing outside `legacy/` imports `legacy/`; `fetchJSON('/literal', …)` only; backend-named financial fields only, no client re-derivation; keyed lists, no `window.*`, no `innerHTML`; preserve E2E ids (`tab-btn-*`, `view-*`, `#bankroll`, `.sh-status`); `POST /api/config`'s refusal strings stay byte-identical.
- Ratchets move down only — never add a `frontend-*` baseline id to go green. Regenerate `static/project-manifest.json` on the host when file counts change.
