# services/kalshi/ — Kalshi integration boundary (cheat sheet)

Owns **all vendor-specific Kalshi semantic interpretation** (Phase A of
the dual-phase initiative, tasks A4–A17, 2026-08-24/25 — design spec:
`docs/superpowers/specs/2026-08-24-kalshi-integration-boundary-design.md`).
Application policy (market selection, strategy, execution orchestration)
deliberately lives OUTSIDE this package; raw payload archival is
preserved end to end.

## Module map

- `transport.py` (A5) — the only place `kalshi_python_async` enters the
  codebase. Builds unauthenticated/signed SDK clients; re-exports
  `call_with_backoff` (same object as `services/http_client.py`'s — one
  retry/limiter/telemetry stack, never two).
- `public.py` (A6) — `KalshiPublicGateway`, every public REST read the app
  uses; the sole implementation AND sole import path since C8 deleted the
  compatibility facade at zero callers (selection *policy* lives in
  `services/market_watch/` since A7).
- `account.py` / `orders.py` (A8) — authenticated READ gateway
  (structurally write-free, asserted by test) vs WRITE primitives
  carrying both safety gates (`trading_enabled` live-read + risk
  kill-switch; closing orders exempt from the halt guard only).
  `services/kalshi/account_client.py` (moved into the boundary at C8)
  composes both behind live property proxies (`_client`/`trading_enabled`/
  `risk` — runtime mutation sites in main.py and account_positions must
  reach the gateway; a construction-time snapshot would freeze the
  real-money switch).
  Emergency-flatten policy lives ABOVE the adapter in
  `services/execution.py` (A9).
- `websocket.py` (A11) — `KalshiStreamGateway`, the WS transport
  (auth/subscribe/reconnect/bounded ingest queue/dispatch); direct
  construction since C8 (the zero-override facade is deleted).
- `contracts/` (A10/A12) — per-channel semantic normalizers + canonical
  types (`PublicTrade`/`TickerUpdate`/`UserFill`/`MarketPosition`/
  `LifecycleEvent`/`CreateOrderRequest`/`CancelOrderResult`). Dispatch
  normalizes every message before any handler sees it (canonical `ticker`
  alias overlay + raw pass-through). `contracts/order.py`'s canonical
  types guard the real write path since C5 (`create_order` builds its
  wire kwargs through `CreateOrderRequest`, BookSide-validated).
  `contracts/types.py` (C2) owns the closed `OutcomeSide`/`BookSide`
  Literals and the one shared narrowing map; `interfaces.py` (C7) owns
  the consumer-facing capability Protocols
  (`AccountReads`/`OrderWrites`/`FlattenCapable`).
- `provenance.py` (A4) — `CONTRACT_DOCS` aggregate/validator. Operation
  names are **globally unique across the package** (validator enforces),
  which is why factory functions are `public_trade_from_ws` etc., not a
  shared `from_ws`.

## Semantics owned here (the historically-dangerous ones)

- Direction: `taker_outcome_side` first, `taker_book_side` (bid≡yes,
  ask≡no) second, deprecated `taker_side` last, unknown → None — never a
  guessed yes/no. The whale provider's `_taker_side` and series_watcher
  bind these same objects (A13).
- Side-aware notional (`taker_notional_usd`) — count × the taker's OWN
  side price (the no-side-inversion family).
- WS identity: fill = `trade_id` (no fill_id on the wire); REST Fill has
  BOTH spellings (see docs/kalshi/CHEATSHEET.md's 2026-08-25 entry).
- `market_position` (singular per-message type) vs `market_positions`
  (plural channel) — both spellings live in `contracts/position.py`.
- Lifecycle: only `settled` may resolve an outcome, via a fresh REST read
  gated on `status == "finalized"` (`determined` can flip via disputed →
  amended).
- create-order-v2: BookSide `bid`/`ask` YES-leg vocabulary only —
  `CreateOrderRequest` rejects anything else at construction.

## Guards that keep it true

- `tools/quality_audit/kalshi_contract_docs.py` (A3) — every public
  operation here must map to exact mirrored docs (CI, every push).
- `tools/quality_audit/kalshi_boundary.py` (A15, finalized C9) —
  SDK-import/host-string containment, a hard file-independent ban on the
  deleted legacy module paths (`services.kalshi_client` /
  `kalshi_account_client` / `kalshi_trade_ws`), deprecated-alias reads
  outside the boundary, plus the mypy step CI runs beside it (C1,
  mypy.ini — scoped to this package + services/execution.py, with
  full-annotation strictness on contracts/).
- `tools/kalshi_census.py` — informational inventory the ratchet shares
  logic with; its curated hot/cold table names real symbols (CI fails if
  a listed symbol stops existing — it caught both the A11 and A13 moves).
- `tests/fixtures/kalshi/*.json` + `tests/test_kalshi_contracts.py` —
  doc-sourced fixtures through production entry points (16 fixtures,
  every high-risk channel/operation + REST/WS shape splits).

## Performance envelope (measured, A12)

Hot dispatch path emits plain dicts: `normalize_trade` 2.92 µs/msg.
Canonical objects are built on demand (4.38 µs) — never eagerly in the
exchange-wide loop. Don't add per-message validation here without
re-measuring.

## Type strictness / tolerance policy (C2-C6, 2026-08-25)

- **Closed Literal types** (contracts/types.py): only where an unknown
  value is unsafe to act on — direction (`OutcomeSide`, `BookSide`).
  One shared narrowing map; unknown → None, never a guessed member.
- **Open strings stay strings**: fee_type (grew beyond its documented
  enum live, 2026-08-21), lifecycle event_type, category/facet values.
  A closed type there turns a harmless vendor addition into a crash.
- **Typed request path**: real order writes build through
  `CreateOrderRequest`/`create_order_kwargs` (C5) — one wire-construction
  path, BookSide-validated before any SDK call, gates first.
- **Public market/event/live-data results deliberately stay documented
  mappings** (C6 verdict, evidence-ranked per the plan): they are
  display/selection data consumed via fixture-pinned allowlists
  (`services/market_watch/`'s `_MARKET_FIELDS`/`_slim_market`, event
  metadata's `sub_title`/`mutually_exclusive`/`competition` extraction —
  each pinned by a doc-sourced fixture test through the production entry
  point, covering that surface's real historical bugs) and they flow
  straight to JSON for the frontend. A dataclass layer here would add
  conversion ceremony with no consumer that benefits — the plan's own
  "more maintenance than safety" case. Revisit only if a public shape
  gains money-path semantics (then it earns a canonical type like the
  account/order/stream shapes did).

## How to add or change a Kalshi endpoint/channel (permanent workflow, C11)

1. **Exact docs first**: `grep -rn` `docs/kalshi/` for the endpoint/
   channel/field; read the exact mirrored page(s) (check
   `docs/kalshi/CHEATSHEET.md` for already-resolved questions). If the
   mirror may be stale: `python -m tools.kalshi_docs_sync --check`, and
   pull new pages before coding (fetch the exact upstream `.md`, bare
   basename per the sync tool's naming algorithm, then `--write`).
2. **Adapter + metadata**: implement in the right gateway/normalizer
   module under `services/kalshi/`, add the operation to that module's
   `CONTRACT_DOCS` naming the exact doc paths. Operation names are
   globally unique across the package (provenance enforces it).
3. **Fixture**: add a doc-sourced fixture under `tests/fixtures/kalshi/`
   (`_meta.source_doc` required; no invented fields) and a test feeding
   it through the PRODUCTION entry point in
   `tests/test_kalshi_contracts.py`.
4. **Normalizer/canonical type**: WS channels normalize at dispatch
   (raw pass-through + canonical alias overlay); add/extend a canonical
   type only if a consumer needs it (C6 rule: types serve correctness,
   not coverage).
5. **Consumer**: application code consumes gateways/canonical fields —
   never vendor aliases, never a raw host/SDK import (the boundary
   scanner makes that a red push).
6. **CI proves it**: the architecture audit (contract-docs + boundary +
   mypy) and the fixture pipeline both run on every push — a deliberate
   local break of your new check before pushing is the cheap way to
   prove it's wired.

**Docs/live discrepancy**: never silently code around one — record it as
a dated entry in `docs/kalshi/CHEATSHEET.md` (what the docs say vs what
was observed), keep the tolerant/raw path, and if it's schema-relevant
consider the docs-drift canary. **Raw-payload archival exception**:
opaque raw payload copies (series_watcher's columns/raw_json, diagnostics
pass-through) are allowed anywhere; semantic *interpretation* is not.
