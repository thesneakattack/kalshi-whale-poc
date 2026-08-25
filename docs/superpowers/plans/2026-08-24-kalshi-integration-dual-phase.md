# Document-Backed Kalshi Integration Dual-Phase Program

> **Execution method:** `.claude/skills/kalshi-integration-refactor/SKILL.md` is the
> authoritative initiative orchestrator. Superpowers provides supporting TDD/debugging/
> verification/review capabilities; it does not replace the repo-specific one-task,
> verify, commit, stop workflow unless the user explicitly changes execution models.

**Goal:** Move all production Kalshi semantic interpretation behind a documented,
CI-enforced integration boundary, then selectively harden the boundary with static types
and remove migration scaffolding without regressing safety or hot-path performance.

**Architecture:** Phase A uses an incremental strangler migration: measure coupling,
repair documentation authority, introduce `services/kalshi/`, centralize semantics,
migrate consumers behind compatibility facades, and ratchet CI. Phase C begins only after
a formal stability gate and strengthens final contracts/types/interfaces rather than
rewriting the migrated application a second time.

**Tech Stack:** Python 3.13, FastAPI, asyncio, Kalshi official async SDK where compatible,
httpx/aiohttp transport as already used, pytest, existing QCP quality tools, Woodpecker
push/PR CI, GitHub scheduled/manual canaries, DDEV runtime.

**Spec:** `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`

**Audit:** `docs/superpowers/research/2026-08-24-kalshi-integration-audit.md`

## Program invariants

- Current HEAD is implementation truth; plan text never justifies duplicating shipped
  behavior.
- Exact mirrored official docs are read before changing Kalshi-shaped behavior.
- Raw payload archival is preserved; semantic interpretation is centralized.
- Public reads do not gain write capability.
- Real trading is never enabled for verification.
- Live `data/*.db` is never used by tests or destructively modified.
- Existing `services/http_client.py` rate-limit/backoff/telemetry behavior is preserved or
  moved without semantic change.
- Application selection/strategy policy does not move into vendor adapters.
- Hot-path changes require before/after evidence.
- Deterministic recurring guards are wired into CI in the same logical task.
- One numbered task = one independently testable commit unless current HEAD already
  satisfies the task, in which case verify and record that fact without manufacturing a
  commit.
- Phase C is prohibited until the Phase A gate is completely proven.

## Phase A — Contain and Document

Detailed plan:
`docs/superpowers/plans/2026-08-24-kalshi-integration-phase-a.md`

| Task | Deliverable |
|---|---|
| A0 | Reproducible Kalshi coupling/contract census and baselines |
| A1 | Production-used contract docs audited and made authoritative |
| A2 | Complete mirror sync/provenance manifest and drift detection |
| A3 | Permanent docs-authority tooling and Claude contract workflow |
| A4 | `services/kalshi/` skeleton + code-adjacent contract provenance |
| A5 | Shared transport/lifecycle extraction without behavioral change |
| A6 | Public read gateway behind compatibility facade |
| A7 | Move market-selection policy out of vendor client |
| A8 | Authenticated account reads vs order-write primitives separated |
| A9 | Move high-level execution/flatten policy above vendor adapter |
| A10 | Channel-specific WebSocket normalizers |
| A11 | Stream gateway/compatibility migration without transport rewrite |
| A12 | Final Phase-A canonical contracts for historically dangerous shapes |
| A13 | Whale/series-watcher consumers migrated |
| A14 | Account/market-watch/remaining consumers migrated |
| A15 | CI-enforced integration boundary and legacy-caller ratchet |
| A16 | Contract fixture coverage for every high-risk/used operation |
| A17 | Integration audit + paper-mode stability/performance gate |

### Phase A gate

C cannot start until A17 proves every requirement in the design spec's Phase A completion
gate. Unknown is a failure to pass the gate.

If live verification cannot be performed safely, stop after deterministic verification
and report that Phase C is blocked pending the live paper-mode gate.

## Phase C — Consolidate and Harden

Detailed plan:
`docs/superpowers/plans/2026-08-24-kalshi-integration-phase-c.md`

| Task | Deliverable |
|---|---|
| C0 | Re-assert Phase A gate against current HEAD |
| C1 | Scoped static type-checking policy/tooling |
| C2 | Closed/open vendor value types and shared primitives |
| C3 | Typed trade/ticker contracts without hot-path runtime validation |
| C4 | Typed fill/position/lifecycle contracts |
| C5 | Typed account/order request-response boundaries |
| C6 | Selective public market/event/live-data typing based on measured value |
| C7 | Gateway Protocols/interfaces and construction/lifecycle cleanup |
| C8 | Remove residual dict semantic consumers and compatibility facades |
| C9 | Tighten type/boundary CI from migration ratchet to final invariant |
| C10 | Final performance/fault-injection/contract audit |
| C11 | Remove migration scaffolding and document permanent workflow |

## Transition principle

Phase A should not create intentionally throwaway normalized APIs solely for C to replace.

When A introduces a canonical contract for a high-risk shape, design it as the likely final
interface. C may strengthen static annotations and implementation details, but consumers
should not be mechanically migrated twice without evidence.

## Final initiative acceptance

The initiative is complete only after C11 and the design spec's Phase C completion gate
are green.

After completion, normal development should not invoke this initiative skill unless
extending/reopening the architecture. Future feature work uses the permanent
`kalshi-integration-authority` rule plus `kalshi-contract-review` whenever Kalshi semantics
are touched.
