# Specialized tooling — effective toolchain and routing policy

Installed 2026-08-25. **Capability-verified 2026-08-25 (Session B)** against a
fresh WSL boot, with every classification below backed by a real invocation,
not by `claude plugin list` output.

This file is the authoritative router for **which specialized tool to reach
for**. It does not replace any existing rule:
`.claude/rules/branching-and-ci.md` still owns git/CI,
`.claude/rules/kalshi-integration-authority.md` still owns Kalshi semantics,
and `.claude/rules/quality-capabilities.md` still owns this repo's own
`.claude/skills/*`.

## The two rules that matter most

**1. Route by capability state, not installation state.**

```
ACTIVE            → use when its trigger applies
LIMITED           → use only within its working subset
BLOCKED_EXTERNAL  → skip SILENTLY, use the fallback
BROKEN_LOCAL      → repair only if the capability is materially needed now
NOT_NEEDED        → ignore
```

**2. Optional specialized integrations fail open, not closed.**

If an optional plugin rate-limits, loses auth, fails to start, or needs a
subscription that isn't there: fall back to the documented alternative,
continue the work, and mention the degraded capability only if it materially
changed what you could verify. **Never halt normal development because an
optional plugin is unavailable, and never re-litigate a BLOCKED_EXTERNAL
tool every session.** The only non-optional gates are the ones project
policy names explicitly (Kalshi doc authority, CI, safety invariants).

Superpowers owns the development process (brainstorming → plan → TDD →
verification). A specialized plugin supplies *evidence* inside that process.
**None of them is a substitute for executable tests.**

---

# Effective toolchain

## Active

### Superpowers — PRIMARY development process
User-scope, v6.3.0. Owns brainstorming/design, planning, TDD, systematic
debugging, implementation workflow, verification, code review, worktrees.
Verified: skills load and execute. Never bypass it in favor of a specialist.

### GitNexus — structural/dependency evidence
Local knowledge graph, no external account. Invoke deliberately via the
`gitnexus-*` skills or `npx gitnexus@latest <cmd>`.

```bash
npx gitnexus@latest impact  <symbol>   # blast radius — BEFORE substantial edits
npx gitnexus@latest context <symbol>   # callers, callees, processes
npx gitnexus@latest trace <from> <to>  # path between two symbols
npx gitnexus@latest status             # is the index current?
```

Use before changing advisory, calibration, confidence, strategy, risk,
sizing, P&L, execution, shadow/live mode, Kalshi client, or shared state.
**Do not run it for trivial or single-file changes.**

**⚠️ Index-integrity check — this repo has already been burned once.**
A partially-completed `analyze` leaves an `incrementalInProgress` flag and a
`LadybugDB ... WAL checkpoint` error, after which queries still return
`status: ok` and `epistemic: "exact"` while emitting a **target-independent
whole-repo blob**. Observed 2026-08-25: `composite_confidence` and
`kelly_scaled_max_size` returned byte-identical impact sets (51 direct, 21
processes, `risk: CRITICAL`), including impossible edges such as a
JavaScript file `CALLS`-ing a Python function. Post-repair the same query
correctly returned `impactedCount: 2, risk: LOW`.

Detection and repair:
```bash
npx gitnexus@latest clean
GITNEXUS_WAL_CHECKPOINT_THRESHOLD=67108864 npx gitnexus@latest analyze --force --skip-agents-md
```
**Smell test before trusting any GitNexus answer:** if two unrelated symbols
give the same impact set, or a result contradicts a 10-second `grep`, the
index is bad — rebuild, don't reason from it. `epistemic: "exact"` is not
evidence of correctness. Confirm consequential answers against source.

Optional cloud/embedding features are deliberately **not** enabled
(`--embeddings`); `.gitnexusrc` keeps `indexOnly: true` so GitNexus never
injects competing `CLAUDE.md`/`AGENTS.md`/skills.

Its auto-augment and freshness hooks are disabled (verified 2026-08-28:
`~/.claude/settings.json` carries only `sql_guard.py`). Invoke GitNexus
deliberately per the routing rules; nothing runs on every tool call.

### dimensional-analysis — trading-math correctness
Local skill (trailofbits 3.0.1), no credentials. Apply *after*
implementation, *before* completion, on code touching probability, prices,
cents/dollars, contracts, bankroll, exposure, percentages, P&L, EV, fees,
calibration, weights, thresholds, normalization.

```
Probability  0..1 normalized      Percent      0..100 unless normalized
PriceCents   cents/contract       DollarPrice  dollars/contract
Contracts    count                Notional/Bankroll/Exposure  dollars
Confidence   dimensionless        Weight       dimensionless
Fee          dollars unless explicitly labeled otherwise
```
This repo has shipped two real unit/derivation bugs (the no-side `1 - price`
inversion and the `equity - starting_bankroll` mislabel — see CLAUDE.md's
"Bug pattern to watch for"). That is exactly this tool's target class. Check
both **dimensional consistency** and **range/domain invariants** — a formula
can be dimensionally perfect and still produce a negative position size when
an unbounded config dial exceeds its assumed range. Do not restructure
architecture merely to satisfy annotations.

### Chrome DevTools MCP — browser-facing evidence
**Status: ACTIVE** (repaired and re-verified 2026-08-25, Session C).

App URL: `https://kalshi-whale-poc.ddev.site:8443` (port 8443, not 443);
`ddev start` first if the containers are down. Use for dashboard bugs,
frontend/backend integration, failed API calls, JS exceptions,
DOM/rendering, network + WebSocket behavior, perf regressions. Not for
backend-only work.

Acceptance test re-run end to end after the WSLg repair: `new_page` →
`list_console_messages` → `list_network_requests` → `get_network_request`.
Headful launch now succeeds (page user-agent reports `X11; Linux x86_64`,
i.e. a real X server), title `Whale Signal — Paper Trading Terminal`, valid
mkcert TLS chain with no `--acceptInsecureCerts`, and browser-originated
FastAPI calls all `200 application/json` through the nginx proxy
(`/api/config`, `/api/session`, `/api/state`,
`/api/position-netting/groups`). The only console error on a clean load is
a cosmetic `GET /favicon.ico 404` — no such file is served, and nothing
depends on one; it is not an app fault and does not need chasing.

**Prior failure, kept because the diagnosis was counter-intuitive:** the
MCP's default launch is *headful*, so it died with `Missing X server to
start the headful browser` once `guiApplications=false` in
`C:\Users\davidf\.wslconfig` removed WSLg on the 2026-08-25 reboot. The
MCP server itself connected fine throughout — **a connected MCP does not
prove the underlying capability works.** Everything except the X server had
already been verified headless. Repair was `guiApplications=true` plus
`wsl --shutdown`; confirm with a non-empty `$DISPLAY`. Do **not** "fix" a
recurrence by editing plugin cache files or registering a second, duplicate
chrome-devtools MCP — one canonical integration only.

Fallback if it regresses: `curl` against the nginx proxy, the existing
browser E2E suite in Woodpecker, and `.claude/skills/frontend-verification`.

## Limited

### Context7 — external library docs
**Anonymous/shared rate limits.** Works without an account; do not create one
to raise limits. Verified retrieving version-relevant FastAPI docs.

For FastAPI, Pydantic, asyncio, HTTP/WebSocket clients, cryptography,
SQLite, frontend and testing libraries: determine the installed version
first, then pull docs, then implement. This project currently runs
FastAPI 0.134.0 / Pydantic 2.13.4 / Python 3.13.15.

If rate-limited or unavailable → go straight to official library docs.
**Never block work on Context7.**

**Kalshi exception:** `docs/kalshi/` is canonical for Kalshi's own API.
Context7 must never override it.

## Installed but inactive — do not invoke

### 42Crunch
**Status: BLOCKED_EXTERNAL.**
Reason: no credential store and no binary — `~/.42crunch/` does not exist and
`42c-ast` is not on PATH. Audit/scan are platform-backed operations; there is
no meaningful local-only subset.
**Do not invoke during normal development. Do not make it a completion gate
for API work. Do not repeatedly ask the user to configure it.**
Activation condition: a 42Crunch platform account + token in
`~/.42crunch/conf/env` plus the `42c-ast` binary (`/42crunch-setup`) — a user
decision involving an external account, out of scope for routine work.

Fallback for FastAPI/API-security work:
```
OpenAPI/FastAPI schema inspection · authn tests · authz tests · route tests
threat-oriented code review · tools.quality_audit's API-contract checks
```
Spec generation, if ever needed:
`ddev exec -s fastapi python -c "import json,main; open('/app/build/openapi.json','w').write(json.dumps(main.app.openapi()))"`

### second-opinion
**Status: BLOCKED_EXTERNAL.**
Reason: it shells out to an external reviewer CLI; v1.7.0 supports OpenAI
Codex CLI and Google Gemini CLI, and **neither is installed** (its bundled
`codex` MCP therefore shows `✘ Failed to connect` in `claude mcp list` —
expected, not a fault to chase).
**Do not make external-model review a completion gate. Do not weaken
correctness standards because it is unavailable.**
Activation condition: an installed *and authenticated* Codex or Gemini CLI —
a paid external account, so a user decision.

Fallback for high-risk review (live orders, kill switches, bankroll limits,
sizing, fee/EV/P&L math, auth, irreversible migrations):
```
superpowers:requesting-code-review
superpowers:verification-before-completion
an independent internal review pass
tests · GitNexus where applicable · dimensional-analysis where applicable
```

---

# Repository routing map

Real paths in this repo. Blocked tools are deliberately absent.

| Area | Real paths | Route to |
|---|---|---|
| Config/tuning | `config/settings.yaml`, `services/config/`, `config_bounds.py`, `config_overrides.py`, `config_performance.py` | GitNexus, dimensional-analysis |
| Advisory | `services/advisory/` | GitNexus, dimensional-analysis |
| Whale calibration | `services/whale_calibration/` | GitNexus, **dimensional-analysis (critical)** |
| Confidence scoring | `services/confidence_scoring.py` | GitNexus, **dimensional-analysis (critical)** |
| Strategy | `services/strategy_engine.py` (`kelly_scaled_max_size`, `evaluate`) | GitNexus, **dimensional-analysis (critical)** |
| Risk/sizing | `services/risk_manager.py` | GitNexus, **dimensional-analysis (critical)**, internal review pass |
| Execution/broker | `services/paper_broker.py`, `services/position/`, `services/exits/`, `services/shadow_mode.py` | GitNexus, internal review pass |
| Kalshi client | `services/kalshi/`, `kalshi_client.py`, `kalshi_account_client.py`, `kalshi_trade_ws.py`, `kalshi_fees.py` | `docs/kalshi/` **first**, then `kalshi-contract-review`, then GitNexus |
| FastAPI routes | `main.py` | Context7 (FastAPI 0.134.0), route/authn/authz tests, `tools.quality_audit` |
| Frontend | `frontend/src/js/`, `static/` | `frontend-verification`, Chrome DevTools |
| Backtesting/analytics | `services/backtest/`, `services/analytics/` | GitNexus, dimensional-analysis |

## Worked routings

**Audit the whale calibration engine**
`Superpowers → GitNexus (verify index sane first) → dimensional-analysis → tests`

**Change sizing from fixed contracts to bankroll percentage**
`Superpowers → GitNexus blast radius → TDD → dimensional-analysis → tests → internal review pass`

**Dashboard is making a failing request**
`superpowers:systematic-debugging → Chrome DevTools (console + network table) → GitNexus if backend tracing is needed → fix → reproduce → verify`

**Add an authenticated FastAPI trading route**
`Superpowers → GitNexus if useful → Context7 for FastAPI specifics → authn/authz + route tests`
(42Crunch is blocked; its absence does not gate this work.)

**Rename a CSS class**
Normal lightweight workflow. **No specialized plugin.**

## Idle overhead — keep it low

Do not let the toolchain tax every task:
- No GitNexus / 42Crunch / dimensional-analysis on every edit.
- GitNexus auto-augment + freshness hooks are disabled (verified 2026-08-28);
  invoke GitNexus deliberately.
- `gitnexus context` can return >140KB for a single symbol — prefer
  `impact --summaryOnly`, or `grep`, when that's all you need.
- Chrome DevTools only for browser-facing work.
- No account checks, auth prompts, or retries for blocked plugins.
