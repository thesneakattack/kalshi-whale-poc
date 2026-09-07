# Realtime Kalshi Data-Plane — Remediation Design (I13)

**Status:** proposed for user review. Implementation waits for approval of this spec and
the companion plan (`docs/archive/lane-1-kalshi-ingestion/plans/2026-08-25-realtime-data-plane-remediation.md`).
**Evidence:** `docs/archive/lane-1-kalshi-ingestion/research/2026-08-25-realtime-root-cause-report.md` (why),
`…-realtime-architecture-review.md` (what the review changed), `…-ws-solution-comparison.md`
and `…-rest-solution-comparison.md` (the matrices).
**Constraints honoured:** exchange-wide whale discovery stays; real trading stays disabled
(`kalshi_account.trading_enabled` untouched); no safety/risk/auth/CORS/kill-switch change;
tests never touch live `data/*.db`; historical data is never destroyed; Kalshi semantics
stay inside `services/kalshi/` (`.claude/rules/kalshi-integration-authority.md`); no
hot-path validation without a measured cost.

## 1. Selection

The simplest architecture that meets the measured targets (root-cause report §8) is:

```text
Kalshi WS ──► websockets recv (max_queue 1024)
               │
               ▼  reader task (one per connection): json.loads → class → gate
               ├── trade, below gate ──► reader buffers (raw_trades append, rejection
               │                          aggregate, tape ring) — never enqueued
               ├── trade, whale-sized ──► market queue (FIFO)
               ├── ticker ─────────────► market queue's coalescing map (apply-if-newer)
               └── fill / position / lifecycle / control ──► critical queue (never shed)

  critical consumer  ──► account & lifecycle handlers (no REST inline; settled → resolver queue)
  market consumer    ──► trades first, then coalesced tickers (check_exits with per-tick memo)
                          whale candidate → ledger.claim(trade_id) → enrichment (retry FSM)
                          → evaluate → broker

  db writer thread   ──► batched flushes for capture stores (raw_trades, rejections,
                          snapshots, catalog updates); persistent connection per store
  tick executor      ──► the trading tick's synchronous SQLite phases, persistent per-thread
                          connections; money stores stay write-through
  REST limiter       ──► critical-first waiter queues + background aging + global 429 brake;
                          budget 8/s until the anonymous ceiling is probed
  resolver task      ──► batched, deferred `settled` reads (GET /markets?tickers=)
  reconciler         ──► on reconnect / error-25 only; feeds candidates through the ledger
```

Why this and not more: with the reader gate in place, per-class queues beyond
critical/market were within 1–3 ms of each other in the honest single-loop model
(review §3.3); a second consumer exists for *isolation* (critical traffic can never sit
behind market traffic), not for throughput. Why not less: a single consumer with a
priority queue reaches candidate p95 241 ms and cannot protect critical traffic from a
long ticker burst (review §3.3 `critical_first`); without the gate the consumer does 400×
the necessary work (root-cause §C2); without loop hygiene every topology is bounded by
4–13 s stalls (§C1).

## 2. Message classes

| Class | Kalshi source (`services/kalshi/`) | Queue | Shed? | Coalesce? | Ordering |
|---|---|---|---|---|---|
| `control` | `subscribed`, `ok`, `error`, pong bookkeeping | critical | never | no | per connection generation; frames from a dead generation are dropped before the handler (review W8) |
| `fill` | `fill` (identity `trade_id`) | critical | never | no | arrival order |
| `position` | `market_position` | critical | never | no (latest wins in state, all applied) | arrival order |
| `lifecycle` | `market_lifecycle_v2` | critical | never | no | arrival order; `settled` enqueues `(ticker, settled_ts)` to the resolver, nothing else awaits |
| `trade` (candidate) | `trade`, `count_fp` ≥ threshold | market FIFO | only at queue cap (counted per class, quality finding) | no | FIFO — the single-in-flight `fetch_signals` invariant is preserved: one market consumer |
| `trade` (sub-threshold) | `trade` | none (reader buffers) | n/a | n/a | n/a — captured, counted (`ingest.prefiltered.trade`), never evaluated |
| `ticker` | `ticker` | market coalescing map keyed by market | implicit (older update replaced) | yes, apply-if-newer on `ts` | per market: newest only; across markets: map iteration order |
| `index` | separate connection (unchanged) | unchanged | unchanged | unchanged | unchanged |

The reader gate is a pure predicate over `services/kalshi/` contract accessors
(`trade_contract.trade_count(...)`, `trade_contract.trade_ticker(...)`) with the threshold
snapshot passed in — it reads no config on the hot path and is exception-guarded inside
`_ingest_raw` (a gate exception counts, fault-logs once per window, and falls open to
"enqueue" so a bug never silently hides whales). Its measured cost must be ≤ 20 µs per
message before it ships (review W5: today's `config_store.get()` + `series_of` path is ~15 µs).

## 3. Ordering semantics

- Within a class on one connection, arrival order is preserved (FIFO queues).
- Across classes there is no ordering guarantee and none is needed: the paper broker
  never consumes fills causally, `check_exits` and `evaluate` are await-free so a trade
  and a ticker on the same market cannot interleave a double close (review W11).
- Coalesced tickers carry the newest `ts`; `market_history.record_snapshot_from_ticker`'s
  5 s throttle therefore never records an older mark (review 2.4).
- A kept queue across a reconnect preserves arrival order of *exchange* events from the
  old connection; only control frames are generation-checked.
- Trade evaluation order across markets is FIFO, as today.

## 4. Backpressure, drop and coalescing policy

- **Reader never blocks** on anything but `queue.put_nowait`; buffers are bounded
  (`raw_trades` 20,000 rows as today, rejection aggregates by (ticker, side, minute),
  tape ring 2,000) with per-buffer overflow counters — never an inline flush.
- **Critical queue** is unbounded in practice (capacity 20,000; at 1–3 msg/s it cannot
  fill without a stalled loop, which the watchdog reports); a full critical queue is a
  quality finding, not a silent drop.
- **Market FIFO** capacity 20,000 as today; at cap the incoming *trade* is dropped and
  counted per class (`ingest.dropped.trade`); with the gate this requires > 20,000 pending
  whale candidates, i.e. a consumer that has not run for hours.
- **Ticker map** replaces the older pending update for the same market; `coalesced`
  counted. Tickers for markets with open positions are applied first (a small ordered set
  the consumer consults), so exit checks see fresh marks before idle-watchlist marks.
- **Backpressure to Kalshi** (TCP window) is what happens if the reader itself stalls;
  the watchdog and `ingest.queue_wait` distinguish that from consumer lag (I1 metrics).

## 5. Retry / dedupe state machine (candidate lifecycle)

Table `candidate_ledger` in `data/signal_log.db` (additive: `CREATE TABLE IF NOT EXISTS`,
`_add_column_if_missing` for the new `source_trade_id` column on `signals`):

```text
                         claim(trade_id)  ── INSERT OR IGNORE ──► conflict → duplicate (counted, stop)
  ┌───────────┐  ok   ┌───────────┐  market known   ┌────────────┐  evaluate  ┌──────────┐
  │ received  │──────►│ claimed   │────────────────►│ evaluating │───────────►│ terminal │
  └───────────┘       └───────────┘                 └────────────┘            └──────────┘
                            │ market unknown               ▲                    (decision,
                            ▼                              │                     skipped,
                      ┌───────────┐  lookup ok             │                     abandoned)
                      │ pending   │────────────────────────┘
                      └───────────┘
                         │ transient failure (429 / timeout / empty batch)
                         ▼ retry with backoff, single owner task, class critical_whale
                      attempts ≤ 8 over ≥ 72 s ──► abandoned (counted, fault-logged, quality finding)
```

- `_mark_seen` moves to *after* `claim`; the ring stays as the cheap in-memory front
  filter, the ledger is the truth. A ring miss followed by a ledger conflict is a
  duplicate, counted — the production analogue of the harness's `duplicates`.
- `strategy.evaluate` is gated by the claim: the decision bridge inserts before it logs
  (today it logs before it evaluates — root-cause §C6).
- The retry owner is one task on the loop; it is the only other mutator of pending state
  and it never touches the dedupe ring from another thread (review R7).
- Over-cap (`_MAX_ONDEMAND_MARKET_FETCH`) and empty-batch outcomes are `pending`, not
  terminal; the 300 s negative cache applies only to a *complete* batch that omitted the
  ticker.
- Restart: pending rows survive in the ledger; the retry owner resumes them on startup.
  There is no separate journal — the ledger is the journal for candidates; queued but
  unclaimed prints lost to a restart are measured by `ingest.dropped_on_restart` and
  revisited only if the soak shows them material.

## 6. REST scheduler semantics (`services/http_client.py`)

- `acquire()` no longer sleeps under the lock. Waiters go into per-class deques; on each
  refill the dispatcher-free wake order is: critical classes (`critical_position`,
  `critical_whale`, fills/balance) first, then background waiters by age (a background
  waiter older than 2 s is promoted ahead of new critical arrivals — the aging that bounds
  starvation to a known value, I11 `priority_aging`).
- Budget stays **8 tokens/s, 8 burst** until the anonymous ceiling is probed
  (`tools/kalshi_rate_limit_probe.py --anonymous-ceiling`, an open verification). Only
  after that measurement may the bucket be raised, and only to a value below the measured
  ceiling with the brake below.
- **Global 429 brake:** any upstream 429 halves the effective refill for 5 s and doubles
  the halving on repeat within the window (adaptive throttling); recovery is linear.
  Critical retries obey the brake — they are not exempt (review R2).
- The tick spawns its background tasks (catalog, resolution, backup, research, schedule)
  *after* its critical gather returns (review R1); the catalog batch is paced (at most 4
  in flight) rather than a 10–40-wide gather.
- The REST tape poll (`_fetch_trade_tape`, streaming off) and every other unclassified
  caller gets a class; `other` must be ≤ 2% of calls in the soak.
- Demand reduction: `settled` resolver batches N tickers per `GET /markets?tickers=` after
  a 60 s delay with three retries at 2/5/15 min until `finalized` (grading stays on
  `finalized`); one shared milestone/live-data cache replaces the three pollers; both are
  measured by the existing `kalshi_rest_endpoint.*` window counts.

## 7. Reconciliation policy

- Trigger: a reconnect or a Kalshi error-25 frame on the trade connection (real loss),
  never queue-full-while-alive (that is the topology's problem and would only measure
  backlog — review R9).
- Window: from the last claimed exchange timestamp before the loss to reconnect time plus
  60 s, paged with `GET /markets/trades` `min_ts`/`max_ts` (`docs/kalshi/CHEATSHEET.md`),
  at most 20 pages, caller class `reconciliation` (background, aged like the rest).
- Every whale-sized print found goes through the same `claim` path; duplicates are
  impossible by construction, and recovered candidates carry `origin = "reconciliation"`.
- The existing `GET /api/diagnostics/trade-capture` stays the manual audit and gains
  count-based exchange-wide completeness (reader `received` vs REST count) next to the
  id-based whale-sized completeness, with the gate threshold snapshot in the report.

## 8. Persistence, thread and process ownership

One process, one loop, two extra threads. Ownership table (a test asserts it):

| Store | Writer | Mode |
|---|---|---|
| `series_watcher.db` (`raw_trades`, books) | db writer thread, 1 s or 500-row flush | WAL, `synchronous=NORMAL` |
| `candidate_log.db` (rejection aggregates, unresolved rows) | db writer thread | WAL, NORMAL |
| `market_history.db` snapshots | db writer thread; outcomes via the tick executor | WAL, NORMAL |
| `market_catalog.db` lifecycle updates | db writer thread | WAL, NORMAL |
| `signal_log.db` (signals, `candidate_ledger`) | market consumer via the tick executor's connection pool (write-through — decision store) | WAL, FULL |
| `paper_broker.db`, `risk_state.db`, `accounts.db` | unchanged (loop thread, write-through, FULL) — money stores are explicitly excluded from write-behind | unchanged |
| everything else | unchanged, but any loop-thread connection opens with `timeout=0.05` and treats `SQLITE_BUSY` as a counted fault, never a 5 s sleep on the loop | |

- The tick executor is a `ThreadPoolExecutor(max_workers=2)` with `threading.local`
  connections; the tick's synchronous phases (`capture_flush_and_titles`,
  `resolve_and_record`, `series_stats`, catalog application) move there behind the
  existing `await` boundaries — no logic changes, only where the work runs.
- The writer thread is a daemon with a bounded shutdown flush (2 s); a dead writer is a
  supervised restart plus `fault_log` plus a quality finding; buffers stay capped with
  overflow counters while it is down.
- No multiprocessing: the consumers share `check_exits`/broker atomicity with the loop.

## 9. Observability (every moved cost gets a timer where it lands)

New or changed, all in `services/observability/` with `CHEATSHEET.md` updated in the same
commit:

- `loop.stall_max_ms`, `loop.stall_count` per window (watchdog task sampling loop lag at
  100 ms; cost measured before enabling).
- `ingest.prefiltered.<class>`, `ingest.gate_exceptions`, `ingest.generation_dropped`,
  per-queue depth / high-water / oldest age (`critical`, `market`, `ticker_map`).
- `whale_pipeline.*` denominators re-based on candidates (documented meaning change).
- `writer.depth`, `writer.last_flush_age_ms`, `writer.overflow.<buffer>`, writer liveness
  finding; `executor.queue_depth`.
- `candidate_ledger.{claimed,duplicates,pending,retries,abandoned}`; abandoned > 0 is a
  quality finding.
- `kalshi_rest_limiter.{critical_waiters,background_waiters,brake_active,brake_events}`;
  `waiters_high_water` becomes per window.
- Existing `GET /api/quality/summary` gains the three new findings (loop stall, writer
  liveness, abandoned candidates).

## 10. Migration strategy

Each phase is behind its own `config/settings.yaml` flag (default off until its gate
passes), ships with its tests and replay gate, and is soaked in paper mode before the next:

| Phase | Change | Gate |
|---|---|---|
| P0 | Guards first: watchdog metric, per-queue metrics, ledger table (write-only, no gating), gate predicate with counters (shadow: counts, does not filter), `docs/kalshi/` open verifications (anonymous ceiling, `determined` re-fire) | metrics visible in `/api/health/pipeline`; ceiling number recorded |
| P1 | Loop hygiene: tick executor with persistent connections; busy-timeout policy; tick spawns background after the critical gather | `loop.stall_max_ms` p95 < 250 ms over 24 h; tick p95 < 1 s |
| P2 | Ledger gates `evaluate`; retry state machine; over-cap/empty-batch paths | H4 xfail flips to pass; 0 duplicates, 0 lost in the transient-failure replay and in a 24 h soak |
| P3 | Reader gate live + capture contract + writer thread + aggregated rejections | `raw_trades` growth rate unchanged ±5%; `ingest.prefiltered` ≈ 99.7% of trades; consumer busy < 10% |
| P4 | Critical/market consumers, ticker coalescing with `check_exits` memo, kept queue with generation stamps | busy-hour replay gates (§8 targets); critical p95 < 100 ms live |
| P5 | Limiter rewrite (critical-first + aging + brake), `settled` resolver, shared milestone cache, classified tape poll | critical wait p95 < 50 ms; `settled` demand ≤ 0.1/s; 429 < 1/h |
| P6 | Reconnect/error-25 reconciliation | injected reconnect recovers ≥ 99% of whale-sized prints with 0 duplicates |

## 11. Rollback

Every phase flag is live-reloadable through the existing config path and reverts to
today's behaviour; the ledger and metrics tables are additive and harmless when unused;
the writer thread drains on disable. No schema is dropped, no data migrated, no
`data/*.db` file replaced. P5's limiter keeps the old FIFO path selectable by flag until
the soak passes.

## 12. Acceptance metrics

Root-cause report §8 verbatim, measured by the runtime metrics above during a 24 h
paper-mode soak that includes at least one busy hour (≥ 120 trades/s sustained) and one
injected reconnect, plus the deterministic replay gates in the plan. Failure of any gate
blocks the next phase; it does not roll back a passed one.

## 13. Internal contradiction review

- Selection vs. review rank 2 (four consumers): reconciled — the review's isolation
  requirement is met by removing inline REST from lifecycle and by the critical/market
  split; the third and fourth groups bought nothing measurable.
- "Budget unchanged" vs. root-cause §6 (limiter at 40% of budget): deliberate — the
  authenticated budget is not proven to apply to unauthenticated traffic.
- "No journal" vs. review rank 1: the ledger *is* durable candidate state; what is not
  journaled is unclaimed queued prints, which are measured first.
- Prefilter vs. historical-data preservation: capture moves, it does not shrink
  (P3 gate: growth rate unchanged).
