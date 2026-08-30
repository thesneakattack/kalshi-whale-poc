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

## Ingest queue-health metrics (realtime data-plane I1, 2026-08-25)

`KalshiStreamGateway.ingest_metrics()` / `reset_ingest_window()` are the
gateway's queue-health surface (per-class received/processed/dropped,
depth/high-water, oldest-message age, queue-wait and handler-time
windows with fixed buckets, Kalshi server error 25 counted separately
from local `QueueFull`, reconnects with reason). Read by
`services/observability/observability.py` (persisted every minute as
`<stream>.ingest.*`) and `/api/health/pipeline`'s `ingest.queue_health`.
The reader now parses JSON (`_ingest_raw`) and the consumer times each
message (`_process_item`) — one parse per message, +2.6 µs/msg measured.
The generic accumulator lives in `services/latency_agg.py`, outside this
package on purpose: the contract-docs scanner treats every public method
here as a Kalshi operation. Full metric list and window semantics:
`services/observability/README.md`.

### `oldest_message_age_sec` covers the coalescing map too (#207, 2026-08-30)

Task 19a's `_ticker_by_market` is a **backlog that lives outside the three
queues**, and `_coalesce_ticker` enqueues its `_TICKER_WAKE` sentinel only on
the empty -> non-empty transition. Once that sentinel is consumed, every queue
reads empty while the map holds arbitrarily old entries, so `_oldest_message_age`
reported `0.0` — perfect health — for a wedged or starved market consumer, the
one failure mode coalescing introduced. It now folds `min(ts)` over the map into
the same max. Confirmed both ways against `tools/soak_analyzer.py`'s
`staleness_metric_trustworthy`: BLIND before, PASS (with the real 60.0s age, and
a correct `backlog_timeliness` FAIL) after.

Semantics worth knowing when reading the number: a superseded entry's timestamp
is deliberately refreshed, because the superseded payload no longer exists to be
stale — so a market that keeps updating never ages, and a wedge shows up through
the entries that stop updating plus `pending_tickers` growth. `depth` still
counts queues only; the map is reported separately as `pending_tickers`.

Cost: `_oldest_message_age` has exactly one call site (`ingest_metrics`), reached
from the 60s observability sampler and per-request diagnostics routes — never
from `_ingest_raw`/`_process_item`/`_consume_*`. Measured 2026-08-30: 0.4 µs at
0 pending, 6.6 µs at 100, 54 µs at 1,000, 524 µs at 10,000. The rejected
alternative was an incrementally maintained "oldest pending ts", which moves
bookkeeping onto the per-message path to save microseconds on a once-a-minute
read.

### Reconnect discards are counted, not lost (#209, 2026-08-30)

`_begin_connection` replaces all three queues and `_ticker_by_market` on every
(re)connect — deliberate (the ticker channel re-snapshots on resubscribe) and
unchanged. Everything it threw away had already been counted into
`received_by_class` on arrival and then reached neither processed, coalesced,
pending nor dropped, so each reconnect broke
`received == processed + coalesced + pending + dropped` by exactly (queued +
map) at that instant: the gap constant at 74 across samples with 12 reconnects.
A correct discard that isn't counted is indistinguishable from a leak. It now
lands in `discarded_on_reconnect_by_class` (`ingest_metrics()`, next to
`dropped_by_class`; persisted as `<stream>.ingest.discarded_on_reconnect.<class>`),
distinct from `dropped_by_class` because queue-full shedding is a different
failure. The `_TICKER_WAKE` sentinel is skipped (not a received message); map
entries count as `ticker`. `tools/soak_analyzer.py`'s `ticker_conservation`
identity gained the term and treats an absent counter (older app) as an assumed
0 that can never PASS.

Cost: one call site (`run()`, once per physical connection — never per message).
O(queued) `get_nowait` drain plus O(1) `len()` for the map; measured 2026-08-30
(container, Python 3.13): 0.3–0.4 µs/item, 27 ms with all three queues full at
20,000 and a 20,000-entry map, once per reconnect on a path already paying a
TCP+TLS+WS handshake. Safe to drain rather than peek: the queues are about to be
dereferenced, `run()`'s `finally` cancelled their consumers, and the count never
awaits.

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

## Combined connect-time subscribe + snapshot on add_markets (P7 Task 33, 2026-08-28)

`_sync_subscriptions`' `force_subscribe` branch sends exchange-wide `trade` and
`market_lifecycle_v2` in **one** subscribe message (`{"channels": ["trade",
"market_lifecycle_v2"]}`), the same combined shape `fill`+`market_positions`
already used in `run()`; each channel keeps its own gate and the message
carries whichever are due. A watchlist-scoped `trade` subscribe stays its own
message - its `market_tickers` sit at the top level of the `params` object and
would apply to every channel in the message, and lifecycle takes no market
filter at all. `ticker`/index channels stay separate for the same reason.
Kalshi answers a multi-channel subscribe with one `subscribed` response per
channel (`websocket-connection.md`'s Subscribed Response schema), which
`_handle_message` already processes one channel/sid at a time - so partial
acceptance, whatever the server does, is handled without change. Known,
pre-existing, not fixed here (R6 in the remediation plan): every subscribe
path sets its `_X_subscribed` flag right after sending, before the server's
confirmation arrives.

`add_markets` on the **ticker** sid now carries `send_initial_snapshot: true`
(documented for "newly added market tickers on the ticker channel" -
`websocket-connection.md` update_subscription schema; never sent on the trade
sid), so a market added mid-connection - a position opening while connected -
gets its first price from WS immediately instead of waiting for its next
natural tick or `market_fetch.overlay_live_prices`' REST seed. Live on the
first post-change connection: `trade` and `lifecycle` both receiving on one
connection, zero drops.

## Consumer-stall bound + liveness backstop (issue #145/#150, 2026-08-28)

Live-observed 2026-08-27 (issue #145): the reader (`recv()` -> `_ingest_raw`)
and consumer (`_consume()` draining `self._queue`) are separate tasks; a hung
`await` inside a handler stalls the consumer forever while the reader keeps
enqueueing, filling the queue and dropping messages with zero automated
recovery (`task_supervisor` only restarts `run()` on an unhandled exception -
a hang that never raises is invisible to it). Root cause traced to
`services/whalewatchers/kalshi_trade_tape.py`'s `fetch_signals` ->
`await asyncio.to_thread(self._process_trades_timed, ...)`, unbounded, wrapping
every blocking SQLite call on the trade path.

Fixed with two layers, not one - a bare reconnect alone would have hidden a
worse problem (see below):

- **`_process_item`** now wraps its `_handle_message(...)` call in
  `asyncio.wait_for(..., timeout=_HANDLER_TIMEOUT_SEC)` (10s default,
  constructor-overridable as `handler_timeout_sec`). Bounds every message's
  worst case at the actual hang site. `TimeoutError` is counted/fault-logged
  separately from `handler_exceptions_*` (`handler_timeouts_total`/
  `handler_timeouts_by_class` in `ingest_metrics()`) - deliberately not
  folded into the existing counter, since conflating them would hide the one
  signal that says whether this is happening often enough to matter.
- **`ensure_consumer_progressing()`** is a backstop for whatever the timeout
  doesn't structurally cover (e.g. a hang inside `_sync_subscriptions`, which
  runs on the *reader's* task, not the consumer's). Polled every 10s from
  `main.py` (`_stream_consumer_liveness_loop`, one per active gateway -
  `trade_stream` and `index_stream`). Signal is direct, not a proxy: total
  processed-message count held flat across 3 consecutive checks while
  `messages_received` kept climbing and the queue holds a backlog - "new
  work arrived, nothing got consumed." A genuinely quiet market
  (`messages_received` flat) or a slow-but-progressing consumer (processed
  count still advancing) both correctly report not-stuck. On trigger, calls
  `force_reconnect(reason)` - closes only the current connection (unlike
  `close()`, never sets `self._stop`), reusing `run()`'s existing
  disconnect -> backoff -> reconnect path, which already discards the stuck
  consumer task (`finally: consumer.cancel()`) and starts a fresh one.

**Known, accepted, NOT fixed (issue #150):** cancelling a timed-out
`asyncio.to_thread(...)` call does not stop the underlying OS thread once it
has started running (confirmed against CPython's own
`concurrent.futures.Future.cancel()`: "cannot be cancelled if it is
running") - it leaks one worker from the shared, bounded default
`ThreadPoolExecutor` per occurrence. Deferred rather than fixed pre-emptively
(no measured frequency yet, per `.claude/rules/realtime-data-plane-
evidence.md`'s "don't tune by intuition") - `handler_timeouts_total` is the
signal to watch; issue #150 names the real fix (a dedicated executor, or
root-causing whatever inside `_process_trades_sync` can hang instead of
raising) if that signal ever climbs.

## Account attestation + exchange-side staleness reads (issues #266/#261, 2026-08-30)

`KalshiAccountGateway.get_user_data_timestamp()` (GET /exchange/
user_data_timestamp) and `.get_api_keys()` (GET /api_keys) - two account
reads with zero prior callers anywhere in `services/` (grepped, confirmed).
Surfaced together on a new on-demand `GET /api/diagnostics/account`
(`services/diagnostics/routes.py`) - deliberately not folded into
`/api/health/pipeline` or `/api/quality/summary`: both stay network-I/O-
free by design/test today, and this app already has an established pattern
for "a diagnostic that needs a real Kalshi call gets its own route"
(`/api/diagnostics/coverage`, `/api/diagnostics/trade-capture`).

`get_api_keys()` doesn't just call the SDK method and `.model_dump()` it
like every other read here - see `docs/kalshi/CHEATSHEET.md`'s entry on
why: the installed SDK's response model silently drops
`api_key_region_expiration_ts` entirely (it predates Kalshi's 2026-08-27
changelog addition), so this one recovers it from the raw response bytes
(`get_api_keys_with_http_info`'s `ApiResponse.raw_data`) instead of
trusting the parsed model.

`classify_api_key_attestation()` and `user_data_age_sec()` are pure
functions in the same module (not the diagnostics route) per the permanent
semantic rule above - `never_attested` (field absent) / `active` / `lapsed`
stay three distinguishable states, never collapsed into one boolean. The
route's `pipeline_oldest_message_age_sec` field is this app's own ingest-
pipeline staleness (`trade_stream.ingest_metrics()`), riding alongside the
exchange's own `as_of_age_sec` so the two - genuinely different
measurements, per issue #266's "do not confuse with" - are comparable from
one response without ever merging into a single number.
