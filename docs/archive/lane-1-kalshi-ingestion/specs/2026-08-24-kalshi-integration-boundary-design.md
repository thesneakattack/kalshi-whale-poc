# Document-Backed Kalshi Integration Boundary Design

**Status:** Design basis for the dual-phase implementation program.

**Audit:** `docs/archive/lane-1-kalshi-ingestion/research/2026-08-24-kalshi-integration-audit.md`

## Problem

The application has accumulated substantial, correct knowledge about Kalshi, but that
knowledge is distributed across vendor wrappers, stream handlers, account presentation,
whale detection, persistence, tests, and long comments. This distribution has already
produced real failures when REST and WebSocket shapes differ or when deprecated fields
look plausible.

The project also has a large local copy of official Kalshi documentation, but its current
mirror has known summary/merge/drift gaps. The architecture must make the documentation
usable as an enforceable development source, not merely a folder Claude is asked to
remember.

## Goals

1. All new or changed Kalshi semantic behavior is derived from exact local official docs,
   not AI/model memory.
2. Vendor-specific REST/WS/SDK interpretation is centralized.
3. Application modules remain organized around autotrader concerns rather than mirroring
   the whole Kalshi website.
4. Public/read-only, authenticated/account, write-capable, and streaming capabilities stay
   explicit.
5. High-risk contracts become difficult to misuse.
6. Raw upstream payloads remain available for diagnostics/research.
7. Existing rate-limit, telemetry, safety, and persistence behavior is preserved.
8. The migration is incremental and continuously testable.
9. CI prevents new integration-boundary leakage.
10. Runtime performance on the exchange-wide stream and trading tick does not regress.

## Non-goals

- Do not implement unused Kalshi endpoints merely because docs exist.
- Do not make the application exchange-agnostic for its own sake.
- Do not add one source file per Kalshi endpoint.
- Do not create a new ORM/shared database.
- Do not alter trading strategy behavior as incidental refactor work.
- Do not weaken typed trading confirmation, `trading_enabled`, risk halt, CORS, kill
  switches, or account-safety gates.
- Do not require runtime Markdown/doc access.
- Do not run expensive runtime schema validation on every exchange-wide WebSocket message
  without measured justification.
- Do not discard raw payloads solely because a canonical representation exists.

## Source-of-truth model

### Normative contract source

Current official Kalshi documentation mirrored under `docs/kalshi/`.

For every contract used by production code, its local official source must be current
enough to support the behavior being implemented.

### Observed compatibility evidence

Safe read-only live responses and existing public canaries may reveal behavior that
contradicts or precedes docs. A docs/live mismatch is recorded as a **contract
discrepancy**, not silently resolved by declaring one source globally superior.

### Client mechanism

The official Kalshi SDK is preferred where it handles the current documented/live
contract correctly and efficiently. Raw HTTP/WS access inside the integration boundary is
allowed where the SDK is incomplete, too strict, or lacks an endpoint.

### Application knowledge

`docs/kalshi/CHEATSHEET.md`, contract fixtures, regression tests, and git history record
application-specific lessons and verified discrepancies.

### AI/model memory

Never authoritative when a local official source exists.

## Documentation mirror design

Keep `docs/kalshi/` as the project-visible corpus, but make
`docs/kalshi/upstream-manifest.json` the machine-readable provenance authority.

Each manifest resource records at least:

```json
{
  "source_url": "https://docs.kalshi.com/...",
  "local_path": "docs/kalshi/...",
  "kind": "markdown",
  "sha256": "...",
  "indexed_by": "https://docs.kalshi.com/llms.txt"
}
```

Requirements:

- one upstream resource maps to one local mirrored resource;
- no content-hashed resource contains local source-header prose;
- curated application notes live outside the verbatim mirror body;
- merged source pages are split into independent resources;
- naming collisions use deterministic, documented local names;
- current upstream index changes are detected;
- supported OpenAPI/AsyncAPI resources listed by the index are mirrored or explicitly
  classified as unsupported with a tested reason;
- README is generated or derived from the manifest rather than parsed as machine
  provenance.

`CHEATSHEET.md` remains human-authored application knowledge and is not a verbatim mirror.

## Integration boundary

The final boundary lives under:

```text
services/kalshi/
```

The exact number of submodules is determined by A0's coupling census. The minimum
capability separation is:

```text
services/kalshi/
├── public.py
├── account.py
├── orders.py
├── websocket.py
├── transport.py
├── contracts/
└── compatibility.py
```

If a file becomes too broad, split by cohesive used concern, not by blindly reproducing
the docs navigation tree.

## Anti-corruption invariant

Vendor-specific semantic interpretation belongs inside `services/kalshi/`.

Examples:

- REST-vs-WS aliases;
- deprecated field precedence;
- outcome-side vs book-side semantics;
- fixed-point parsing rules;
- message type/channel distinctions;
- response envelope differences;
- lifecycle state meaning;
- order request/response wire schema;
- SDK/live compatibility workarounds;
- documented pagination/batching constraints.

Application modules consume canonical fields/types such as `ticker`, `outcome_side`,
`count`, `trade_id`, and `occurred_at` rather than reinterpreting vendor aliases.

## Raw payload exception

Opaque raw payloads may cross the boundary for archival, observability, diagnostics,
research, and reproducibility. Application modules may store/pass `raw_payload`; they
should not re-derive vendor semantics from it.

## Canonical contract policy

Types are introduced where semantic misuse is consequential or historically proven.

Phase A may introduce the final canonical representation immediately for:

- public trade;
- ticker update;
- user fill;
- market position;
- lifecycle event;
- create-order request;
- cancel-order response.

Phase C expands/hardens only where Phase A evidence supports it.

### Closed semantics

Values such as YES/NO outcome side can be strict. Unknown values must be rejected,
quarantined, or represented as unknown rather than guessed.

### Open vendor extensions

Values such as fee-type/category/optional metadata should remain tolerant when an unknown
value can safely pass through. Preserve the raw string/field and surface discrepancies
instead of rejecting an entire response.

## Numeric policy

Do not globally convert every upstream numeric string to `Decimal` on the hot path.

- Convert where financial precision actually matters.
- Preserve upstream raw strings in raw payloads.
- Use cheap parsing for prescan/filtering where the current hot-path design requires it.
- Measure before adding richer conversion to exchange-wide message handling.

## Capability boundaries

### Public gateway

Exposes only public read operations the application actually uses. It cannot place/cancel
orders.

### Account gateway

Exposes authenticated balance/position/fill/order-history reads.

### Order gateway

Owns create/cancel/amend vendor primitives and preserves current write safety checks.

### Application execution service

Owns high-level policy such as emergency flattening/close orchestration. It composes
account/order primitives while keeping raw write capability narrow.

### Stream gateway

Owns connection/subscription/message dispatch. Channel-specific normalizers may be
separate when they improve testability. Business actions remain application concerns.

## Application policy placement

Move candidate selection/ranking, round-robin parent-series selection, watchlist caps, and
discovery policy out of the vendor client into `services/market_watch/`.

The adapter may batch/fetch efficiently, but it should not decide which markets are
strategically interesting.

## Documentation provenance in code

Avoid a second hand-maintained YAML map. Each adapter/normalizer exposes code-adjacent
documentation metadata in a static, inspectable form:

```python
CONTRACT_DOCS = {
    "get_markets": (
        "docs/kalshi/get-markets.md",
        "docs/kalshi/pagination.md",
        "docs/kalshi/fixed_point_migration.md",
    ),
}
```

A generated tool validates that referenced files exist, every public integration operation
has a doc mapping, fixture metadata points into the same official source family, and
deleted operations cannot leave stale registry entries.

## Compatibility facades

Transitional facades are allowed because this is a live, safety-sensitive system.

Rules:

1. caller count is captured at Phase A start;
2. new callers are forbidden once replacement exists;
3. each migration task reduces or maintains the count—never increases it;
4. zero callers requires facade deletion;
5. facade methods delegate without re-implementing semantics.

## CI enforcement

Extend `tools/quality_audit/` with high-confidence checks:

- direct SDK import outside allowed integration files;
- direct production Kalshi REST/WS URL use outside allowed integration files;
- undocumented public integration operation;
- new compatibility-facade consumer after the ratchet is established;
- known deprecated high-risk semantic interpretation outside the boundary where AST
  context is provable.

Do not ban generic field names globally.

Deterministic boundary checks run in the existing Woodpecker architecture-audit path.
Kalshi fixture checks remain in the dedicated contract pipeline. Network-dependent mirror
drift/public canary remains scheduled/manual.

## Testing strategy

Continue the established offline pattern:

```text
official mirrored doc
    -> fixture with source metadata
    -> production adapter/normalizer
    -> canonical result assertions
```

Do not duplicate parser logic inside tests.

Migration of a consumer must prove the same application behavior using canonical input.
Boundary CI itself gets deliberate isolated failure fixtures.

## Performance gates

Capture and compare:

- tick phase timings;
- REST call counts by normalized endpoint;
- rate-limit hits;
- WebSocket messages/sec;
- average trade-handler ms;
- dropped stream messages;
- existing CPU-related trade-stream metrics;
- request/cache behavior affected by moved code.

No abstraction is accepted if it introduces a material regression without an explicit,
measured reason.

## Phase A completion gate

Phase C cannot begin until all are true:

1. coupling census exists and is reproducible;
2. every production-used contract has authoritative local docs;
3. mirror updater/manifest can detect index/content changes;
4. direct vendor semantic interpretation is inside `services/kalshi/` or an explicitly
   documented temporary exception;
5. application selection policy has left the vendor wrapper;
6. public/account/order/stream capability boundaries exist;
7. compatibility caller counts are known and ratcheted;
8. contract fixtures cover every high-risk used wire shape;
9. architecture and Kalshi-contract CI are green;
10. full pytest is green;
11. live DB isolation is intact;
12. trading/risk/security gates are unchanged;
13. hot-path/tick metrics are not materially worse than baseline;
14. a paper-mode soak shows healthy streams/ticks with no new fault pattern.

If any item is unknown, Phase C stops.

## Phase C completion gate

The initiative is complete when:

1. legacy Kalshi facade imports/callers are zero or explicitly retained with a
   user-approved non-migration reason;
2. canonical high-risk contracts have static type coverage;
3. type checking runs in CI at an appropriate strictness boundary;
4. vendor extension values remain tolerant where safe;
5. no new expensive validation exists on hot paths without measurements;
6. compatibility migration scaffolding is removed;
7. integration-boundary audit is green;
8. Kalshi contract fixtures are green;
9. full pytest is green;
10. final deliberate fault injection proves the guards;
11. paper-mode performance/stream health remains within the accepted Phase A baseline;
12. documentation describes the final boundary and how future Kalshi changes are handled.

## Future development rule

After completion, touching a Kalshi contract should normally require:

1. read `docs/kalshi/CHEATSHEET.md`;
2. read exact mirrored official source(s);
3. update the adapter/normalizer;
4. update/add doc-backed fixture;
5. run targeted contract tests;
6. let CI enforce boundary/integration;
7. record any docs/live discrepancy;
8. update CHEATSHEET when a non-obvious reusable lesson is established.

The rest of the application should not need to relearn Kalshi wire semantics.
