---
name: frontend-modularization-task
description: Use when implementing, resuming, or reviewing a task from the frontend modularization plan (Preact + signals + htm strangler-fig migration, schema-driven Config tab, charts module), or when the user says to continue that work. Reconstruct progress from current HEAD, execute exactly one incomplete task with TDD, keep every guard CI-owned, verify, commit, and stop.
---

# Frontend Modularization Task Execution

Canonical files (stop and report if any is missing — never reconstruct them from memory):

- `docs/superpowers/research/2026-08-25-frontend-modularization-research.md` — evidence
- `docs/superpowers/specs/2026-08-25-frontend-modularization-design.md` — the design
- `docs/superpowers/plans/2026-08-25-frontend-modularization.md` — numbered tasks T1a–T9
- `frontend/CHEATSHEET.md` (exists from T1b onward) — layer rules, API-helper rule, ownership rule

This skill is the execution orchestrator. Superpowers skills stay supporting disciplines
(`test-driven-development`, `systematic-debugging`, `verification-before-completion`,
`requesting-code-review`); do not switch to a worktree/subagent/ledger orchestration model
unless the user asks. Do not keep a separate progress ledger.

## Reconstruct progress (every task, before touching anything)

1. `git branch --show-current`, `git status --short`, `git log --oneline -15`,
   `git log origin/main..HEAD --oneline`. PR groups and branch prefixes are in the plan; resume
   the existing initiative branch if it exists, else cut one from a synchronized `main`
   (`.claude/rules/branching-and-ci.md`).
2. Read the plan's checkboxes, then **verify them against HEAD**, which is truth:
   - `ls frontend/src/js/panels frontend/src/js/legacy frontend/src/js/core 2>/dev/null`
   - `grep -c '^window\.' frontend/src/js/legacy/*.js` (the `window.*` ratchet)
   - `python3 -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json`
     and the `frontend-*` entries in `tools/quality_audit/baseline.json`
   - `cd frontend && npm run check` (from T1b onward)
   - the E2E probes named in the plan (`data-bundle`, `#system-health`, `#config-panel`)
3. The first task whose acceptance criteria HEAD does not satisfy is the task. State it, and
   what evidence says the previous one is complete.

## Per-task workflow

1. Read the task's **Create / Modify / Delete** lists and the spec sections it implements.
   For Kalshi-shaped data in a panel, `docs/kalshi/CHEATSHEET.md` and
   `.claude/rules/kalshi-integration-authority.md` still apply.
2. Load the relevant capabilities only: `frontend-verification` (always for JS changes),
   `ci-cd-guardrails` (T1a, T1b, any new guard), `persistence-safety` (T4a/T4b touch
   `config_store` behaviour), `kalshi-contract-review` (only if a panel changes what it reads
   from Kalshi-derived fields), `dimensional-analysis` (T8b), Chrome DevTools (browser checks).
3. TDD: model/render tests first (`frontend/test/**`), backend tests first (`tests/test_config_schema.py`).
4. Implement. Rules that are not negotiable:
   - **One container id ↔ one renderer.** Delete every `legacy/` write to the panel's `OWNS` ids
     in the same commit; `test/guards/container-ownership.test.js` must pass.
   - **Nothing outside `legacy/` imports `legacy/`.** Layer rules per the spec §1.1.
   - **`fetchJSON('/literal', …)` only** — no URL-building helpers (the contract scanner).
   - **Backend-named financial fields only** in models; no client-side re-derivation
     (CLAUDE.md "Bug pattern to watch for"); `sideAdjustedPrice()` stays single-owner.
   - **Keyed lists, handlers as properties, no `window.*` exposure, no `innerHTML`** in new code.
   - Preserve ids/classes the E2E tests use (`tab-btn-*`, `view-*`, `#bankroll`, `.sh-status`).
   - `POST /api/config`'s three refusal `detail` strings stay byte-identical.
5. Ratchets move **down**: remove the resolved `frontend-*` baseline ids in the same commit and
   say so in the message; never add a new `frontend-window-export`/cycle id to make CI green.
6. Targeted local verification (cheap): `cd frontend && npm run check`; the specific pytest
   files named in the task; `python3 -m tools.quality_audit …` exit 0; a `/run` browser smoke
   when the task says "browser check"; regenerate `static/project-manifest.json` on the host
   when file counts change. Woodpecker does the exhaustive run on push.
7. Before commit: `git diff --check`, `git status --short`, stage specific paths only, no
   bundle/`.map`/`node_modules`/`data/*.db`/`.env`.
8. Commit with the task's message; push; confirm the Woodpecker status for the SHA
   (`gh api repos/thesneakattack/kalshi-whale-poc/commits/<sha>/status`); on red, fix
   narrowly and push again — never weaken a guard.
9. Tick the task's checkboxes in the plan **in the same commit** (the plan is documentation,
   HEAD is truth). If a task turns out too large, split it, document the split in the plan,
   and commit the first half only.
10. Stop. Report: task done, evidence, what the next task is, whether a PR group is complete
    (then `gh pr create`, merge with `--merge`, delete the branch per the branching rule).

## Stop conditions

- A canonical file is missing or contradicts HEAD in a way the plan didn't anticipate.
- A required guard would have to be weakened to proceed.
- A change would touch real-trading gates, the kill switch, or live `data/*.db` files.
- The measured bundle or tick-render cost exceeds the spec's budget and the task has no
  documented mitigation — report the number, don't raise the budget.
