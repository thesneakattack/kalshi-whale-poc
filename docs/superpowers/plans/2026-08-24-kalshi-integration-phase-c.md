# Kalshi Integration Phase C — Consolidate and Harden Implementation Plan

> **For agentic execution:** Use `.claude/skills/kalshi-integration-refactor/SKILL.md`.
> Phase C is prohibited until Phase A task A17 has fully passed against current HEAD.

**Goal:** Strengthen the stable Phase A boundary with selective static typing and final
interfaces, remove compatibility scaffolding, and make historically-dangerous Kalshi
contract misuse difficult or impossible without adding hot-path brittleness.

**Spec:** `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`

## Global constraints

- Do not migrate consumers a second time merely to change syntax.
- Types must serve correctness, not type-coverage vanity.
- Unknown safe vendor extensions remain tolerant and preserved.
- Closed trading semantics remain strict.
- No blanket Pydantic validation on exchange-wide messages.
- Measure any hot-path representation/conversion change.
- Phase A safety/performance baseline is the regression baseline.

---

## C0 — Re-assert the Phase A gate against current HEAD

**Files**
No implementation files unless a regression is found.

- [ ] Read the A17 commit/evidence and current git history since it landed.
- [ ] Regenerate the coupling census.
- [ ] Run architecture audit, Kalshi fixture tests, and full pytest.
- [ ] Verify no later commit reintroduced direct vendor access or legacy callers.
- [ ] Verify current mirror check recognizes current upstream index state.
- [ ] Verify trading remains disabled for test/live verification.
- [ ] If any Phase A gate regressed, stop C and repair/re-run A17 first.
- [ ] Commit nothing if clean.

**Acceptance**
C starts from a genuinely stable A state, not from historical memory that A once passed.

---

## C1 — Introduce scoped static type checking

**Files**
- Modify: `requirements-dev.txt`
- Create: `mypy.ini` or a `pyproject.toml` section according to current repo convention
- Modify: `.woodpecker/kalshi-contract-fixtures.yml` or architecture-audit pipeline to run
  the checker as a separately identifiable command
- Type-check scope: `services/kalshi/`

**Decision**
Use `mypy` unless current HEAD has already adopted another Python static checker. At
execution time, query the current stable Python-3.13-compatible release and pin the exact
version in `requirements-dev.txt`, matching the repository's dependency-pinning style.

- [ ] Add the checker dependency/config.
- [ ] Start with `services/kalshi/` and canonical contract modules; do not fail the whole
  legacy application on unrelated typing debt.
- [ ] Prove one deliberate type error in a temp/copy file fails.
- [ ] Prove valid current boundary passes.
- [ ] Add CI invocation with clear failure attribution.
- [ ] Run dependency audit if the dev dependency set changes.
- [ ] Commit: `quality: type-check Kalshi integration boundary`

**Acceptance**
Static contract mistakes can fail before runtime without forcing a repo-wide typing
rewrite.

---

## C2 — Define strict closed semantics and tolerant open vendor values

**Files**
- Create/modify: `services/kalshi/contracts/types.py`
- Modify relevant contract modules
- Tests: focused type/runtime behavior tests

**Type policy**
Closed semantics may use literal/enums where unknown means unsafe. Open vendor extension
values remain strings or tolerant wrappers.

Example:

```python
from typing import Literal

OutcomeSide = Literal["yes", "no"]
```

Do not create a closed `FeeType` enum merely to mirror the SDK if live Kalshi may add
values safely.

- [ ] Test unknown outcome side takes an explicit unknown/error path rather than guessing.
- [ ] Test unknown safe extension string survives normalization.
- [ ] Test raw payload preserves unknown fields.
- [ ] Type-check the contract package.
- [ ] Run contract tests.
- [ ] Commit: `refactor: harden Kalshi semantic value types`

**Acceptance**
Strictness matches business risk rather than vendor schema completeness.

---

## C3 — Harden trade and ticker contracts without hot-path runtime validation

**Files**
- Modify: `services/kalshi/contracts/trade.py`
- Modify: `services/kalshi/contracts/ticker.py`
- Modify direct consumer signatures only where necessary to satisfy the already-established
  Phase-A interface
- Tests: trade/ticker contract and performance-regression tests

- [ ] Capture operation-count or existing instrumentation baseline. Avoid fragile CI
  wall-clock thresholds unless the repository already has a stable synthetic pattern.
- [ ] Add precise static annotations for canonical trade/ticker structures.
- [ ] Keep cheap prescan before expensive conversion when the current flow benefits from it.
- [ ] Do not add Pydantic runtime validation to every exchange-wide message.
- [ ] Type consumer function signatures at the boundary.
- [ ] Run mypy, contract tests, and synthetic performance regressions.
- [ ] Compare paper-mode `trade_stream_perf` if live stream is available.
- [ ] Commit: `refactor: harden trade and ticker contracts`

**Acceptance**
High-volume flow gets stronger static guarantees with no material runtime regression.

---

## C4 — Harden fill, position, and lifecycle contracts

**Files**
- Modify: `services/kalshi/contracts/fill.py`
- Modify: `services/kalshi/contracts/position.py`
- Modify: `services/kalshi/contracts/lifecycle.py`
- Modify: `services/account_positions.py`,
  `services/whale_stream/whale_stream_handlers.py` signatures only where required
- Tests: contract, trading-gate, lifecycle tests

- [ ] Encode canonical identity/ticker semantics so consumers cannot choose REST vs WS
  aliases themselves.
- [ ] Encode lifecycle event/result availability conservatively; never imply `determined`
  is final.
- [ ] Preserve unknown optional fields and raw payload.
- [ ] Run mypy and fixture tests.
- [ ] Run existing determined/finalized/settled regressions.
- [ ] Commit: `refactor: harden account stream contracts`

**Acceptance**
The historical fill/position/lifecycle bug classes are structurally difficult to
reintroduce.

---

## C5 — Harden account and order request-response boundaries

**Files**
- Modify: `services/kalshi/account.py`
- Modify: `services/kalshi/orders.py`
- Modify: `services/kalshi/contracts/order.py`
- Modify application execution service from A9
- Tests: account, trading-gate, contract tests

- [ ] Type read results only to the extent the application actually consumes them.
- [ ] Type create/cancel request-result semantics from current V2 docs.
- [ ] Preserve explicit runtime safety checks; typing cannot replace trading/risk gates.
- [ ] Add negative tests for invalid/unknown closed order-direction semantics.
- [ ] Run mypy, contract tests, and trading-gate tests.
- [ ] Commit: `refactor: harden Kalshi account and order contracts`

**Acceptance**
Write-capable interfaces are both type-clear and safety-gated.

---

## C6 — Selectively type public market/event/live-data results

**Files**
- Modify: `services/kalshi/public.py`
- Create/modify canonical modules only for shapes whose current consumer census shows
  repeated field access or semantic risk
- Tests: public client and market-watch contract tests

**Rule**
Do not model every Kalshi field or endpoint.

- [ ] Use current census to rank public shapes by number of consumers, alias complexity,
  financial meaning, and historical bug evidence.
- [ ] Add types for the high-value subset.
- [ ] Leave low-value/extensible objects as documented mappings when a strict model would
  add more maintenance than safety.
- [ ] Preserve raw/unknown fields where forward compatibility matters.
- [ ] Run mypy plus public/market-watch tests.
- [ ] Commit: `refactor: selectively type public Kalshi data`

**Acceptance**
Typing coverage is evidence-driven rather than an attempt to reproduce the full provider
schema.

---

## C7 — Define final gateway interfaces and clean construction/lifecycle ownership

**Files**
- Create/modify: `services/kalshi/interfaces.py`
- Modify: `services/kalshi/public.py`, `account.py`, `orders.py`, `websocket.py`
- Modify: `services/app_state.py` and cached-client construction sites
- Modify: `tools/quality_audit/resources.py`
- Tests: resource lifecycle, app-state, adapter tests

**Preferred mechanism**
Use `typing.Protocol` for consumer-facing dependency contracts where tests/fakes benefit
from structural typing. Do not add an abstract-class hierarchy without need.

- [ ] Define Protocols for used public/account/order capabilities.
- [ ] Type consumers/fakes against protocols rather than compatibility concrete classes.
- [ ] Make close/resource ownership explicit.
- [ ] Update resource scanner to final class/factory names.
- [ ] Deliberately test an unclosed final client in a temp tree.
- [ ] Run mypy and architecture audit.
- [ ] Commit: `refactor: finalize Kalshi gateway interfaces`

**Acceptance**
Consumers depend on capabilities rather than transitional concrete classes.

---

## C8 — Remove residual semantic dict consumers and compatibility facades

**Files**
Delete only when the current census proves zero callers:
- `services/kalshi_client.py`
- `services/kalshi_account_client.py`
- `services/kalshi_trade_ws.py`
- `services/kalshi/compatibility.py`
to the extent each is truly unused at current HEAD.

Also modify:
- remaining imports/callers;
- quality scanners/baselines that name deleted legacy classes;
- tests that intentionally imported compatibility names.

- [ ] Regenerate census and list every legacy caller.
- [ ] If count is nonzero, migrate each caller to final gateway/canonical contract.
- [ ] Re-run census.
- [ ] Delete a facade only at zero callers.
- [ ] Search current code/tests/CI/docs for stale legacy imports/names.
- [ ] Run full pytest, mypy, architecture audit, and Kalshi fixture tests.
- [ ] Commit: `refactor: remove legacy Kalshi facades`

**Acceptance**
No temporary old API remains by inertia.

---

## C9 — Tighten CI from migration ratchet to final invariant

**Files**
- Modify: `tools/quality_audit/kalshi_boundary.py`
- Modify: `tests/test_quality_audit.py`
- Modify: Woodpecker architecture/Kalshi contract pipeline as required
- Clean: `tools/quality_audit/baseline.json` stale migration exceptions

- [ ] Remove allowances whose only purpose was transitional facade usage.
- [ ] Turn proven high-confidence final-boundary rules into hard errors.
- [ ] Keep heuristic field scanning informational unless false-positive rate is
  demonstrably low.
- [ ] Prove deliberate SDK import, direct URL use, undocumented operation, legacy facade
  import, and type error each fail their permanent CI owner.
- [ ] Prove clean tree passes.
- [ ] Remove resolved baseline IDs with dated reasoning.
- [ ] Commit: `quality: finalize Kalshi boundary enforcement`

**Acceptance**
The final architecture is a repository invariant, not a convention.

---

## C10 — Whole-system performance and fault-injection verification

**Files**
No feature files unless a real defect is found.

- [ ] Run full pytest.
- [ ] Run scoped static type checker.
- [ ] Run `tools.quality_audit`.
- [ ] Run dedicated Kalshi contract fixture command.
- [ ] Run synthetic performance regressions.
- [ ] Run docs mirror/drift check in the appropriate safe/scheduled mode.
- [ ] Deliberate safe fault injection:
  - undocumented adapter operation;
  - direct SDK import outside boundary;
  - legacy facade import;
  - malformed/unknown closed trade side;
  - REST/WS fill-position alias mismatch fixture;
  - resource leak fixture.
- [ ] Compare REST telemetry, tick phase timing, WS messages/sec, average handler ms,
  dropped messages, and fault counts to the Phase A baseline.
- [ ] Run a 10-minute paper-mode DDEV soak with real trading disabled.
- [ ] Use `integration-audit` then `final-verification`.
- [ ] If any material regression appears, fix it in a focused commit and rerun C10.

**Acceptance**
Both implementation and the guardrails themselves are proven.

---

## C11 — Remove migration scaffolding and document permanent maintenance workflow

**Files**
- Modify: `docs/kalshi/README.md`
- Modify: `docs/kalshi/CHEATSHEET.md` only for reusable established lessons
- Create/modify: `services/kalshi/CHEATSHEET.md`
- Modify: `.claude/rules/kalshi-integration-authority.md`
- Modify: `.claude/skills/kalshi-contract-review/SKILL.md`
- Update `ROADMAP.md` and `static/status.html` using `/sync-status-docs`
- Mark this dual-phase initiative complete without deleting design history

- [ ] Document final package responsibilities and public/account/order/stream boundaries.
- [ ] Document how to add/change a Kalshi endpoint/channel:
  exact docs -> adapter metadata -> fixture -> normalizer -> consumer -> CI.
- [ ] Document docs/live discrepancy handling.
- [ ] Document raw payload archival exception.
- [ ] Document type strictness/tolerance policy.
- [ ] Remove initiative-only migration exceptions/rules that are no longer needed while
  preserving permanent documentation authority.
- [ ] Run final verification matrix and `git diff --check`.
- [ ] Commit: `docs: complete document-backed Kalshi integration`

**Acceptance**
Future sessions can implement Kalshi changes from repo-native instructions without
reconstructing this conversation or the migration history.
