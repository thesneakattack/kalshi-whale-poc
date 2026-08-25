# Specialized tooling — plugin routing policy

Installed 2026-08-25. This file is the authoritative router for **which
specialized tool to reach for**. It does not replace any existing rule:
`.claude/rules/branching-and-ci.md` still owns git/CI,
`.claude/rules/kalshi-integration-authority.md` still owns Kalshi semantics,
and `.claude/rules/quality-capabilities.md` still owns this repo's own
`.claude/skills/*`.

## Responsibility hierarchy

```
Superpowers          engineering process        (owns HOW work is done)
  ↓ everything below supplements it, never replaces it
GitNexus             architectural/dependency evidence
Context7             external-library documentation
dimensional-analysis trading/scoring numerical correctness
42Crunch             API/OpenAPI security
Chrome DevTools MCP  browser/runtime/network evidence
second-opinion       independent high-risk review
```

Superpowers owns the development process (brainstorming → plan → TDD →
verification). A specialized plugin supplies *evidence* inside that process.
None of them is a substitute for executable tests.

## Routing triggers

| Trigger | Tool |
|---|---|
| Cross-module change, "what breaks if I change X" | GitNexus |
| Financial/scoring arithmetic, units, scaling | dimensional-analysis |
| External library behavior/version uncertainty | Context7 |
| FastAPI route/auth/API-security change | 42Crunch |
| Frontend/browser/runtime bug | Chrome DevTools |
| High-risk trading/risk/security change | second-opinion |
| Kalshi field/endpoint/message semantics | `docs/kalshi/` + `kalshi-contract-review` (NOT Context7) |

**Never invoke everything for every task.** Most edits need none of these.

## Actual invocations

These are the real, verified entry points. No wrapper slash-commands were
created — each plugin already ships its own skills, and duplicating them
would just add drift.

| Tool | How you actually invoke it |
|---|---|
| GitNexus | `gitnexus-impact-analysis`, `gitnexus-exploring`, `gitnexus-debugging`, `gitnexus-refactoring`, `gitnexus-pr-review`, `gitnexus-taint-analysis`, `gitnexus-pdg-query`, `gitnexus-cli`, `gitnexus-guide` skills + `npx gitnexus@latest <cmd>` |
| Context7 | MCP tools only (`plugin:context7:context7`) — no skills |
| dimensional-analysis | `dimensional-analysis` skill (+ 5 sub-agents) |
| 42Crunch | `42crunch-audit`, `42crunch-scan`, `42crunch-setup`, `generate-oas`, `42crunch-api-security-testing` skills |
| Chrome DevTools | MCP tools + `chrome-devtools`, `chrome-devtools-cli`, `troubleshooting`, `memory-leak-debugging`, `debug-optimize-lcp`, `a11y-debugging` skills |
| second-opinion | `second-opinion` skill (shells out to an external LLM CLI) |

## Repository routing map

Real paths in this repo, not hypothetical ones:

| Area | Real paths | Primary tools |
|---|---|---|
| Config/tuning | `config/settings.yaml`, `services/config/`, `config_bounds.py`, `config_overrides.py`, `config_performance.py` | GitNexus, dimensional-analysis |
| Advisory | `services/advisory/` | GitNexus, dimensional-analysis, second-opinion |
| Whale calibration | `services/whale_calibration/` | GitNexus, **dimensional-analysis (critical)**, second-opinion |
| Confidence scoring | `services/confidence_scoring.py` | GitNexus, **dimensional-analysis (critical)** |
| Strategy | `services/strategy_engine.py` (`kelly_scaled_max_size`, `evaluate`) | GitNexus, **dimensional-analysis (critical)**, second-opinion |
| Risk/sizing | `services/risk_manager.py` (`max_trade_size`, `check_total_exposure`, `check_daily_loss`) | GitNexus, dimensional-analysis, **second-opinion (critical)** |
| Execution/broker | `services/paper_broker.py`, `services/position/`, `services/exits/`, `services/shadow_mode.py` | GitNexus, **second-opinion (critical)** |
| Kalshi client | `services/kalshi/`, `kalshi_client.py`, `kalshi_account_client.py`, `kalshi_trade_ws.py`, `kalshi_fees.py` | `docs/kalshi/` first, then GitNexus, second-opinion |
| FastAPI routes | `main.py` (93 paths) | **42Crunch (critical)**, Context7 |
| Frontend | `frontend/src/js/`, `static/` | **Chrome DevTools (critical)** |
| Backtesting/analytics | `services/backtest/`, `services/analytics/` | GitNexus, dimensional-analysis |

## Tool-specific usage

### GitNexus — structural evidence
Indexed knowledge graph of this repo (6.6k nodes / 14.3k edges).
```bash
npx gitnexus@latest impact  <symbol>   # blast radius — run BEFORE substantial edits
npx gitnexus@latest context <symbol>   # callers, callees, processes
npx gitnexus@latest trace <from> <to>  # path between two symbols
npx gitnexus@latest status             # is the index current?
npx gitnexus@latest analyze            # re-index (after significant merges)
```
Use before changing advisory, calibration, confidence, strategy, risk,
sizing, P&L, execution, shadow/live mode, Kalshi client, or shared state.
**Do not run it for trivial or single-file changes.**
The index is stale-checked manually — `status` after pulling/merging;
`analyze` when it reports drift. (Its auto-freshness hook was deliberately
not enabled; see "Idle overhead" below.)

### dimensional-analysis — trading math correctness
Apply *after* implementation, *before* completion, on code touching
probability, prices, cents/dollars, contracts, bankroll, exposure,
percentages, P&L, EV, fees, calibration, weights, thresholds, normalization.

Project unit conventions:
```
Probability  0..1 normalized      Percent      0..100 unless normalized
PriceCents   cents/contract       DollarPrice  dollars/contract
Contracts    count                Notional/Bankroll/Exposure  dollars
Confidence   dimensionless        Weight       dimensionless
Fee          dollars unless explicitly labeled otherwise
```
This repo has already shipped two real unit/derivation bugs (the no-side
`1 - price` inversion and the `equity - starting_bankroll` mislabel — see
CLAUDE.md's "Bug pattern to watch for"). That is exactly this tool's target
class. Do not restructure architecture merely to satisfy annotations.

### Context7 — external library docs
For FastAPI, Pydantic, asyncio, HTTP/WebSocket clients, cryptography, SQLite,
frontend and testing libraries: determine the project's installed version,
then pull version-specific docs, then implement.

**Kalshi exception:** `docs/kalshi/` is canonical for Kalshi's own API.
Context7 must never override it.

### 42Crunch — API security
This app's OpenAPI is **not served publicly** (nginx proxies only `/api/` and
`/auth/`; `/openapi.json` returns 404 through the web container — by design).
Generate the spec instead:
```bash
ddev exec -s fastapi python -c "import json,main; open('/app/build/openapi.json','w').write(json.dumps(main.app.openapi()))"
```
Run for: new public endpoints, auth/authorization changes, account APIs,
trading APIs, config-mutation APIs, request-model security changes, major
OpenAPI changes, pre-live security review. **Not after every Python edit.**
Never auto-rewrite application code just to clear a finding.

### Chrome DevTools MCP — runtime browser evidence
For browser-facing bugs prefer runtime evidence over static speculation:
```
reproduce → console → network → request/response → DOM/runtime state
→ (GitNexus if backend tracing needed) → hypothesis → fix → reproduce → verify
```
App URL: `https://kalshi-whale-poc.ddev.site:8443` (port 8443, not 443).
Use for dashboard bugs, frontend/backend integration, failed API calls, JS
exceptions, DOM/rendering, network + WebSocket behavior, perf regressions.
Not for backend-only work.

### second-opinion — independent high-risk review
Reserve for: live order placement/cancellation, live-trading activation,
kill switches, bankroll limits, risk controls, position sizing, fee/EV/P&L
math, whale-calibration or advisory redesign, auth/security, major
architecture, irreversible migrations.
Provide goal, requirements, diff, invariants, tests, known risks, specific
questions. **Do not blindly accept its output** — weigh findings against
tests, runtime evidence, `docs/kalshi/`, and project requirements.

## Cooperation examples

**Trading sizing change**
`Superpowers → GitNexus (blast radius) → Context7 if a library is involved →
TDD → dimensional-analysis → tests → second-opinion → verification`

**Dashboard API failure**
`Superpowers systematic-debugging → Chrome DevTools (console/network) →
GitNexus if backend tracing needed → fix → tests → Chrome reproduction →
verification`

**Rename a CSS class**
Normal lightweight workflow. No specialized plugin.

## Superpowers lifecycle integration

- **Brainstorming/design** — GitNexus for architecture/dependency evidence;
  Context7 where an external library shapes the design.
- **Planning** — add explicit specialized verification tasks only where
  justified (blast-radius check, dimensional analysis, 42Crunch pass,
  Chrome runtime verification, second-opinion review).
- **TDD** — specialized tools never replace executable tests.
- **Systematic debugging** — route by evidence type: dependency → GitNexus;
  browser/runtime → Chrome DevTools; library behavior → Context7; math/
  scaling → dimensional-analysis; API security → 42Crunch.
- **Verification before completion** — ask "does this change trigger a
  specialized verification requirement?" If yes, run it.

## Idle overhead

Always-on cost is ~3.5k tokens across all plugins. Keep it that way:
- No GitNexus/42Crunch/dimensional-analysis on every edit.
- GitNexus's auto-augment + freshness hooks (`Grep|Glob|Bash`) were
  **deliberately left disabled** — they spawn a node process on every search
  and Bash call. Invoke GitNexus deliberately instead.
- Chrome DevTools only for browser-facing work; second-opinion only for
  genuinely consequential changes.

## Known setup gaps (as of 2026-08-25)

- **second-opinion** requires an external LLM CLI (OpenAI Codex or Gemini),
  neither installed; its bundled `codex` MCP therefore fails to connect.
  Skill is discoverable; reviews need a user decision on the external account.
- **42Crunch** requires a platform/trial token in `~/.42crunch/conf/env` plus
  the `42c-ast` binary — neither configured. Run `/42crunch-setup` to
  provision (needs user credentials).
