# Research: solution-family comparison for the trade-resolve consumer-blocking bottleneck

2026-09-03, ~21:15–22:00 UTC. Requested by autotrade-ce (coordinator) after Gate 2
measurement of PR #526 (`services/whale_stream/whale_stream_handlers.py`'s ticker-path
throttle) surfaced a second, distinct, previously-unnamed blocking mechanism on the
trade-message path (issue #542), plus a correlated data-plane completeness loss
(issue #541, 140,495 dropped trade messages). Both were root-caused with source
state-transition proof and confirmed against live 24h metric history; #541's own
comment thread ties the two together with a reconnect-free 33-minute saturation
window that the "restart resubscribe surge" alternative cannot explain. This document
does not re-litigate that root cause — it takes #542's finding as given and answers
the coordinator's follow-up question: now that the mechanism is known, what are the
genuinely viable ways to fix it, compared on mechanism, benchmark, correctness,
failure behavior, and complexity, per CLAUDE.md's data-plane HARD RULE ("a confirmed
bottleneck gets competing solution families compared … not the first fix that
works")?

**No code, config, or live-app changes were made.** Every number below comes from a
live `GET` against `/api/observability/history`/`summary`, a `grep`/`Read` of current
source, or a `git log`/`show`, cited inline. This document is itself a planning-stage
artifact under CLAUDE.md's "nothing advances on one pass" HARD RULE — it gets its own
self-review, adversarial review, and consolidation before anyone treats its
recommendation as a decision, and a further design/spec or implementation-plan stage
(with its own cycle) before any code is written.

**Top-line finding, stated up front:** of the three solution families the coordinator
named, only one (bounded-concurrency resolve, §3) addresses the root mechanism #542
identified — an awaited REST call serially blocking the sole trade-queue consumer.
The other two reduce how often or how long a blocking call happens, not the fact that
it blocks. A new correctness fact discovered while researching this comparison,
not previously known: `realtime_data_plane.two_consumer_mode: true` is live in
`config/settings.yaml:72` today, which means trade-class and ticker-class messages
share the *same* single consumer (`_consume_market_from`, draining `market_queue`) —
so a multi-second resolve stall on a trade message also delays ticker-message
processing (price freshness, `check_exits`), not just trade throughput. This raises
the stakes on all three options and is folded into each one's analysis below.

## 1. The mechanism, restated precisely (from #542, re-verified here)

`_process_stream_trade` (`services/whale_stream/whale_stream_handlers.py:184`) is the
sole live call site for `fetch_signals()` in streaming mode
(`_streaming_trade_tape_enabled()`, `whale_stream_handlers.py:99-104`, true whenever
the real provider + WS credentials are active — the app's normal running state,
confirmed live: `state["whale_source"]` reports `"(websocket)"`). It always calls:

```python
signals = await whale_provider.fetch_signals(
    market_context={"markets": state["markets"], "trade_tape": [trade], "cfg": cfg_now, ...},
)
```

`trade_tape=[trade]` — exactly one trade. `fetch_signals` (`kalshi_trade_tape.py:284`)
awaits `_resolve_unknown_markets` inline, which — for any print whose ticker isn't
cached and clears the whale-size gate — issues a batched `client.get_markets_by_tickers(batch)`
REST call and *waits for it* before returning control to the caller.

The consumer that runs all of this is `_consume_from`/`_consume_market_from`
(`services/kalshi/websocket.py:1127-1175`), a strictly sequential loop:
`item = await queue.get(); await self._process_item(...); queue.task_done()`. Exactly
one consumer task is spawned per queue (`websocket.py:665-695`). So for the duration
of one resolve call, the sole consumer cannot dequeue the next message — of any class.

Verified live, 1:1, no batching ever occurs on this path: `whale_pipeline.counter.offlist_candidates`
== `resolve_calls` == `kalshi_rest_class.critical_whale.calls` — not just their averages
but `min`, `max`, and `avg` all identical across all three metrics (67.0 / 110.0 / 87.0,
6 samples each) in the same ~9-minute post-restart window
(`/api/observability/summary?hours=0.15`, current generation started
`2026-09-03T21:20:25Z` per `docker logs ddev-kalshi-whale-poc-fastapi`).
`trade_tape` is never longer than 1 in the streaming path, so `wanted` (the resolve
batch) is never longer than 1 either — "batched via `get_markets_by_tickers`" is true
of the client method's signature, never exercised as batching in production.

### 1.1 New fact: trade and ticker share one consumer today

`config/settings.yaml:72`: `realtime_data_plane: two_consumer_mode: true` — live, not
the documented default (`websocket.py`'s own comment calls it "default off"; the
live value overrides that). With it on, `_ingest_raw` (`websocket.py:1021-1065`)
routes every message class not in `_CRITICAL_CLASSES = {"fill", "position",
"lifecycle", "control"}` (`websocket.py:135`) to one shared `market_queue`. "trade"
and "ticker" are both outside `_CRITICAL_CLASSES`, so both land there — ticker via
`_coalesce_ticker` (`websocket.py:1080-1119`, absorbed into a per-market pending map,
newest-wins), trade via the ordinary `queue.put_nowait` path. Exactly one consumer,
`_consume_market_from` (`websocket.py:1127-1155`), drains `market_queue`: real queue
items first-in-first-out, then one pending coalesced ticker per idle iteration.

Consequence: a multi-second stall inside `_process_stream_trade`'s resolve call is
not a trade-only problem. It blocks the same consumer from reaching the next queued
ticker update (or the next trade) until the resolve returns. This directly implicates
the shared consumer as a contributor to *both* #526's originally-diagnosed symptom
(stale positions, delayed `check_exits`) and #542's (trade queue depth) — #526's
throttle reduced the *cost of running* the ticker-side block once dequeued, but does
nothing if the consumer is stuck *before* even reaching that ticker item because a
trade message ahead of it triggered a resolve call. This was not identified in #526's
own research or in Gate 2 — it surfaces here because this comparison required tracing
the actual live queue topology, which neither prior investigation needed to do.

### 1.2 Cost distribution, precisely (24h history, `/api/observability/history`)

| stat | resolve.window_avg_ms (n=633) | resolve.window_max_ms (n=633) |
|---|---:|---:|
| median | 0.59 ms | 1,421.35 ms |
| p90 | 1.51 ms | 3,113.78 ms |
| max | 96.86 ms | 15,379.21 ms |

`window_avg_ms`'s near-zero median confirms the prescan gate is doing its job — the
~99%+ of trades that never need a resolve cost nothing. `window_max_ms` is the
figure that matters: in a typical ~75-90s sampling window, the *worst* resolve call
observed already costs 1.4s at the median, 3.1s at p90, spiking to 15.4s over the
full 24h window (8.85s was the value that directly correlated with the sharpest
queue-depth jump in #541's incident-window analysis). `critical_whale.network.window_max_ms`
independently measured up to 44,580 ms over the same 24h — pure Kalshi-side response
latency, before this app's own rate limiter is even in the picture.

## 2. Constraints any option must satisfy

- **Completeness (CLAUDE.md's data-plane HARD RULE):** every whale-sized print must
  still reach a scoring decision. None of the three options may drop, merge, or
  silently skip a trade to solve this — that would trade one data-plane defect for
  another under the same rule that flagged #541. This rules out treating the ticker
  path's `_coalesce_ticker` (§1.1, newest-exchange-ts-wins, older discarded) as a
  template to copy: it is safe there because a ticker update is redundant level-state
  once superseded; a trade print is a distinct, non-substitutable event. Nothing
  below proposes coalescing/dropping trades — only batching or de-serializing the
  *resolve calls* they trigger.
- **No queue-capacity/rate/batch-size tuning "because it should help"** (CLAUDE.md):
  the bottleneck is confirmed and its mechanism is proven from source (§1), so this
  constraint is satisfied — but the comparison below still may not lean on "raise the
  cap" as an option, and doesn't.
- **Not safety-adjacent** in the sense of ROADMAP.md/`docs/next-action.md`'s Task
  6/7/8 gate (`risk_manager.py`, `paper_broker.py`, `candidate_ledger.py`) — this is
  whale-signal ingest, not risk/sizing/settlement. Still real-money-adjacent in the
  sense that a signal delayed or lost changes what the strategy ever sees, so
  correctness scrutiny stays high; it just doesn't require the extra human-go-ahead
  gate those three modules do.
- **Exchange-wide hot path:** per CLAUDE.md, "any diagnostic or abstraction on the
  exchange-wide hot path is measured for runtime cost before it ships" — applies to
  whichever option is eventually implemented, not decided here.

## 3. Option B — bounded-concurrency resolve (decouple resolve+score from the consumer)

**Mechanism.** The consumer (`_consume_market_from`) currently does the free prescan
gate *and* the resolve call *and* the scoring *and* signal emission, all inline,
before it can dequeue the next item. Split this: the consumer still runs the prescan
gate synchronously (already "pure arithmetic on the print itself — no DB, no
network," per `_resolve_unknown_markets`'s own docstring, sub-millisecond) but, for a
print that needs a fresh resolve, hands the rest off to a bounded pool of concurrent
tasks (a semaphore-gated `asyncio.create_task`, or a small dedicated async worker
pool) and immediately loops back to `queue.get()` for the next item — trade or
ticker.

This codebase already has a directly analogous precedent for "give this call path
its own small, bounded, dedicated pool instead of the shared default":
`services/whalewatchers/_scoring_pool.py` — a 4-worker `ThreadPoolExecutor`,
introduced specifically so `_process_stream_trade`'s synchronous scoring work
doesn't share Python's unbounded default `asyncio.to_thread` executor. Its own
docstring: "4 workers: this call path normally needs ~1 concurrently … headroom for
legitimate brief overlap … bounded and observable is this design's actual goal." The
same reasoning applies here, on the async-I/O side rather than the thread side.

**Correctness — the load-bearing finding of this comparison.** `_resolve_unknown_markets`
resets shared, per-provider mutable state unconditionally at the top of every call:

```python
self._resolve_failed_tickers = set()   # kalshi_trade_tape.py:401
```

with the comment "stale state from a prior tick must never leak into this one's
mark-seen decision." That comment assumes exactly one call in flight at a time — true
today (strictly serial), false the moment concurrency is introduced. Under N
concurrent in-flight resolves, a later call starting while an earlier one is still
awaiting its REST response would wipe `self._resolve_failed_tickers` out from under
the earlier call before it finishes populating/consuming it — silently reintroducing
the exact "permanently unresolvable, no retry" failure mode the surrounding code
comment (`kalshi_trade_tape.py:404-408`) says was already fixed once (H4/Task 11) for
a different case. **This is a must-fix precondition for Option B, not an
implementation detail to sort out later** — it has to be made per-call-local (a
returned set, not `self.` state) before any concurrency is introduced, or Option B
reopens a bug this codebase already paid down once.

**A second, more serious finding from adversarial review (F6): `self._seen_trade_ids`/
`self._seen_order` (the mark-seen dedupe ring) are already exposed to concurrent
mutation *today*, independent of anything Option B introduces.**
`services/whalewatchers/_scoring_pool.py`'s own module docstring states its 4-worker
pool exists for "**both** the WS-message path, `_process_stream_trade -> fetch_signals`,
**and** the candidate-retry path, `score_recovered_trade`" — sized with "headroom for
legitimate brief overlap **plus the candidate-retry path**." But `_process_trades_sync`'s
own docstring claims the opposite safety property: "`self._seen_trade_ids`/`self.
_seen_order` are only ever touched from within **one in-flight `fetch_signals()`
call at a time**." `score_recovered_trade` calls `_process_trades_sync` directly —
not via `fetch_signals()` — so that safety claim's own stated scope doesn't cover it,
by its own wording. Tracing the call graph: `_candidate_retry_loop` runs as its own
independently-supervised task, concurrently with the WS stream task, waking every 5s
and (when streaming mode is active — the live default) submitting
`score_recovered_trade` work to the **same** `_scoring_pool` the WS consumer uses.
Today, with zero code changes, the WS stream's `_process_trades_sync` call and the
candidate-retry loop's `_process_trades_sync` call can already run on two different
worker threads of the same pool, both mutating the same unlocked
`self._seen_trade_ids` (`set.add`/`in`) and `self._seen_order` (`deque.append`/
`popleft`, with a multi-statement eviction check) — the same class of hazard
identified above for `_resolve_failed_tickers`, but pre-existing and live right now,
not introduced by Option B. CPython's GIL makes each individual `set`/`deque` call
atomic, so this is a narrow-interleaving risk inside the eviction check's multi-
statement sequence, not a certainty on every overlap — source-derived reasoning from
two directly-contradicting docstrings plus a traced call graph, not an empirically
reproduced failure.

`self._market_cache` (the TTL cache), by contrast, **is** safe as originally
characterized: it is only ever touched inside `_resolve_unknown_markets`, which is
only ever called from `fetch_signals()`, which has exactly one live caller at a time
today (`main.py`'s tick-loop path and the streaming path are mutually exclusive by
construction — see §4's `_streaming_trade_tape_enabled()` branch). Confirmed safe,
not merely unaudited.

**Consequence for scoping:** the `_seen_trade_ids`/`_seen_order` hazard needs its own
fix (a lock around `_mark_seen`, or a concurrency-tolerant dedupe-ring restructure) —
it must not be folded silently into "the Option B precondition," since it already
exists independent of whether Option B is ever built, and fixing only
`_resolve_failed_tickers` for Option B's sake would leave it in place exactly as
today. Filed separately as issue #546 so it isn't lost inside this document, per this
repo's own standing practice of not letting a finding live only inside a research doc.

Ordering: `_consume_market_from`'s own docstring already states "trades keep strict
arrival order, tickers are level state where cross-market order is deliberately
unguaranteed" (design spec §3, cited in the docstring) — i.e., cross-*ticker* order
was never guaranteed even today. Option B's real change is that two *trades on
different tickers* could now complete scoring out of order relative to each other
(previously guaranteed in-order by strict serial processing) — same-ticker ordering
is preserved by construction if the per-ticker resolve+score path stays sequential
per ticker (a natural constraint to add: bound concurrency by *ticker*, not just by
count, so two prints on the same off-list ticker in-flight together can't race each
other's cache write). The `opened_since=now` stale-price guard (`services/exits/
exit_engine.py:232-233` — `strategy_engine.py`'s `check_exits` is a thin passthrough
to it, confirmed on adversarial review; not the guard's own home) is a real,
adjacent precedent, but its documented purpose is narrower than "an ordering-audit
tool": its own extensive docstring (`exit_engine.py:160-179`) describes it as
protection against a specific same-tick, poll-order staleness bug (a 2026-08-11
incident where a just-opened position's `entry_price` could be newer than the
tick's own already-fetched `latest_prices` snapshot) — not a general tool for
auditing cross-ticker ordering assumptions across the WS stream's downstream
consumers. It's worth pointing to as the closest existing precedent for
"this codebase already reasons carefully about stream-ordering hazards," but
auditing whether any downstream consumer assumes trade-before-ticker ordering
across *different* tickers under Option B's concurrency is still real, undone work
for the implementation stage, not something this guard already covers.

**Benchmark (analytical — no code was run; this is arithmetic against measured
parameters, not a synthetic benchmark like PR #526's).** At a bounded concurrency of
N=4 (matching `_scoring_pool`'s existing precedent) against resolve calls costing
1.4s at the median / 3.1s at p90 (§1.2), the consumer's own per-item cost drops to
the prescan-gate cost alone (sub-millisecond, unmeasured directly here but bounded by
`resolve.window_avg_ms`'s 0.59ms median, which already includes the rare resolve-triggering
prints averaged in) — the resolve latency is moved entirely off the consumer's
critical path. Queue depth should then track inbound message rate rather than being
gated by resolve latency, which is the actual mechanism #541's drops depend on (§1,
and #541's own reconnect-free-window falsifier result). N=4 concurrent resolves
against the shared 8 req/s burst-8 token bucket (`http_client.py:323`, shared with 8
other classes) claims up to half the burst capacity at once during a spike — this
interacts with Option A below and is not free; sized conservatively here, not
validated against a real burst.

**Failure behavior.** A resolve call that errors or times out today marks the ticker
into `_resolve_failed_tickers` and re-enqueues its trades via `candidate_retry`
(`kalshi_trade_tape.py:456-465`, existing code, unaffected by this option). Under
concurrency, the same failure handling applies per in-flight task — no new failure
mode beyond the shared-state race already flagged above. A pathological burst (many
distinct off-list tickers appearing near-simultaneously) is bounded by the semaphore,
not unbounded task fan-out — extra work queues behind the bound rather than spawning
unbounded connections, the same "bounded and observable, not eliminating the
underlying issue" philosophy `_scoring_pool`'s own docstring states for its own
precedent.

**Complexity.** Real, not trivial: a new pool/semaphore primitive, a fix to the
shared-mutable-state bug above, and a decision about per-ticker sequencing (to
preserve same-ticker cache-write safety). Moderate — comparable in shape to
`_scoring_pool`'s own existing implementation, which this codebase has already built
and run in production for the sync-scoring side.

## 4. Option A — coalesce/batch pending resolve requests across a short window

**Mechanism.** Instead of resolving inline per message, split `_resolve_unknown_markets`
into its already-free prescan-gate stage (unchanged) and a decoupled resolve stage:
tickers that clear the gate and aren't cached get added to a pending set, flushed
either on a short timer (e.g. 100-250ms) or when it reaches the existing
`_MAX_ONDEMAND_MARKET_FETCH` cap (100, `kalshi_trade_tape.py:82`), issuing one
`get_markets_by_tickers()` call for every distinct ticker accumulated in that window
— restoring the batching the original 2026-08-17 design (`3c3ff67`) assumed would
happen, and which has never actually happened on the streaming path (§1, verified
1:1 live).

There is a live, closely analogous precedent in this exact file for "coalesce
multiple pending updates for the same key" — `_coalesce_ticker` (§1.1 above). It is
*not* directly reusable, and the distinction matters: `_coalesce_ticker` is
correct specifically because a ticker update is safely supersedable level-state (an
older price is simply stale once a newer one arrives). A trade print is not — Option
A must batch the *resolve requests* multiple distinct trades generate, never merge
or drop the trades themselves. Every trade that triggers a resolve still needs its
own scoring pass and its own possible signal, after its ticker resolves.

**Correctness.** Same completeness constraint as above, satisfied by construction —
no trade is ever dropped, only the REST call frequency changes. Two real
complications: (1) a print now waits for its batch window to close before it can be
scored — a new, deliberate, small (~100-250ms) latency floor on signal emission for
resolve-triggering prints, worth naming explicitly since CLAUDE.md's data-plane rule
treats timeliness as a first-class property, not free to trade away silently; (2) as
written, batching a resolve **does not by itself free the consumer** — if the
consumer still `await`s the batch flush inline (waiting up to the timer interval, or
until the cap is hit), it is blocked just as before, only less often. Delivering
Option A's consumer-unblocking benefit requires the same decoupling Option B
introduces (hand the batch off, keep draining the queue). **This is the key finding
for Option A: it is not an alternative to Option B, it is a refinement layered on
top of it** — batching reduces the *number* of resolve calls a burst produces;
decoupling (B) is what keeps the consumer free regardless of how many calls are in
flight. Presented alone, without B's decoupling, Option A only reduces call *count*,
not blocking.

**Benchmark (analytical).** At the measured steady-state resolve-triggering rate —
530 `whale_sized_offlist` events over 525s of generation uptime ≈ 1.01/s
(`GET /api/health/pipeline` at `2026-09-03T21:29:10Z` against the generation that
started `21:20:25Z`, consistent with §1's independently-measured ~1/s from the
6-sample `offlist_candidates` window) — a 100-250ms batch window typically
accumulates 0.1-0.25 distinct tickers — often
zero or one, meaning **under normal load batching amortizes almost nothing**: most
windows have at most one call to make regardless. The real payoff is
counter-cyclical: during the exact kind of backlog #541 measured (queue pinned near
the 20,000 cap for 33 minutes), pending resolve-triggering prints pile up *because*
the consumer is behind, and a batch window sampled during that pile-up would catch
many distinct tickers in one call instead of one-at-a-time — the worse the incident,
the more this specific option helps, which is a genuinely useful property, but only
once paired with B's decoupling (without it, the pile-up is a consumer-side backlog
regardless of how the eventual call is shaped, since the *first* call in the burst
still blocks the batch-window `await` the same way it blocks today).

**Failure behavior.** A batch that fails (network/rate-limit error) now needs
`candidate_retry.enqueue` fan-out across every trade whose ticker was in that batch,
not just one — same code shape as today's `truncated` handling
(`kalshi_trade_tape.py:434-448`) generalizes, not a new mechanism, but touches more
trades per failure than today's per-print granularity.

**Complexity.** Meaningful: a new pending-set/timer/flush primitive, plus (per the
finding above) needs Option B's decoupling to deliver its real benefit — so in
practice this is "B, plus a batching layer on B's hand-off path," not a standalone,
smaller alternative to B.

## 5. Option C — reserved/separate rate-limiter allocation for `critical_whale`

**Mechanism.** `_kalshi_read_limiter` (`http_client.py:327`) is one shared
`_TokenBucketRateLimiter` (8 req/s, burst 8, `http_client.py:323-325`) across all
nine `CALLER_CLASSES` (`http_client.py:36-50`): `critical_whale`,
`critical_position`, `interactive`, `background_discovery`, `background_catalog`,
`background_live_status`, `background_resolution`, `background_index_backfill`,
`other`. Split it — reserve a sub-budget for `critical_whale` (and arguably
`critical_position`, both already named "critical" and both on request-serving hot
paths) separate from the five `background_*` classes, so a busy `background_catalog`
burst can no longer make a `critical_whale`
call wait behind it. In one measured ~9-minute window, `critical_whale` (87 calls)
and `background_catalog` (84 calls) were comparable in volume — real, current
contention, not a hypothetical.

**Correctness.** Lowest risk of the three — touches only limiter wiring, not
`kalshi_trade_tape.py`'s call shape, not trade/ticker ordering, not any shared
mutable provider state. No new failure mode beyond "a class with its own smaller
bucket can itself be rate-limited sooner in a pure-`critical_whale` burst," which is
a tuning question, not a correctness one.

**Benchmark (analytical).** Removing limiter/backoff contention from the resolve
latency distribution would move the *median* cost toward `critical_whale.network.window_avg_ms`'s
measured 78.79-290.02ms (two different windows measured; both far below the current
1.4s resolve-call median) — a real, roughly 5-18x improvement to the common case
(1421.35/290.02 ≈ 4.9x at the low end, 1421.35/78.79 ≈ 18.0x at the high end).
But it does **not** touch the tail: `critical_whale.network.window_max_ms` measured
up to 44,580ms over 24h, pure Kalshi-side response latency that a local rate-limiter
change cannot affect. The single worst correlated event in the whole incident (the
8,854.6ms resolve spike lined up with the sharpest queue-depth jump, #541's own
comment thread) sits inside that tail, not the median — meaning **Option C alone
would likely have left the worst moment of the actual incident essentially
unchanged**, even though it measurably helps the common case.

**Failure behavior.** Identical to today's — this option changes wait time, not what
happens on an error.

**Complexity.** Smallest of the three. `_TokenBucketRateLimiter` already exists;
this adds a second instance (or a per-class-group allocation) and routes
`classify()`'s existing caller-class dispatch (`http_client.py:37,70`) to the right
bucket. No changes to `kalshi_trade_tape.py`, no concurrency/ordering risk.

## 6. Comparison

| | Option A: batch resolve calls | Option B: bounded-concurrency resolve | Option C: reserved rate budget |
|---|---|---|---|
| **Addresses root mechanism (consumer blocked)?** | Only if paired with B's decoupling — alone, reduces call count, not blocking | **Yes — the only option that does** | No — reduces one delay source (limiter/backoff), leaves network-latency tail and inline-await both intact |
| **Fixes the newly-found ticker-interference (§1.1)?** | Only via B | Yes, directly | No |
| **Completeness risk** | None (no trade dropped, by construction) | None | None |
| **New correctness burden** | Batch-window latency floor (~100-250ms); wider blast radius per failed batch | Must fix `self._resolve_failed_tickers`'s per-call-reset assumption; needs per-ticker concurrency bound. A second, pre-existing hazard (`_seen_trade_ids`/`_seen_order`, issue #546) was found already live today, independent of Option B — real but not this option's own precondition | None |
| **Benefit shape** | Counter-cyclical — near-zero help under normal load, most help exactly when backlog is worst (needs B to realize) | Removes resolve latency from the consumer's critical path entirely, regardless of load | Cuts *median* ~5-18x; tail (up to 44.6s measured) untouched |
| **Complexity** | Moderate, and not standalone (subsumed by B) | Moderate (new pool/semaphore + one real bug to fix first) | Small (limiter wiring only) |

## 7. Permanent detection for recurrence

Per the data-plane HARD RULE, whichever option ships needs a standing detector for
this *mechanism* recurring — not just confidence in today's specific fix. Every
metric this comparison used already exists (`/api/observability/history`); nothing
new needs instrumenting, only alerting on what's already emitted:

- **`trade_stream.ingest.queue_depth` sustained above ~80% of capacity for ≥5
  minutes with `trade_stream.ingest.reconnects` flat over the same window.** This is
  exactly the manual falsifier #541's own comment thread used to distinguish
  "resolve-blocking" from "restart resubscribe surge" — automating it as a standing
  fault-log/alerting check (`services/alerting/alerting.py` already exists as the
  home for this class of check) would have surfaced this incident without needing an
  ad hoc investigation.
- **`whale_pipeline.stage.resolve.window_max_ms` (or `handler.trade.window_max_ms`)
  exceeding ~2s (the p90 band established in §1.2) for multiple consecutive sample
  windows.**

Naming these here, not designing their exact implementation — that's an
implementation-plan-stage decision, same as the fix itself.

## 8. Recommendation (for consolidation to accept, adjust, or reject — not a decision)

**Option B is the only option that addresses the root mechanism**, and its analysis
surfaced a real, previously-unknown bug (`self._resolve_failed_tickers`'s per-call
reset) that has to be fixed as part of implementing it, not discovered by the next
incident. Adversarial review went further and found a **second, more serious
hazard in the same area — `self._seen_trade_ids`/`self._seen_order` are already
exposed to concurrent mutation today, independent of Option B** (issue #546): the
WS stream consumer and the candidate-retry loop already share `_scoring_pool`, and
`_scoring_pool.py`'s own docstring says that overlap is deliberate, directly
contradicting `_process_trades_sync`'s safety-claim docstring. This is real,
currently-latent, and needs its own fix regardless of whether Option B is ever
built — it must not be scoped only as an Option B precondition. **Option A is not a
real alternative to B — it is a refinement that only
delivers its own benefit once layered on B's decoupling**, and its benefit is
specifically counter-cyclical (most valuable exactly during the kind of backlog
#541 measured), which is a genuine reason to include it, just not as a standalone
choice. **Option C is a valid, low-risk complement** — cheap, no correctness burden,
real median-latency improvement — but insufficient alone, since it leaves the
measured 44.6-second network tail and the inline-`await` mechanism itself untouched;
it would not have prevented the worst moment of the actual incident.

Recommended shape for the eventual design/spec stage (not decided here): **B, with
A's batching folded into B's hand-off path as one implementation detail rather than
a separate mechanism, optionally paired with C** for the median-latency win on top.
This is a recommendation for consolidation to weigh, not authorization to build it —
per CLAUDE.md's "nothing advances on one pass," this document gets its own
self-review, adversarial review, and consolidation before any design/spec or
implementation-plan stage begins, and the coordinator is the one who decides whether
this recommendation stands.

## Addendum — a live episode reproduced Option C's contention finding while this
document was under review

A real episode at 2026-09-03 ~21:40-21:44Z, reported by two peer sessions
(autotrade-96 and the coordinator, autotrade-ce) while this document was in its
self-review/adversarial-review cycle — not independently re-derived here, cited
with each figure's own source per the "never guess" HARD RULE:

**From autotrade-96** (`/api/health/pipeline` polls, relayed by the coordinator):
`last_tick_duration_sec` 5.35s → 23.07s (two consecutive polls ~15s apart) → 5.47s
(recovered by 21:44:28Z); `queue_depth` climbed to 16,865/20,000 (84%) mid-episode,
17,009/20,000 (85%) at 21:44:28Z; `stale_over_300s_count` 19→10-11 of 23 open
positions; `open_fds` 251/1024, up from an 89-92 baseline.

**From autotrade-ce** (directly measured, shortly after 96's 21:44:28Z read):
`last_tick_duration_sec` 5.47s (confirming recovery); `queue.depth` 11,931/20,000,
draining from 96's 17,009; `queue_wait` window: count 12,779, avg 83.68s, max
92.17s, with **12,760 of 12,779 sampled waits in the `>10s` bucket**; `handler_time_
by_class.trade` window avg 2.956ms/max 900.99ms, **lifetime max 10,248.77ms**;
`handler_timeouts_by_class.trade` = 2; `connection.reconnects` = 0 for the episode
— a clean, independent, live reconnect-free confirmation of the same shape §1's
falsifier test established from historical data; `schedulers`: `catalog_scan`
busy (started 0.9s prior), `milestone_scan` last started 232.2s prior — concurrent
background REST scheduler activity at the same moment, matching what both peer
sessions independently noticed in the logs.

This is exactly Option C's finding-3 shape (§5: `critical_whale` sharing the
8 req/s bucket with `background_catalog` and other classes) occurring live, not
only in the 24h historical window this document otherwise analyzes — real-time
corroboration that the contention this document describes is an ongoing condition,
not a one-off artifact of the original incident window. It does not change any
conclusion above: still consistent with Option C being a valid, insufficient-alone
complement (the episode recovered via drain, same pattern as the historical
episodes), and still consistent with Option B being the only option that removes
the mechanism rather than reducing its frequency. `handler_time_by_class.trade`'s
10,248.77ms lifetime max sits close to figures cited elsewhere in this document's
own analysis of the *ticker* handler's historical cost (a different metric,
`handler.ticker.*`, from the #526/Gate 2 investigation) — worth checking in the
implementation-plan stage whether that's the same underlying phenomenon or a
coincidence; not resolved here, since it wasn't independently re-derived by this
document's own author, only relayed.

## Appendix — evidence log

- `curl -sk 'https://kalshi-whale-poc.ddev.site:8443/api/observability/summary?hours=0.15'`
  → §1's live 1:1 `offlist_candidates`/`resolve_calls`/`critical_whale.calls`
  (min/max/avg = 67.0/110.0/87.0, all three metrics) confirmation; §5's
  `background_catalog` (84.17) vs `critical_whale` (87.00) comparison
- `curl -sk 'https://kalshi-whale-poc.ddev.site:8443/api/observability/summary'`
  (default `hours=24`) → §1.2's `critical_whale.network.window_avg_ms` (avg 290.02ms)
  and §5's `critical_whale.network.window_max_ms` (avg 742.46ms, **max 44,580.24ms** —
  the tail figure Option C's analysis turns on)
- `curl -sk 'https://kalshi-whale-poc.ddev.site:8443/api/observability/history?metric=whale_pipeline.stage.resolve.window_avg_ms&hours=24&limit=1000'`
  and the same for `window_max_ms` → §1.2's median/p90/max table (n=633 samples each)
- `curl -sk 'https://kalshi-whale-poc.ddev.site:8443/api/health/pipeline'` at
  `2026-09-03T21:29:10Z`, `ingest.provider_stats` field (`prescanned: 73337,
  whale_sized_offlist: 530, markets_resolved: 487, resolve_failures: 0`), read
  against the `21:20:25Z` generation-start timestamp from the `docker logs` pull
  above → §4's 530-events/525s ≈ 1.01/s rate figure
- `docker logs -t ddev-kalshi-whale-poc-fastapi 2>&1 | grep 'WatchFiles detected\|Started server process'`
  → current generation start time (`2026-09-03T21:20:25Z`, PID 90095), used to bound
  the "freshest measured generation" window cited in §4's rate calculation
- `grep -n 'two_consumer_mode' config/settings.yaml` → `72:  two_consumer_mode: true`,
  §1.1's live-config confirmation (not the code comment's stated "default off")
- `grep -n '_CRITICAL_CLASSES\s*=' services/kalshi/websocket.py` →
  `frozenset({"fill", "position", "lifecycle", "control"})`, §1.1
- Direct `Read`/`grep -n` of: `services/whale_stream/whale_stream_handlers.py`
  (`_process_stream_trade`, `_streaming_trade_tape_enabled`),
  `services/whalewatchers/kalshi_trade_tape.py` (`fetch_signals`,
  `_resolve_unknown_markets`, `min_contracts_for`, cache/batch constants),
  `services/kalshi/websocket.py` (`_consume_from`, `_consume_market_from`,
  `_ingest_raw`, `_coalesce_ticker`, `_ensure_split_queues`),
  `services/whalewatchers/_scoring_pool.py` (full file, precedent for Option B),
  `services/http_client.py` (`CALLER_CLASSES`, `_TokenBucketRateLimiter`,
  `_KALSHI_READ_RATE_PER_SEC`/`_KALSHI_READ_BURST`), `main.py:1195-1230`
  (tick-loop `fetch_signals` call site, contrasted with the streaming one) — every
  source claim above is a read of the current file at this branch's base
  (`origin/main` at `080b17e`), not a recollection.
- `git log -1 --format="%ci %s" 3c3ff67` / `e5bf56b`, `git merge-base --is-ancestor
  e5bf56b 3c3ff67` → the commit-ordering proof (already established in #542's own
  comment thread, re-cited here as load-bearing context for §1) that the WS handler's
  single-trade call shape predates the resolve mechanism's design.
- Issues #541, #542 (this session, same investigation) — read in full as the
  starting point; this document does not re-derive their root-cause claims, only
  extends them into a solution comparison.
- Adversarial review (`-adversarial-review.md`, independent Agent-tool pass, no
  memory of authoring this document) — its F6 finding (the pre-existing
  `_seen_trade_ids`/`_seen_order` concurrency hazard) is filed as issue #546; its
  F7-F11 corrections are applied inline above and in the self-review's own
  correction note.
