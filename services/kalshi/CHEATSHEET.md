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
  uses. `services/kalshi_client.py` subclasses it as the compatibility
  facade (selection *policy* moved to `services/market_watch/` at A7).
- `account.py` / `orders.py` (A8) — authenticated READ gateway
  (structurally write-free, asserted by test) vs WRITE primitives
  carrying both safety gates (`trading_enabled` live-read + risk
  kill-switch; closing orders exempt from the halt guard only).
  `services/kalshi_account_client.py` composes both behind live property
  proxies (`_client`/`trading_enabled`/`risk` — runtime mutation sites in
  main.py and account_positions must reach the gateway; a
  construction-time snapshot would freeze the real-money switch).
  Emergency-flatten policy lives ABOVE the adapter in
  `services/execution.py` (A9).
- `websocket.py` (A11) — `KalshiStreamGateway`, the WS transport
  (auth/subscribe/reconnect/bounded ingest queue/dispatch).
  `services/kalshi_trade_ws.py` is a zero-override subclass facade.
- `contracts/` (A10/A12) — per-channel semantic normalizers + canonical
  types (`PublicTrade`/`TickerUpdate`/`UserFill`/`MarketPosition`/
  `LifecycleEvent`/`CreateOrderRequest`/`CancelOrderResult`). Dispatch
  normalizes every message before any handler sees it (canonical `ticker`
  alias overlay + raw pass-through). `contracts/order.py`'s canonical
  types have **no production caller yet by design** — they are the C5
  wiring surface.
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
- `tools/quality_audit/kalshi_boundary.py` (A15) — SDK-import/host-string
  containment, legacy-facade import ratchet (kalshi_client 16 /
  account 2 / trade_ws 2 — lower it as consumers migrate, never raise
  silently), deprecated-alias reads outside the boundary.
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
