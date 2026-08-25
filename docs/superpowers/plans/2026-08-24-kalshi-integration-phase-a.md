# Kalshi Integration Phase A — Contain and Document Implementation Plan

> **For agentic execution:** Use `.claude/skills/kalshi-integration-refactor/SKILL.md`.
> Execute exactly one numbered task, verify, commit, report, and stop. Do not substitute
> Superpowers subagent/worktree/ledger orchestration unless the user explicitly requests it.

**Goal:** Establish a documented, measurable, CI-enforced Kalshi integration boundary and
migrate production consumers to it without behavior, safety, or performance regression.

**Architecture:** Incremental strangler migration behind compatibility facades. Centralize
vendor semantics, preserve raw payloads, move application policy out of vendor clients,
and make the documentation mirror authoritative for every used contract.

**Tech Stack:** Python 3.13, pytest, DDEV, existing Kalshi SDK/httpx/WebSocket stack,
QCP `tools.quality_audit`, Woodpecker CI.

**Spec:** `docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`

## Global constraints

- Never enable real trading.
- Never weaken trading/risk/security gates.
- Never test against live repository `data/*.db`.
- Read exact mirrored Kalshi docs before editing contract semantics.
- Preserve raw-payload archival.
- Preserve existing REST telemetry/rate-limiting behavior.
- No expensive runtime validation on exchange-wide hot paths without measurement.
- Deterministic recurring checks belong in CI.
- Re-ground exact filenames/interfaces against current HEAD before each task.

---

## A0 — Generate the Kalshi coupling and contract census

**Files**
- Create: `tools/kalshi_census.py`
- Create: `tests/test_kalshi_census.py`
- Create/generated: `docs/kalshi/used-contracts.json`
- Modify if needed: `tools/quality_audit/api_usage.py`
- Read: `services/kalshi_client.py`, `services/kalshi_account_client.py`,
  `services/kalshi_trade_ws.py`, `services/http_client.py`,
  `services/whale_stream/**`, `services/market_watch/**`,
  `services/whalewatchers/kalshi_trade_tape.py`, `services/account_positions.py`,
  `services/series_watcher.py`

**Produces**
A deterministic JSON census with:
- direct SDK imports;
- direct Kalshi HTTP/WS host usage;
- legacy wrapper imports/construction sites;
- wrapper method call sites;
- known Kalshi field reads in production;
- fixture source-doc inventory;
- hot-path/cold-path classification metadata for known entry points.

- [ ] Write fixture-repo tests that create a temporary miniature source tree and prove the
  census detects a direct SDK import, wrapper call, vendor-field read, and fixture doc
  reference.
- [ ] Run:
  `ddev exec -s fastapi python3 -m pytest tests/test_kalshi_census.py -v`
  and confirm RED before implementation.
- [ ] Implement AST-based census generation. Keep uncertain findings informational rather
  than inventing semantic certainty.
- [ ] Add CLI:
  `python -m tools.kalshi_census --repo-root . --json-out docs/kalshi/used-contracts.json`
- [ ] Run the CLI against current HEAD and manually inspect every category with nonzero
  counts.
- [ ] Record the current legacy-caller count and direct-access count in the generated file;
  do not hand-edit those numbers.
- [ ] Run targeted tests, `python -m tools.quality_audit`, `git diff --check`.
- [ ] Commit: `audit: inventory Kalshi integration coupling`

**Acceptance**
The census is reproducible from a clean checkout and gives future tasks a mechanical blast
radius rather than relying on memory or grep.

---

## A1 — Make every production-used contract locally authoritative

**Files**
- Modify: exact `docs/kalshi/*.md` files identified by A0 as production dependencies
- Modify: `tests/fixtures/kalshi/*` metadata where a fixture points to a merged/summary
  source rather than the exact upstream resource
- Modify: `docs/kalshi/CHEATSHEET.md` only for app-specific discrepancy notes, not to copy
  verbatim docs

**Consumes**
`docs/kalshi/used-contracts.json`

- [ ] For each used REST operation/WS channel/semantic rule in the census, map the current
  code behavior to its exact official resource from `docs/kalshi/llms.txt`.
- [ ] Fail the task if a used contract has no exact local official source.
- [ ] Replace any production-used curated summary with the exact current upstream body.
  Keep application commentary in CHEATSHEET rather than inside the mirrored body.
- [ ] Split any production-used merged file into one file per upstream source and update
  code/fixture documentation pointers.
- [ ] Add/adjust tests in `tests/test_kalshi_contracts.py` so every changed fixture source
  pointer resolves to an existing mirrored file.
- [ ] Run all Kalshi contract tests:
  `ddev exec -s fastapi python3 -m pytest -v tests/test_kalshi_contracts.py tests/test_kalshi_client.py tests/test_kalshi_account_client.py tests/test_kalshi_trade_ws.py`
- [ ] Regenerate the census and confirm every **used** contract is locally sourced.
- [ ] Commit: `docs: make used Kalshi contracts authoritative`

**Acceptance**
No production-used Kalshi semantic behavior depends solely on a curated local summary or
missing local source.

---

## A2 — Replace README-derived mirror provenance with a complete machine manifest

**Files**
- Modify: `tools/kalshi_docs_drift.py`
- Create: `tools/kalshi_docs_sync.py`
- Modify/generated: `docs/kalshi/upstream-manifest.json`
- Modify/generated: `docs/kalshi/README.md`
- Modify: `docs/kalshi/llms.txt`
- Add current upstream Markdown and supported OpenAPI/AsyncAPI files listed by the index
- Create/modify: `tests/test_kalshi_docs_drift.py`
- Create: `tests/test_kalshi_docs_sync.py`
- Modify scheduled workflow only if command/outputs change:
  `.github/workflows/docs-drift-check.yml`

**Produces**
A manifest-driven mirror where each resource has one source URL, one local path, kind, and
normalized content hash.

- [ ] Write tests proving manifest generation does not parse README prose.
- [ ] Write tests proving index additions/removals are detected.
- [ ] Write tests proving two same-basename URLs resolve deterministically without merging.
- [ ] Write tests proving content-hashed mirror files contain upstream body rather than a
  local `Source:` prefix.
- [ ] Write tests for supported YAML/spec resources listed by `llms.txt`.
- [ ] Run those tests RED.
- [ ] Implement `kalshi_docs_sync` with an explicit `--check` and `--write` mode. `--check`
  must never modify the tree.
- [ ] Convert the current manifest to the new schema and refresh the mirror.
- [ ] Generate README from the manifest for human navigation.
- [ ] Update `kalshi_docs_drift` to consume the manifest directly.
- [ ] Deliberately alter one temp mirrored body/index entry and prove `--check` fails.
- [ ] Run valid-tree `--check` and tests.
- [ ] Ensure scheduled CI still invokes network-dependent work only in scheduled/manual
  context.
- [ ] Commit: `build: make Kalshi docs mirror manifest-driven`

**Acceptance**
The local mirror can prove index and content drift without special-casing curated summaries
as uncheckable.

---

## A3 — Permanently enforce documentation-first Kalshi development

**Files**
- Modify: `.claude/skills/kalshi-contract-review/SKILL.md`
- Modify if needed: `.claude/rules/kalshi-integration-authority.md`
- Create: `tools/quality_audit/kalshi_contract_docs.py`
- Modify: `tools/quality_audit/__main__.py`
- Modify: `tests/test_quality_audit.py`
- Modify: `.woodpecker/quality-architecture-audit.yml` only if the existing aggregate
  `tools.quality_audit` invocation does not automatically include the new scanner

**Rule**
Every public adapter/normalizer operation introduced by this initiative must have
code-adjacent exact local doc metadata.

- [ ] Write scanner tests against temp modules:
  - operation with valid `CONTRACT_DOCS` -> no finding;
  - missing mapping -> high-confidence error;
  - nonexistent local doc -> high-confidence error;
  - stale mapping to nonexistent operation -> report according to provable context.
- [ ] Run scanner tests RED.
- [ ] Implement scanner and register it in the existing audit CLI.
- [ ] Strengthen `kalshi-contract-review` to require exact doc identification, fixture
  comparison, and discrepancy recording.
- [ ] Prove deliberate isolated failure makes the architecture audit nonzero.
- [ ] Prove current valid repo state passes or baselines only reviewed pre-existing
  findings.
- [ ] Commit: `quality: enforce documented Kalshi contracts`

**Acceptance**
"Claude should read the docs" is supported by a permanent repo rule and a deterministic
code/documentation guard.

---

## A4 — Create the integration package and provenance interface

**Files**
- Create: `services/kalshi/__init__.py`
- Create: `services/kalshi/contracts/__init__.py`
- Create: `services/kalshi/provenance.py`
- Create: `tests/test_kalshi_provenance.py`

**Interfaces**
Provide a lightweight, static/inspectable contract metadata shape without expensive
runtime behavior. Adapter modules expose:

```python
ContractDocs = tuple[str, ...]
CONTRACT_DOCS: dict[str, ContractDocs]
```

- [ ] Write tests for valid local paths, duplicate operation keys, and generated
  human-readable inventory.
- [ ] Implement the smallest package skeleton; do not move transport yet.
- [ ] Run contract-doc scanner and provenance tests.
- [ ] Commit: `refactor: establish Kalshi integration package`

**Acceptance**
The boundary exists without changing any production caller.

---

## A5 — Move shared vendor transport/lifecycle mechanics without semantic change

**Files**
- Create or adapt: `services/kalshi/transport.py`
- Modify: `services/http_client.py` only where the integration wrapper currently owns
  vendor-specific helper code
- Modify: legacy wrappers to delegate
- Modify: `tools/quality_audit/resources.py`
- Tests: `tests/test_http_client.py`, `tests/test_kalshi_client.py`,
  `tests/test_kalshi_account_client.py`, resource-scanner tests

**Constraints**
Do not duplicate connection pools or rate limiters. Existing `services/http_client.py`
remains the shared runtime mechanism unless current HEAD has deliberately superseded it.

- [ ] Capture baseline REST telemetry behavior and close/lifecycle tests.
- [ ] Write delegation tests that prove retries, endpoint labels, and close behavior are
  unchanged.
- [ ] Move only vendor-adapter glue needed by later package modules.
- [ ] Update the resource-lifecycle scanner so new integration class names cannot silently
  escape leak detection.
- [ ] Deliberately create an unclosed new client in a temp source fixture and prove scanner
  failure.
- [ ] Run targeted tests and architecture audit.
- [ ] Commit: `refactor: centralize Kalshi transport ownership`

**Acceptance**
Transport behavior is one implementation, not old and new parallel stacks.

---

## A6 — Introduce the public read gateway behind the existing facade

**Files**
- Create: `services/kalshi/public.py`
- Modify: `services/kalshi_client.py` into a compatibility facade/delegator
- Tests: `tests/test_kalshi_client.py`, `tests/test_kalshi_contracts.py`
- Regenerate: `docs/kalshi/used-contracts.json`

**Interfaces**
The gateway covers only operations actually used at current HEAD. Do not implement unused
Kalshi endpoints.

- [ ] For each migrated public method, read its exact local docs and add `CONTRACT_DOCS`.
- [ ] Write delegation/behavior tests before moving implementation.
- [ ] Move request/response envelope handling and SDK/raw-HTTP compatibility logic into the
  new gateway.
- [ ] Keep compatibility method signatures stable for unmigrated callers.
- [ ] Ensure `get_series_list`-style live/SDK discrepancy handling remains tolerant and
  documented.
- [ ] Regenerate census; direct SDK/vendor access outside the new boundary must not
  increase.
- [ ] Run full public-client and contract tests.
- [ ] Commit: `refactor: add documented public Kalshi gateway`

**Acceptance**
Public wire semantics have one implementation under `services/kalshi/`.

---

## A7 — Move discovery/watchlist policy out of the vendor gateway

**Files**
- Modify: `services/kalshi/public.py`
- Modify: `services/kalshi_client.py`
- Create or use focused policy module under `services/market_watch/`, likely
  `services/market_watch/selection.py`
- Modify: `services/market_watch/market_fetch.py`,
  `services/market_watch/discovery_cache.py` as required
- Tests: existing market-watch tests plus focused selection tests

**Move**
- candidate filtering/ranking policy;
- round-robin parent-series selection;
- top-volume/watchlist policy.

**Keep in gateway**
Efficient documented batching/fetching only.

- [ ] Freeze current selection behavior in pure unit tests before moving it.
- [ ] Move the pure selection algorithm into market-watch scope.
- [ ] Change market-watch callers to combine vendor fetch plus app selection.
- [ ] Remove policy implementation from the new public gateway; compatibility facade may
  temporarily delegate only if a caller still requires the old signature.
- [ ] Verify watchlist ordering/grouping/caps exactly match baseline tests.
- [ ] Regenerate census and record facade caller reduction.
- [ ] Commit: `refactor: move market selection policy out of Kalshi client`

**Acceptance**
The Kalshi adapter does not decide what the strategy should watch.

---

## A8 — Split authenticated account reads from order-write primitives

**Files**
- Create: `services/kalshi/account.py`
- Create: `services/kalshi/orders.py`
- Modify: `services/kalshi_account_client.py` into compatibility facade
- Tests: `tests/test_kalshi_account_client.py`, `tests/test_kalshi_contracts.py`,
  `tests/test_trading_gate.py`

**Interfaces**
`AccountGateway`: balance, positions, fills, order-history reads actually used.
`OrderGateway`: create/cancel and any currently-used write primitive.

- [ ] Read exact local portfolio/order docs, including fixed-point/order-direction docs.
- [ ] Write tests proving public/account read objects do not expose write methods.
- [ ] Write tests proving every existing real-order safety gate still blocks writes when
  disabled.
- [ ] Move SDK/auth/request-shape semantics without changing user-facing safety policy.
- [ ] Keep compatibility facade for current `services.app_state.account` wiring.
- [ ] Run trading-gate and account contract tests.
- [ ] Commit: `refactor: separate Kalshi account reads and order writes`

**Acceptance**
Authenticated read capability and order-write capability are explicit.

---

## A9 — Move high-level execution/flatten policy above the vendor adapter

**Files**
- Create or modify the existing execution/risk-appropriate application module chosen from
  current HEAD; prefer an existing execution service over a new package when one already
  owns real-order orchestration
- Modify: `services/kalshi_account_client.py`
- Modify: `services/kalshi/orders.py`
- Modify callers of `flatten_all` or equivalent
- Tests: execution/risk/trading-gate tests

- [ ] Freeze current emergency flatten behavior in tests, including closing-side semantics,
  risk exceptions, and trading-disabled behavior.
- [ ] Move orchestration into the application execution layer.
- [ ] Make low-level order gateway calls no easier for unrelated business modules to
  access.
- [ ] Preserve current risk-halt and closing-order semantics exactly.
- [ ] Run trading-gate, execution, and order contract tests.
- [ ] Commit: `refactor: move execution policy above Kalshi adapter`

**Acceptance**
Vendor adapter exposes primitives; application execution service owns policy.

---

## A10 — Extract channel-specific semantic normalizers

**Files**
- Create focused modules under `services/kalshi/contracts/`:
  - `trade.py`
  - `ticker.py`
  - `fill.py`
  - `position.py`
  - `lifecycle.py`
- Modify: `services/kalshi_trade_ws.py` only to delegate semantic normalization
- Tests: `tests/test_kalshi_contracts.py`, `tests/test_kalshi_trade_ws.py`

**Constraints**
Do not split socket reader/reconnect/subscription mechanics merely for aesthetics.

- [ ] Feed existing doc-backed fixtures directly into each new normalizer.
- [ ] Write failure tests for:
  - legacy direction-field precedence;
  - unreadable side -> unknown/no guess;
  - `market_ticker`/`ticker` alias;
  - WS `trade_id` identity;
  - singular position message type;
  - determined/finalized/settled distinction.
- [ ] Run RED before implementation.
- [ ] Move exactly those semantics into normalizers.
- [ ] Preserve raw payload in the normalized result.
- [ ] Keep transport callback behavior otherwise unchanged.
- [ ] Run contract and websocket tests.
- [ ] Commit: `refactor: centralize Kalshi websocket semantics`

**Acceptance**
Known high-risk WS semantic interpretation has one implementation.

---

## A11 — Introduce the stream gateway without an unnecessary transport rewrite

**Files**
- Create/adapt: `services/kalshi/websocket.py`
- Modify: `services/kalshi_trade_ws.py` compatibility facade
- Modify: `services/app_state.py` construction only as needed
- Modify: `tools/quality_audit/resources.py`
- Tests: websocket/resource/app-state tests

- [ ] Capture baseline connection/subscription/dispatch tests.
- [ ] Move or delegate current transport implementation behind the new package boundary.
- [ ] Preserve independent trade/index socket behavior and queue/backpressure metrics.
- [ ] Preserve `close()` ownership and update resource scanner.
- [ ] Do not change handler business logic in this task.
- [ ] Run websocket tests and architecture audit.
- [ ] Commit: `refactor: move Kalshi websocket transport behind gateway`

**Acceptance**
Transport is under `services/kalshi/`, while business callbacks remain application code.

---

## A12 — Introduce final Phase-A canonical high-risk contracts

**Files**
- Modify: `services/kalshi/contracts/trade.py`
- Modify: `services/kalshi/contracts/ticker.py`
- Modify: `services/kalshi/contracts/fill.py`
- Modify: `services/kalshi/contracts/position.py`
- Modify: `services/kalshi/contracts/lifecycle.py`
- Create: `services/kalshi/contracts/order.py`
- Tests: contract tests

**Design**
Use lightweight final interfaces suitable for C to harden without forcing consumers to
migrate a second time. Prefer `TypedDict` and/or small `slots=True` dataclasses based on
measured hot-path cost.

Canonical semantics include:

```text
PublicTrade: trade_id, ticker, outcome_side, count, prices, occurred_at, raw_payload
TickerUpdate: ticker, bid/ask/price fields needed by app, exchange timestamp, raw_payload
UserFill: trade_id, ticker, side/action/count/prices/time, raw_payload
MarketPosition: ticker, position/exposure/pnl/fees fields, raw_payload
LifecycleEvent: ticker, event_type, close-time/update semantics, raw_payload
CreateOrderRequest / CancelOrderResult: documented V2 semantics
```

- [ ] Write type/normalizer unit tests from doc-backed fixtures.
- [ ] Test unknown closed semantic values do not become guessed YES/NO.
- [ ] Test safe unknown extension fields remain in raw payload.
- [ ] Measure normalizer cost on representative trade messages before and after.
- [ ] Implement canonical contracts with no blanket Pydantic hot-path validation.
- [ ] Commit: `refactor: define canonical Kalshi contracts`

**Acceptance**
A consumer can stop reading raw aliases without waiting for Phase C.

---

## A13 — Migrate whale detection and raw series capture

**Files**
- Modify: `services/whalewatchers/kalshi_trade_tape.py`
- Modify: `services/series_watcher.py`
- Modify: `services/whale_stream/whale_stream_handlers.py` only at the producer/consumer
  boundary required for trade/ticker canonical delivery
- Tests: `tests/test_whalewatchers_kalshi_trade_tape.py`,
  series-watcher tests, contract tests

- [ ] Freeze existing signal direction/notional/count-gate behavior in tests.
- [ ] Change whale provider semantic logic to consume canonical trade fields rather than
  `taker_*` vendor aliases.
- [ ] Keep cheap prescan behavior; do not add expensive object creation before the
  whale-size threshold without measurement.
- [ ] Change `series_watcher` to archive `raw_payload` while deriving canonical columns
  from the canonical event rather than reinterpreting aliases.
- [ ] Prove raw JSON persistence remains complete enough for current research workflows.
- [ ] Compare existing `trade_stream_perf` metrics in a safe paper-mode sample if the live
  stream is available.
- [ ] Regenerate census; high-risk raw semantic reads in these modules should fall.
- [ ] Commit: `refactor: migrate whale pipeline to canonical Kalshi contracts`

**Acceptance**
Whale business logic no longer knows Kalshi direction aliases.

---

## A14 — Migrate account, market-watch, stream handlers, and remaining consumers

**Files**
Primary known consumers:
- `services/account_positions.py`
- `services/whale_stream/whale_stream_handlers.py`
- `services/market_watch/market_fetch.py`
- `services/market_watch/discovery_cache.py`
- `services/market_watch/catalog_scan.py`
- `services/market_watch/event_metadata.py`
- `services/market_watch/live_status.py`
- `services/market_events/event_inspector.py`
- `services/diagnostics/routes.py`
- `services/market_catalog/routes.py`
- `services/app_state.py`
- every additional production consumer reported by the current A0 census

**Rule**
Use the current census, not this historical list, as completeness authority.

- [ ] If current census makes A14 too large for one reviewable commit, stop before editing
  and split A14 into explicit cohesive sub-tasks in this plan.
- [ ] For account positions, remove REST-vs-WS alias interpretation from presentation code.
- [ ] For stream handlers, consume canonical callback objects and stop reading known
  high-risk raw aliases.
- [ ] For market-watch, use public gateway methods while keeping discovery policy local.
- [ ] For diagnostics/routes, use gateways/factories and preserve close/lifecycle safety.
- [ ] Preserve raw payload pass-through only where diagnostics/research needs it.
- [ ] Run each module's targeted tests, then full Kalshi contract tests.
- [ ] Regenerate census and manually inspect every remaining semantic-read/direct-access
  finding.
- [ ] Commit: `refactor: migrate remaining Kalshi consumers behind boundary`

**Acceptance**
No unexplained production consumer remains outside the boundary according to the census.

---

## A15 — Turn the integration boundary into a CI ratchet

**Files**
- Create: `tools/quality_audit/kalshi_boundary.py`
- Modify: `tools/quality_audit/__main__.py`
- Modify: `tests/test_quality_audit.py`
- Modify if invocation requires it: `.woodpecker/quality-architecture-audit.yml`
- Regenerate/review: `tools/quality_audit/baseline.json` only for genuinely reviewed
  pre-existing exceptions

**Hard/high-confidence checks**
- SDK import outside allowlist;
- direct Kalshi REST/WS host usage outside boundary;
- undocumented integration operation;
- new import of legacy compatibility facade after ratchet baseline;
- specific known deprecated semantic access outside boundary when AST context is provable.

- [ ] Write isolated temp-tree tests for each failure.
- [ ] Prove valid tree passes.
- [ ] Do not create broad `ticker`/`trade_id` string bans.
- [ ] Wire scanner through existing architecture audit.
- [ ] Deliberately inject one forbidden import in a temp fixture and prove the CI command
  exits nonzero.
- [ ] Commit: `quality: enforce Kalshi integration boundary`

**Acceptance**
New leakage cannot silently re-enter on future pushes/PRs.

---

## A16 — Complete contract fixtures for the production-used surface

**Files**
- Modify/add: `tests/fixtures/kalshi/*.json`
- Modify: `tests/test_kalshi_contracts.py`
- Modify: `services/kalshi/**` `CONTRACT_DOCS` mappings as required
- Modify: `.woodpecker/kalshi-contract-fixtures.yml` if new test files are outside its
  current explicit list

- [ ] Compare current used-operation census to fixture coverage.
- [ ] Add a doc-sourced fixture for every high-risk used operation/channel without one.
- [ ] For low-risk used operations, at minimum prove request/response envelope handling and
  documented required fields through production adapter tests.
- [ ] Ensure every fixture has exact source metadata and no undocumented invented fields.
- [ ] Run the dedicated Kalshi fixture CI command locally in DDEV.
- [ ] Deliberately mutate one fixture field in a temp copy and prove the relevant production
  normalizer test fails.
- [ ] Commit: `test: complete Kalshi contract coverage`

**Acceptance**
The fixture suite covers the actual production dependency surface rather than a curated
subset.

---

## A17 — Phase A integration audit and stability gate

**Files**
- Modify generated census/report artifacts only if designed to be committed
- Update relevant module `CHEATSHEET.md` and human status/roadmap using existing
  `/sync-status-docs` conventions when the initiative milestone ships
- No feature implementation unless verification finds a real defect; fix defects as
  focused prerequisite commits before rerunning A17

- [ ] Run:
  `ddev exec -s fastapi python3 -m pytest -q`
- [ ] Run:
  `ddev exec -s fastapi python3 -m tools.quality_audit --repo-root . --baseline tools/quality_audit/baseline.json`
- [ ] Run the exact command from `.woodpecker/kalshi-contract-fixtures.yml`.
- [ ] Run docs mirror `--check` in its safe mode.
- [ ] Regenerate the coupling census and confirm:
  - no unexplained direct SDK/HTTP/WS access;
  - no new compatibility callers;
  - high-risk semantic reads centralized;
  - all used contracts documented.
- [ ] Use `integration-audit`.
- [ ] Compare current REST metrics, tick timings, and WS trade-handler metrics with A0
  baseline.
- [ ] With real trading disabled, perform a **10-minute paper-mode DDEV soak** when the
  environment is available:
  - stream remains connected/healthy;
  - no new repeating faults;
  - dropped-message behavior is no worse than baseline;
  - tick duration/rate-limit hits show no material regression;
  - `/api/quality/summary` and relevant health endpoints remain healthy or understood.
- [ ] If the safe live soak cannot be run, stop and report Phase C blocked rather than
  marking the gate complete.
- [ ] Run `git diff --check` and confirm clean/staged state.
- [ ] Commit only documentation/status changes that honestly record the proven gate:
  `docs: mark Kalshi integration Phase A stable`

**Acceptance**
Every Phase A completion-gate item in the design spec is explicitly proven. Only then is
C0 allowed.
