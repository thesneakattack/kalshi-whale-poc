# Realtime Data-Plane — Solution-Family Research (I9)

**Task:** I9 of `docs/superpowers/plans/2026-08-25-realtime-data-plane-investigation.md`.
**Status:** research only. No winner is selected here; I10/I11 benchmark candidates on the I6
harness and the measured workloads, I12 reviews the leader adversarially, I13 selects.
**Method:** three parallel research tracks with separate scopes (WebSocket ingestion and
backpressure; ordering/coalescing/SQLite/process boundaries; request scheduling and
at-least-once reconciliation), each reconciled by the author against the repository's own
measurements before anything below was kept; a fourth track (Kalshi's documented
facilities) was done directly against the mirrored docs. Sources are listed per section.

## 0. What every candidate must satisfy — the measured constraints

These are the numbers from I0–I8 that a solution family is judged against; a pattern that
does not move one of them is not a candidate here, however elegant.

| Constraint | Measured value | Source |
|---|---|---|
| Exchange-wide trade arrival | p50 148 / p95 322 / max 383 msg/s (1-s windows); 130–140/s sustained in the busy hour | I0 §6, I7 |
| Current sustainable service (single consumer) | ~258 msg/s at a 4 ms handler; measured p95 arrival is **not** sustained | I6 |
| Handler cost per trade | 3.3–7 ms mean, of which ~3 ms is thread hop + worker + loop re-entry paid on 100% of trades for 0.2–0.3% candidates | I2, I7 |
| Event-loop stalls | 4–13 s consumer stalls in every busy-hour sample; tick phases of the same size (`capture_flush_and_titles` 0.7–1.6 s every tick, `resolve_and_record` 1–5 s, one 111 s tick) | I2, I7 |
| Queue behaviour under overload | depth p50 17,441/20,000, oldest message p50 119 s, 10.7% dropped; a larger queue only delays and lengthens | I7, H10 |
| Reconnect cost | queued backlog discarded (≈19k messages each), nothing received during backoff; causes seen: keepalive ping timeout, handshake timeouts during a loop stall | I7, I6 |
| Head-of-line effect | lifecycle/ticker/fill messages wait the same ~2 min as trades in one FIFO | I7 |
| Whale-sized capture under saturation | 35% (0% during a drop episode, 100% when the queue is empty) | I4, I7 |
| Candidate loss mechanism | transient enrichment failure ⇒ marked seen, never retried (proven) | I3 |
| REST budget | verified 20 req/s sustained, 60-request burst; local limiter 8/s, burst 8; demand ~1.7 calls/s but bursty | I5, I8 |
| REST latency composition | local limiter wait dominates for 4 of 6 caller classes; background classes hold ~73% of calls and nearly all errors | I5, I7, I8 |
| Batching | flat 10 tokens per request; 200-ticker `GET /markets` complete in 106 ms | I8 |
| Non-negotiables | exchange-wide coverage stays; paper mode; no live `data/*.db` risk; Kalshi boundary preserved | plan |

## 1. What Kalshi's own documentation offers (mirror-only track)

Read against `docs/kalshi/websocket-connection.md`, `quick_start_websockets.md`,
`exchange_sharding.md`, `changelog-index.md`, `get-trades.md`, `get-account-api-limits.md`,
`list-non-default-endpoint-costs.md` and the existing CHEATSHEET entries.

| Facility | What the mirror says | Consequence for solution families |
|---|---|---|
| Exchange-wide `trade` channel | market specification optional; one subscription streams every trade | The product requirement is satisfiable on one subscription; the question is purely local service capacity |
| `shard_factor` / `shard_key` on subscribe | documented **"for communications channel fanout"** only; error codes 19–22 reject bad shard params | A "split the trade firehose across N connections by consistent hashing" family is **not** a documented facility for `trade`; it would need live verification before it can be a candidate, and the CHEATSHEET already flags that |
| Multiple connections | no documented per-account connection limit; the app already runs two (trade/ticker/lifecycle/account vs index) | Physically separate connections per traffic class are documented-safe; they change *which* connection a burst lands on, not the loop that serves it |
| `update_subscription` (`add_markets` / `delete_markets` / `get_snapshot`) | scoped channels can be re-scoped live; no action switches a subscription between scoped and exchange-wide | Ticker scoping stays dynamic; the trade subscription is all-or-nothing |
| Error 25 (subscription buffer overflow) | Kalshi's own outbound buffer per subscription overflowed — "subscribe to a smaller subset or optimize read throughput" | A server-side loss point distinct from local drops; I1 counts it (0 seen so far — all observed loss has been local) |
| Error 26 / 27 | per-subscription market limit (number unstated); per-subscription command rate limit | Bounds on how aggressively a scoped ticker subscription can be churned |
| Keepalive | the `websockets` library handles ping/pong; no interval documented | A blocked event loop can itself kill the connection (1011 keepalive timeout seen in I7) — a loop-hygiene requirement, not a protocol one |
| `market_lifecycle_v2` | exchange-wide, unscopable, already consumed; now carries `exchange_index` | Lifecycle is push-based already; its loss on reconnect is the concern, not its availability |
| Exchange sharding (`exchange_sharding.md`) | categories move to dedicated exchange *instances* (`exchange_index` on markets/events/lifecycle); one WebSocket URL; affects collateral/subaccounts for trading | No data-plane impact today; any real-trading path must carry `exchange_index` — out of scope here, recorded so the remediation design does not ignore it |
| `GET /markets/trades` with `min_ts`/`max_ts`, `limit` ≤ 1000, cursor | exchange-wide REST record of prints, keyed by the same `trade_id` as the stream | The only reconciliation source; costs 10 tokens per 1,000 trades, i.e. ~2 requests/minute at busy-hour flow (I4/I7) |
| `GET /account/limits`, `GET /account/endpoint_costs` | per-account bucket and per-endpoint cost, machine-readable | A scheduler can size itself from the account's own numbers instead of constants (I8) |

## 2. Track A — WebSocket ingestion and backpressure (Python 3.13 / `websockets` 17.0.1)

Reconciled against the installed library source and CPython, plus a container
microbenchmark run by the research track (no app, no pytest). Verified by the author:
the keepalive and handshake mechanisms below match I7's recorded disconnect reasons.

**What the library actually does (17.x asyncio implementation).**

- There is no reader task: bytes arrive via the transport's `data_received`, frames land in
  an unbounded `Assembler` deque; `max_queue` (repo: 1024, low-water 256) is a high/low
  watermark that **pauses transport reading** above the high mark and resumes below the low
  one. Paused reading closes the TCP window, so **backpressure does reach Kalshi** — which
  is where error 25 ("subscription buffer overflow … optimize read throughput") comes from.
  No error 25 has been observed yet: every loss so far is local.
- The 1011 "keepalive ping timeout" close is generated by **this client's own** keepalive
  task (`connection.py`): after a loop stall ≥ 20 s both the ping interval and the pong
  timeout expire together, and if the pong cannot be processed within 20 s (loop stalled
  again, or 1,024 frames to drain first) the client fails the connection itself. The
  documented remedies are a larger `ping_timeout` or `None`; the real fix is not stalling.
- `open_timeout` (10 s) is an `asyncio.timeout`; a deadline already in the past fires on
  the next loop iteration, so a long loop stall **guarantees** "timed out during opening
  handshake" on every reconnect attempt — exactly I7's six failures during the 111 s tick.
- `read_limit` does not exist on the 17.x asyncio client; `recv(decode=False)` returns
  bytes (`json.loads` accepts them); `recv()` is cancellation-safe but must remain
  single-caller (`ConcurrencyError` otherwise); `process_exception` only matters if the
  library's own reconnect loop is adopted.

**Measured cost of the reader pattern (container, 20k iterations):** bare `await recv()`
0.11 µs; `asyncio.timeout` around `recv()` 2.8 µs; the repo's two-task `asyncio.wait` +
cancel 20.9 µs (17.5 with `eager_task_factory`); `json.loads` of a 323-byte trade 3.75 µs;
queue put/get 0.6 µs. The reader ceiling is ~30 µs/message ≈ 30k msg/s — **about 1% of
one core at 400 msg/s**. Wasteful and fragile (its safety relies on `call_soon` FIFO
delivering the cancellation before the next `recv()` task starts), but not the bottleneck.
A reader that only `recv()`s plus a separate task awaiting the subscription-update event
removes the per-message tasks; the library permits concurrent send/recv.

**Load-shedding families for the bounded queue** (ordering and failure semantics stated):

| Family | Why it exists | Fails how | Ordering | Cost |
|---|---|---|---|---|
| Drop-newest (current `put_nowait`) | never blocks the reader | under sustained overload the queue keeps the *oldest* 20k, so every decision is ~2 min stale and the fresh whale print is the one dropped (I7: 0/85) | preserved within survivors | ~0.6 µs |
| Drop-oldest | bounds latency instead of loss | loses history, can evict a critical message if classes share a queue | preserved within survivors | same |
| Coalesce per key (last-write-wins) | state-like classes (`ticker`, `position`) only need the latest value | **wrong for `trade`/`fill`**, which are events | per-key latest only | dict lookup |
| Bounded per-class queues, critical-first | low-volume, must-not-lose classes (`fill`, `position`, `lifecycle`, control) never contend with sheddable trades | a fill can be served before the trade that caused it (cross-class order lost) | preserved within class | dict lookup |
| Reader-side pre-filter | I7: 73% off-list + 27% sub-threshold prints are decidable by a ~1 µs predicate before enqueue | filter drift silently discards signal unless counted like drops | unchanged | ~1 µs |
| Keep the queue across reconnects | `run()` discards the queue and cancels the consumer on disconnect (≈19k messages lost per reconnect in I7) | messages from the old connection precede the new one's (new sid) | unchanged | zero |

Gap detection: the generic message envelope documents `seq` ("check if you want to
guarantee you received all the messages"), but `public-trades.md` does not list it on the
trade message — **verify live** (the gateway can record whether trade envelopes carry
`seq`) before any design leans on it.

**Event-loop hygiene:** `loop.slow_callback_duration` only works under `set_debug(True)`
(not free); a watchdog task measuring `sleep(d)` drift detects the stalls I2 could only
infer; `asyncio.to_thread` always uses the default executor (20 threads here) — off-loop
SQLite needs a dedicated single-worker executor per database with one connection per
thread (`check_same_thread`), because `_sqlite` releases the GIL around `sqlite3_step` and
work does overlap. `uvloop` accelerates loop/transport machinery that costs µs here while
the problem is ms–s of Python and synchronous SQLite; it cannot shorten a stall and has an
open 3.13 transport bug — not worth adopting on this evidence.

**Families for the ingestion bottleneck (not ranked):**

1. **Single-loop hygiene** — dedicated per-DB executors for the tick's synchronous SQLite,
   plain-`recv()` reader plus a separate subscription task, persistent queue across
   reconnects, a stall watchdog. Lowest burden; removes the measured stall cause *if the
   correlation holds*; leaves FIFO staleness to family 2.
2. **Class-aware bounded queues** — critical-first per-class queues, drop-oldest or
   pre-filter for trades, `seq`-gap accounting where available. In-process, µs cost,
   already benchmarkable in the I6 harness; adds cross-class ordering caveats.
3. **Process isolation** — ingest + cheap whale filter in a separate process so trading-tick
   work cannot touch the reader. Highest burden: IPC serialisation (≈ the `json.loads`
   cost again), a second failure domain the I1/I2/I4 diagnostics do not observe,
   cross-process ordering and restart semantics.
4. **Vendor sharding (adjunct only)** — documented for `communications`, unverified for
   `trade`; only helps if consumers are parallel, i.e. pointless on one stalled loop.

Why not heavier infrastructure: the requirement is ≤ 400 msg/s against a ~30 µs reader and
a ~5 ms consumer — two orders of magnitude of headroom in a hygienic single process, and
every multi-process design re-pays serialisation and adds an unobserved failure domain
without touching the stall source unless family 1 is done anyway.

## 3. Track B — ordering, coalescing, SQLite on the loop, and process boundaries

Reconciled against the repo and a container micro-benchmark run by the research track
(Python 3.13.15, SQLite 3.46.1, GIL build, temp DB — not the app). Verified by the author:
the tick's `series_stats()` N+1 (`main.py:736`, one fresh connection per watchlist
market), and `candidate_log._connect()` re-running the WAL pragma and `CREATE TABLE` on
every rejection write.

**Where the per-message milliseconds actually go (benchmarked):**

| Operation | Cost |
|---|---|
| `asyncio.to_thread(lambda: None)` round trip | 139 µs |
| foreign-thread `call_soon_threadsafe` wake reaching the loop | p50 317 µs · p95 444 µs |
| loop re-entry while another thread holds the GIL | up to one switch interval — **5.26 ms** worst (default `sys.getswitchinterval()` = 5 ms) — i.e. exactly I2's "unattributed remainder" band |
| the repo's `_connect()` idiom per write (connect + `PRAGMA journal_mode=WAL` + `CREATE TABLE IF NOT EXISTS` + `table_info` + INSERT + commit + close) | **12.6 ms** |
| same INSERT + commit on a persistent connection, `synchronous=FULL` (one WAL fsync) | 4.67 ms |
| … at `synchronous=NORMAL` | 0.073 ms |
| `executemany` in one transaction | 1.5 µs / row |
| per-call-connect indexed read vs persistent | 190 µs vs 10.6 µs |
| 1,000-row `executemany` into a 200k-row `raw_trades`-shaped table (TEXT pk + 2 indexes) | 13 ms — consistent with 0.7–1.6 s per 1–2k rows into the live 16.9 M-row table with cold index pages |

So the 27%-of-trades rejection write (`candidate_log.record_rejection`, ~26/s in the busy
hour) costs roughly a third of one worker's wall time on its own; the ticker handler's 25 ms
average is the connect idiom on the loop (`market_history.record_snapshot_from_ticker`);
and `capture_flush_and_titles` is the `raw_trades` flush **plus** 150 `series_stats()`
connections per tick, all on the loop.

**Ordering, as guaranteed today:** one connection, one FIFO, one consumer, one in-flight
`fetch_signals` — the dedupe ring is documented as safe *only because* one call is in
flight at a time. Kalshi's `seq` (where present) is per-subscription gap detection, not
per-market ordering.

**Keyed partitioning (per-ticker order while parallelising across tickers):**

| Pattern | Ordering | Python cost | Repo fit |
|---|---|---|---|
| N loop tasks, `hash(ticker) % N` | per-key FIFO kept; cross-key order not (nothing cross-ticker is order-sensitive except global counters) | dispatch 0.3 µs, queue put+get 0.5 µs; concurrency, not CPU parallelism | good — isolates one key's REST await from other keys; the shared dedupe ring stays safe only while `_mark_seen` has no `await` between `in` and `add`; simpler: partition the ring per consumer so a `trade_id` can only ever be seen by one |
| N worker threads, one per partition | per-key FIFO within a thread; results re-enter the loop in *completion* order | GIL contention during Python portions; the loop's latency floor rises to the 5 ms switch interval; one connection per thread | write parallelism is illusory for one file (WAL: one writer at a time, serialised behind `busy_timeout`); parallelism exists only *across* files, which the one-DB-per-concern layout already gives |
| single writer per resource | total order per resource, trivially | one thread-queue hand-off 2–5 µs + 0.3 ms wake if a result is needed | the natural shape for SQLite: one writer thread per DB file (or one for all, sequenced) |

**Coalescing vs streams:** a latest-value map (`dict[ticker] → newest`, dirty set, one
Event) is correct for `ticker` on open positions — only the last mark matters to
`check_exits` — and costs ~0.1 µs; apply only if the stored `ts` is older, or a stale
coalesced update can regress state after a lifecycle event; its failure is that any
path-dependent feature (max adverse excursion, spread history) cannot read from it.
Coalescing is **wrong** for fills/positions (each is money), lifecycle
(`created → determined → settled` is a state machine; dropping `determined` breaks the
`finalized` gate) and trades (per-event dedupe). Those need an at-least-once channel the
reader never sheds; the reader already classifies at enqueue, so routing
`ticker → conflating map`, `fill/position/lifecycle → never-shed FIFO`, `trade → batched
path` is a dispatch change, not a transport change.

**SQLite facts that constrain every family:** WAL — readers and writers don't block each
other, **one writer at a time**, autocheckpoint at 1,000 pages, and a permanently-open
reader starves checkpoints; `synchronous=NORMAL` in WAL is durable across application
crashes and may lose only on power loss; SQLite's own FAQ: tens of thousands of inserts per
second but "only a few dozen transactions per second"; Python's default 5 s `timeout` is a
`busy_timeout` *sleep on whichever thread is writing* — 5 s on the loop thread would itself
be a stall; the implicit deferred `BEGIN` can hit `SQLITE_BUSY` on upgrade — writers should
use `isolation_level="IMMEDIATE"`; `sqlite3.connect()` itself is 42 µs — the 12.6 ms is the
DDL/pragma/fsync the idiom bolts on; unclosed `Connection`s warn in 3.13.

**Families for the per-message thread-hop cost (not ranked):** (1) filter before hopping —
the contract-count rejection deciding 99.7% of trades costs microseconds and runs inside
the hop today; hop only candidates and batch the rejection write; (2) batch the hop — drain
up to K messages / T ms per `to_thread`, persistent connection inside, FIFO preserved,
≤ T ms added latency; (3) a dedicated DB thread with persistent per-file connections
(`IMMEDIATE`, `NORMAL`) fed by a `queue.Queue`, replying via `call_soon_threadsafe` —
structural single writer, warm statement cache, isolated from the 20-thread default
executor that `storage_health`/`research`/`backup` also use; (4) in-memory per-ticker
state on the loop with SQLite as write-behind only — zero hops, trivially ordered, at the
price of an explicit rebuild-on-start path.

**Families for the loop stalls caused by the tick's synchronous SQLite (not ranked):**
(A) move the tick's SQLite phases to the DB thread/executor — I/O runs GIL-free, Python
row marshalling still contends in 5 ms slices; (B) shrink the work — flush `raw_trades`
every second instead of per tick, replace the 150× `series_stats` N+1 with one grouped
query or a per-tick cache, `synchronous=NORMAL` on WAL files (4.67 → 0.073 ms per commit);
(C) a write-behind queue for every `data/*.db` write, batched per file per interval —
acceptable for `raw_trades`/rejections/snapshots (the 20,000-row buffer already has that
semantics), **never** for `paper_broker.db`/`risk_state.db`/fills, which must stay
write-through-and-await (the kill-switch invariant); (D) `asyncio.sleep(0)` between phases
— inadequate, each phase alone is 0.7–5 s; (E) a process boundary — below.

**Process boundaries and brokers — honest verdict at this scale:** the CPU share of a trade
message is ~0.3 ms (~12% of one core at 400 msg/s); nothing is CPU-bound, and the stall is
I/O *on the loop thread*. `ProcessPoolExecutor` pickles every payload both ways, cannot
share the dedupe ring, `state` or connections, still defaults to `fork` on Linux in 3.13
(SQLite documents carrying an open connection across `fork()` as a corruption risk), and
does nothing for the loop stall. A second analytics process over a socket costs a wire
protocol, backpressure design and restart choreography inside ddev — and the sender is
still the loop that must stay responsive. Brokers (Redis Streams, NATS/JetStream, ZeroMQ):
an `asyncio.Queue` moves ~2 M items/s — capacity is not the problem; a broker hop is
≥ 100 µs each way plus a syscall, more than the 139 µs hop it would replace; none fixes a
blocked loop, the reconnect discard, or the WAL single-writer rule; each adds a service to
run, persist and monitor. Conditions that would flip this: durable capture of the raw feed
across restarts/crashes (today the 20k-row buffer and the queue die with the process),
fan-out to more than one consumer process, CPU work above one core, or a second host.

**Python 3.13 facts that matter:** the container runs the GIL build (free-threading is
experimental with a ~40% single-thread penalty; 3.14 makes it supported at 5–10%);
`Connection.autocommit` exists but the default remains legacy transaction control, so the
repo's `with conn:` commit-on-exit is unchanged; `asyncio.Queue.shutdown()` /
`QueueShutDown` (3.13) is the direct tool for draining instead of discarding on reconnect;
`eager_task_factory` is measurable only after the ms-scale costs are gone; 3.14's
`python -m asyncio pstree` would be the first-class stall diagnosis — on 3.13 it is a
watchdog task.


## 4. Track C — critical-vs-background REST scheduling under one budget

Reconciled against `services/http_client.py`, I5/I7/I8 and the mirror. Verified by the
author: the WebSocket session limit ("default 200, increases with tier", changelog) and the
absence of any anonymous-traffic limit statement in `rate_limits.md`.

**What the evidence constrains.** Shape, not volume: 1.67 calls/s average (8% of the
verified 20 req/s) yet limiter wait dominates latency because the tick's `asyncio.gather`
fan-outs and catalog batches arrive as bursts of up to 34 waiters against an 8-token local
burst. A 429 storm today costs whoever is unlucky up to ~7.5 s of backoff (4 retries at
0.5/1/2/4 s), the bucket having no notion of who a token is for. Background classes hold
~73% of calls and nearly all errors.

| Family | Mechanism | Starvation / fairness | 429 behaviour | Cost & burden | Repo fit |
|---|---|---|---|---|---|
| **C1 Demand reduction only** (eliminate, coalesce, cache, de-burst: 200-ticker batches at the same 10 tokens, stop re-polling the 15%-failing live-status targets, measure H9 duplicates, stagger the tick's fan-out) | fewer and flatter calls | none — nothing to starve if peaks fit the burst | the only family that also reduces upstream exposure | per-site edits, no abstraction; decays silently without a per-class share/limiter-wait alert | highest; matches the measured mechanism (bursts) — but guarantees nothing for `critical_whale` |
| **C2 Raise the local limiter toward the verified budget** (burst 8 → 60 alone absorbs every measured burst) | more tokens | no reordering | converts local waits into upstream 429 backoff that lands on critical callers too | trivial code; evidence-gated | the 20 req/s figure is an *authenticated* number; this app's market-data client is unauthenticated and the mirror says nothing about anonymous limits — needs a stepped-rate probe (anonymous vs authenticated, per-IP?) or signing the market-data reads (a boundary change) first |
| **C3 Strict priority with aging** (dispatcher releasing waiters from a priority queue keyed by caller class; background ages up after T s; per-waiter deadlines) | ordering | critical bound ≈ one refill; aging prevents background starvation | 429'd critical calls re-enter at critical priority; fail fast into the retry state machine | ~100–150 lines in `http_client.py`, no call-site change; one new failure class (a stuck dispatcher stalls everything — needs a watchdog metric) | good: attribution already exists; the only family that guarantees what C1 cannot |
| **C4 Weighted fair queuing / DRR across classes** | proportional shares | excellent between background classes; critical worst case ≈ Σ other quanta / rate (0.3–0.75 s) — worse than C3 for the hot path | same as C3 | C3's code plus a second set of magic numbers (weights) | justified only if background-vs-background fairness is a stated target |
| **C5 Reserved capacity / hierarchical bucket** (critical sub-bucket with borrowing from background, never the reverse; global brake on 429) | partition | critical cannot starve while its demand ≤ reserve | sub-buckets are blind to upstream — pair with a 429 brake | smallest scheduling family; one mis-sizable number; strict partitions waste idle reserve | high, especially with C2 headroom (a 4–6/s reserve costs background nothing measurable at 20/s) |
| **C6 Per-class concurrency caps** | admission | bounds burst amplitude per class only | reacts to the wrong signal (RTT, not tokens) | cheap | complement, not a solution — the repo already learned Kalshi meters throughput, not concurrency |

Cross-cutting: every family must keep local attempts *including retries* under the
upstream budget, route retries back through the scheduler, keep jitter, and expose
per-class wait, waiter age, 429s by class and a "critical waited behind background"
counter (I5 provides most of it). Honest ordering of the evidence: C1 alone plausibly meets
the measured target — bursts of ≤ 34 against a burst of 60 need no scheduler if the tick's
fan-out is flattened; scheduling machinery buys a *guarantee* for a 9%-share class at the
price of a new failure mode, so I11 must benchmark it against C1 + evidence-gated C2.

## 5. Track D — reconciliation and at-least-once candidate evaluation

The stream delivers at-most-once (drops, reconnect dumps, no replay) and the consumer
evaluates at-most-once (seen before evaluated); at-least-once needs a retry path *and* a
recovery source, and exactly-once *effect* then needs an idempotent decision. Verified by
the author: `signal_log.signals` has only an autoincrement primary key (a re-evaluated trade
would log twice); `strategy_engine` refuses a second position on a ticker already held
(idempotent per market, not per trade); `series_watcher.raw_trades` keys on `trade_id`.

| Family | Mechanism | Semantics | Cost / ring interaction | Fails how |
|---|---|---|---|---|
| **D1 In-process retryable candidate state machine** (`pending → resolving → evaluated | abandoned`; mark seen only at a terminal state; bounded pending set with per-candidate backoff; never cache a *failed* batch as negative; shorten the 300 s negative cache for whale-sized prints) | retry | exactly-once within a process; at-least-once across restarts only with D2 | candidates are 0.2% of trades (~800/h); a 10k cap with a staleness horizon bounds it; ring = terminal, pending is separate | under a 429 storm the oldest pending are abandoned — visibly; duplicate evaluation if a late re-presented copy and the retry both evaluate (needs D5). **The minimal H4 fix is this family at its smallest** and flips the strict xfail |
| **D2 Persistent candidate journal** (`data/candidate_journal.db`, additive schema, `INSERT OR IGNORE` on `trade_id`, batched flush) | durable retry | at-least-once across restarts and `--reload`s; doubles as the decision ledger | ~800 rows/h; trivial unless written synchronously on the hot path | a per-message or synchronous write reproduces the stall class that caused the loss |
| **D3 Bounded REST reconciliation after detected loss** (trigger on drops / reconnect / error 25; page `GET /markets/trades` over the gap; prescan `count_fp` locally; feed unseen whale-sized ids into D1) | recovery | recovers what the stream lost | ~13–24 pages per 2-min gap = seconds of the verified budget but ~3 s of the local bucket — needs its own caller class; reconcile promptly (ring evicts in 10–30 min), honour `seen_horizon` and `backlog_exceeds_lag` | the stall that caused the loss also delays reconciliation; both paths may evaluate the same print (D5); a recovery for reconnect-shaped gaps, not a substitute for fixing saturation (35% vs 100% capture) |
| **D4 Periodic sampled completeness audit** (a 60 s window every ~15 min → observability metrics + quality finding) | detection | none — makes I4's tool the permanent recurrence detector | ~0.1 req/s amortised | can miss short episodes; a floor, with I1's drop/reconnect counters as D3's trigger |
| **D5 Duplicate-decision safety** (idempotency key = `trade_id` for every decision; additive `UNIQUE` on the signal's source id or a ledger in D2) | exactly-once effect | makes at-least-once safe | one index / one column | a ticker-level guard would hide a legitimate second whale on the same market — key on the trade, not the market |

Why not build most of this: if the ingestion redesign removes saturation, loss collapses
to the reconnect case (~2/h measured) and D1 (minimal) + D5 + D4 detection may suffice;
D2/D3 should be justified by post-fix loss measurement. The mirror offers no
replay/resume/gap-fill for `trade`; error 25 tells you Kalshi-side loss happened but does
not replay it; REST `GET /markets/trades` is the only documented recovery source.

## 6. Synthesis — candidate families per confirmed bottleneck

No winner. Each confirmed bottleneck lists at least three meaningfully different families
plus the families judged not credible and why. Every entry carries the attributes the plan
requires (mechanism, appropriateness, failure semantics, ordering, Python cost, burden,
repo fit) in its track section above; this table is the index I10/I11 benchmark from.

| Confirmed bottleneck (evidence) | Credible families | Judged not credible here (and why) |
|---|---|---|
| **B1 Event-loop stalls starving the consumer** (I2/I7: 4–13 s stalls every sample, 111 s worst; tick phases of the same size; 1011 keepalive and handshake timeouts as consequences) | Loop hygiene (A-family 1: off-loop SQLite via a dedicated writer thread, plain-`recv()` reader, watchdog) · shrink the tick's work (B-family B: 1 s flush cadence, N+1 removal, `synchronous=NORMAL`) · write-behind queue for non-money stores (B-family C) · process isolation (A-family 3) | `asyncio.sleep(0)` between phases (phases are seconds); uvloop (does not touch blocking work; open 3.13 bug); a broker (a blocked loop stays blocked) |
| **B2 Per-message cost paid on every trade for 0.3% candidates** (I2/I7: 100% thread hops, 3–7 ms floor, 27% rejection writes at 12.6 ms each) | Filter before hopping + batched rejection writes · batch the hop (K/T) · dedicated DB thread with persistent connections · in-memory per-ticker state with write-behind | More consumer threads writing one file (WAL single writer); `ProcessPoolExecutor` (pickling cost > hop cost, cannot share the ring) |
| **B3 One FIFO: critical classes inherit the trade backlog** (I7: lifecycle/ticker/fill ~2 min stale) | Per-class bounded queues, critical-first · coalescing map for `ticker`/`position` + never-shed FIFO for `fill`/`lifecycle` · separate physical connections per class (documented-safe) | Vendor trade sharding (`shard_factor` is documented for `communications` only — needs live verification before it is even a candidate); priority within a single queue via re-sorting (O(n) on a 20k queue) |
| **B4 Reconnect discards the queued backlog** (I7: ~19k messages twice; I6 model) | Keep the queue and consumer across reconnects (`Queue.shutdown` only on real stop) · bounded REST reconciliation of the gap (D3) · periodic completeness audit as detector (D4) | Larger `ping_timeout` alone (hides the stall, keeps the discard); relying on Kalshi replay (none is documented) |
| **B5 Critical REST calls queue behind background bursts in an undersized local bucket** (I5/I7/I8: limiter wait dominant; 8-token burst vs verified 60; 73% background share; 15% live-status failures) | Demand reduction/de-bursting (C1) · evidence-gated limiter sizing from the account's own numbers (C2, after an anonymous-ceiling probe or signing market-data reads) · reserved critical capacity (C5) · strict priority with aging (C3) | Per-class concurrency caps as the fix (Kalshi meters throughput, not concurrency — already learned live); weighted fair queuing as the primary tool (optimises background fairness, not critical latency) |
| **B6 Transient enrichment failure loses the candidate forever** (I3 proven) | Retryable candidate state machine (D1, minimal H4 fix) + idempotency key on `trade_id` (D5) · persistent journal (D2) · reconciliation feeding retries (D3) | Widening the negative-cache TTL logic alone (does not retry the lost print); a ticker-level dedupe guard (hides a legitimate second whale) |

**Cross-cutting invariants any combination must keep:** exchange-wide `trade`
subscription; per-ticker ordering for trades; fills/positions/lifecycle never shed, never
coalesced; money stores write-through; decisions idempotent on `trade_id`; every shed,
drop, reconnect and retry counted by class (I1/I2/I4/I5 already own the counters);
Kalshi boundary and safety gates untouched.

**Why the simplest in-process designs remain in the running (reasons *not* to adopt
sophisticated infrastructure):** the requirement is ≤ 400 msg/s; the reader costs ~30 µs
and a hygienic consumer ~5 ms per message, i.e. two orders of magnitude of headroom in one
process; the measured causes are a blocked loop, an 8-token burst and a seen-before-
evaluated bug — none of which a broker, a process pool, uvloop or vendor sharding
addresses; each of those adds a failure domain the current diagnostics do not observe and
a service or dependency to operate.

## 7. Open verifications carried into I10/I11 (facts, not designs)

1. Does the `trade` message envelope carry `seq` in practice? (Documented on the generic
   envelope and other channels, absent from `public-trades.md`.) One-line gateway sampling
   answers it; gap detection depends on it.
2. Does the anonymous market-data ceiling match the authenticated 20 req/s / 60-burst? A
   stepped-rate probe with `tools/kalshi_rate_limit_probe.py` (anonymous vs authenticated;
   per-IP?) — required before C2 can be sized. Alternative: sign market-data reads (a
   boundary change with its own review).
3. Does `shard_factor`/`shard_key` apply to `trade`? Only if a split-connection family
   survives I10 on other grounds — otherwise not worth the live test.
4. H9 milestone duplicate factor from the exact `kalshi_rest_endpoint.*` counters (probe
   scheduled; result recorded in `2026-08-25-rest-demand-study.md`).
5. The causal link for the loop stalls: I10 must reproduce "tick-phase SQLite on the loop ⇒
   consumer stall ⇒ queue growth" in the replay harness with measured phase durations, and
   the remediation's own watchdog must show the stalls gone before the correlation is
   promoted to cause.

## Sources (beyond the repo files named inline)

`websockets` 17.0.1 installed source (`asyncio/connection.py`, `messages.py`,
`client.py`) and docs — https://websockets.readthedocs.io/en/stable/topics/memory.html ,
…/topics/keepalive.html , …/reference/asyncio/client.html , …/howto/patterns.html ,
…/faq/asyncio.html ; CPython 3.13 — asyncio-task, asyncio-eventloop, asyncio-dev,
asyncio-queue, concurrent.futures, sqlite3, sys (`setswitchinterval`), whatsnew 3.13/3.14,
free-threading howto; `Modules/_sqlite/cursor.c` (GIL released around `sqlite3_step`);
SQLite — https://sqlite.org/wal.html , pragma.html , isolation.html , lang_transaction.html ,
faq.html , howtocorrupt.html ; Google SRE "Handling Overload" and "Addressing Cascading
Failures"; Brooker, "Exponential Backoff And Jitter" (AWS); Shreedhar & Varghese, DRR
(IEEE/ACM ToN 1996); `tc-htb(8)`; Netflix concurrency-limits; Richardson, "Idempotent
Consumer"; Stripe idempotent requests; Kafka intro (partition ordering); Thompson,
"Single Writer Principle"; ReactiveX backpressure operators; Redis Streams and persistence
docs; NATS core concepts; ZeroMQ guide ch. 2 and `zmq_setsockopt`; uvloop repository and
issue #685; Kalshi mirror pages named in §1.
