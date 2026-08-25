# Kalshi Integration Architecture Audit

**Audit snapshot:** `main` at `d6440347c5107a1bbbcb1b859736118a8c378057` on 2026-08-24.

**Important:** This snapshot records the evidence used to design the initiative. It is not
implementation truth after newer commits land. Every execution task must re-ground against
current HEAD before changing code.

## Objective

Make every backend path that depends on Kalshi semantics derive those semantics from the
repository's mirrored official documentation rather than human or model memory, while
reorganizing the Kalshi integration boundary so that vendor-specific REST/WebSocket/SDK
knowledge is centralized and application modules consume stable internal contracts.

The initiative deliberately uses two phases:

- **Phase A — Contain and Document:** establish a documented anti-corruption boundary,
  migrate callers incrementally, preserve behavior, and enforce the boundary.
- **Phase C — Consolidate and Harden:** strengthen types and interfaces where evidence
  shows they buy correctness, remove migration facades, and tighten static enforcement.

Phase C is conditional on Phase A's stability gate.

## Current strengths to preserve

The repository is not starting from a poor architecture. Several existing decisions are
correct and must survive the refactor:

1. `services/kalshi_client.py` is public/read-only and
   `services/kalshi_account_client.py` is authenticated/write-capable. The separation keeps
   ordinary market reads away from real-order capability.
2. `services/http_client.py` centralizes connection pooling, retry/backoff, rate limiting,
   and REST telemetry.
3. `services/kalshi_trade_ws.py` separates exchange stream transport from most application
   processing in `services/whale_stream/`.
4. `docs/kalshi/` mirrors a large official-doc corpus and
   `docs/kalshi/CHEATSHEET.md` records application-specific lessons and upstream
   discrepancies.
5. `tests/test_kalshi_contracts.py` and `tests/fixtures/kalshi/*.json` already feed
   doc-sourced payloads through production normalizers/handlers.
6. `.woodpecker/kalshi-contract-fixtures.yml` provides a named deterministic contract CI
   check.
7. QCP runtime/static quality infrastructure already provides investigation-to-guard
   discipline, REST telemetry, storage safety, and architecture scanning.
8. `series_watcher.py` intentionally preserves full raw payloads for later research and
   diagnostics.

These are assets, not obstacles.

## Finding A — the documentation mirror is broad but not fully authoritative yet

`docs/kalshi/README.md` says the 2026-08-16 pass mirrored the complete Markdown index that
existed then. `tools/kalshi_docs_drift.py`, however, explicitly documents important
exceptions:

- 16 local files are curated summaries rather than verbatim upstream bodies.
- two local files merge two upstream resources;
- `llms.txt` has local source-header text and is excluded from content hashing;
- the drift implementation already observed upstream resources newer than the committed
  index;
- OpenAPI/AsyncAPI YAML resources listed by the upstream index are not mirrored;
- resources represented by summaries/merges get `sha256=None`, which means availability
  is checked but content drift is not.

### Required correction

Distinguish two requirements:

**Used-contract completeness is a hard prerequisite.**
Every endpoint/channel/semantic rule used by production code must have a current,
verbatim-enough local official source before that consumer is migrated.

**Global mirror completeness is an initiative goal, not a reason to block unrelated
migration indefinitely.**
The mirror updater should eventually capture the complete current `llms.txt` resource set,
including supported non-Markdown API specification files, but a currently-unused RFQ/FCM
resource must not block refactoring a public trade parser.

The mirror's machine manifest, not README prose, should become the provenance source of
truth. README becomes generated/human reference.

## Finding B — `KalshiClient` mixes vendor access with application policy

`services/kalshi_client.py` spans multiple vendor concerns: markets, series, events,
milestones/live data, trades, candlesticks, exchange status, search metadata, and
orderbooks.

Breadth alone does not require one class per Kalshi documentation heading. The stronger
problem is that the client also contains application selection policy:

- `get_candidate_markets`
- `round_robin_select`
- `get_top_volume_markets`

`services/market_watch/` already owns watchlist/discovery policy. The vendor boundary
should answer "fetch these markets"; `market_watch` should answer "which markets should
the autotrader care about?"

## Finding C — authenticated vendor primitives and application execution policy are mixed

The authenticated client correctly owns credentials, SDK setup, portfolio/account reads,
and order writes, but high-level operations such as emergency flattening are application
execution policy assembled from vendor primitives.

The final architecture should keep raw order capability narrow while exposing safe
high-level execution through the application's execution/risk layer. Moving policy out
must not make low-level writes easier for arbitrary consumers to call.

## Finding D — high-risk Kalshi wire semantics leak into application modules

Examples observed at the audit snapshot:

- `services/whalewatchers/kalshi_trade_tape.py` interprets
  `taker_outcome_side`, `taker_book_side`, legacy `taker_side`, fixed-point count/price
  fields, and side-aware notional.
- `services/account_positions.py` understands REST-vs-WS alias differences such as
  `ticker` vs `market_ticker` and `fill_id` vs `trade_id`.
- `services/whale_stream/whale_stream_handlers.py` reads raw ticker/fill/position/lifecycle
  fields directly.
- `services/series_watcher.py` both preserves raw payloads and derives selected vendor
  fields.

This is not theoretical. QCP contract-fixture work found three real bugs from distributed
wire assumptions:

1. WS fills were keyed on `fill_id`, which the WS user-fill message does not carry.
2. WS position dispatch used the plural subscription channel name instead of the singular
   per-message type.
3. WS position processing read `ticker` where the actual message uses `market_ticker`.

### Final invariant

**Semantic interpretation of Kalshi wire format belongs in `services/kalshi/`.**

Opaque raw payloads may cross the boundary for archival, diagnostics, observability, and
research. Application code may store/pass `raw_payload`; it should not re-derive
vendor-specific meaning from it.

## Finding E — the current static API inventory is too weak to enforce the desired boundary

`tools/quality_audit/api_usage.py` inventories calls only when the receiver variable is
literally named `client` or `account`, and findings are informational. That was appropriate
for QCP inventory but cannot prove that vendor access is centralized.

A new/extended boundary scanner should begin with high-confidence rules:

- direct `kalshi_python_async` import outside approved integration modules;
- direct production request to Kalshi REST hosts outside approved integration modules;
- direct Kalshi WebSocket connection outside approved integration modules;
- new imports of compatibility facades after migration begins;
- known deprecated high-risk field interpretation outside approved normalizers.

Do not begin by banning generic strings such as `ticker` or `trade_id`; that would create
false-positive noise.

## Finding F — raw payload preservation is a feature

`series_watcher.py` exists because earlier slimming made important retrospective questions
unanswerable. The refactor must preserve full raw payload capture where configured and the
ability to inspect newly-added upstream fields without a schema migration first.

The anti-corruption layer controls **interpretation**, not raw archival.

## Finding G — strict typing must be selective and tolerant

The official SDK has already failed an entire response when live Kalshi introduced an enum
value not recognized by the published SDK. Another live path has observed `live_datas:
null` where the generated model expected a list.

Therefore the internal type system must distinguish:

- **closed business semantics**, where unknown values are unsafe and should be rejected or
  quarantined (for example YES/NO outcome side);
- **open vendor extension values**, where unknown strings/fields should be preserved and
  surfaced without collapsing the whole response.

Do not reproduce generated-SDK brittleness inside the application.

## Finding H — hot-path runtime validation can be harmful

The exchange-wide trade stream has dedicated CPU/throughput instrumentation because it can
process a large message rate. Phase C must not automatically add Pydantic validation,
`Decimal` conversion, or expensive object construction to every inbound message.

Preferred pattern:

```text
raw inbound payload
    -> cheap documented extraction/prescan
        -> irrelevant: discard
        -> relevant: canonical normalization
```

Static `TypedDict`, `Protocol`, `NewType`, and small `slots=True` dataclasses are available
without requiring heavy runtime validation.

## Finding I — WebSocket decomposition should be evidence-driven

The current transport class is broad, but there is already a useful boundary between
transport (`services/kalshi_trade_ws.py`) and business processing (`services/whale_stream/`).

Do not pre-commit to an eight-file protocol framework. Extract channel semantic
normalizers first. Split connection/subscription/dispatch internals only when cohesion,
testability, or file complexity demonstrates value.

## Finding J — compatibility facades need an exit ratchet

Incremental migration is safer than a big-bang cutover, but transitional facades can
become permanent.

The migration must continuously count legacy callers and CI must forbid **new** legacy
consumers once the replacement exists. Zero callers requires facade deletion.

## Finding K — the architecture needs a quantitative census before extraction

Phase A begins with a reproducible census that counts:

- production files importing Kalshi clients;
- direct SDK imports;
- direct Kalshi HTTP/WS access;
- client construction sites and lifecycle patterns;
- Kalshi wrapper methods actually called;
- REST operations actually consumed;
- WS channels/message types consumed;
- raw vendor fields interpreted outside integration code;
- known REST-vs-WS aliases;
- compatibility workarounds;
- used contracts with and without doc-backed fixtures;
- hot-path vs cold-path consumers.

The census decides how far the package should split. Architecture is earned by evidence.

## Final design thesis

> Mirror Kalshi completely for knowledge. Model the contracts the application consumes.
> Centralize interpretation of those contracts. Type the semantics where misuse is
> consequential. Preserve raw data where investigation requires it. Let CI prevent the
> boundary from leaking again.
