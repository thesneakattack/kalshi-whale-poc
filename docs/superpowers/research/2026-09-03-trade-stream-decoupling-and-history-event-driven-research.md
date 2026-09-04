# Research: trade-stream/history decoupling and History's move off fixed-interval polling

2026-09-03, ~22:15-23:15 CDT. Requested by the coordinator (autotrade-6c) per David's standing
priority (`docs/next-action.md`, 2026-09-03 ~22:20-22:35 UTC directive): decouple the critical
trade stream entirely from history/diagnostics (no shared consumers, thread pools, or queues),
and move History's modules off fixed-interval polling to on-demand/event-driven. Research stage
only, per CLAUDE.md's "nothing advances on one pass" — this document does not authorize a
design/spec or implementation-plan stage; it answers what genuine decoupling would require given
the *current* code (not the second-pass audit's snapshot from a day ago), and what the audit's
Tier 3 framing (items 25/26) gets right or wrong now that today's fixes have changed the
baseline.

**Everything below is a direct read of current source at this branch's base (`origin/main`,
`git log -1` confirms `46a0baa` at research start), not a recollection of the cited docs' own
claims — every fact is re-verified against the file it's about, per CLAUDE.md's "never guess"
HARD RULE and the Kalshi-integration-authority rule's spirit applied to internal architecture
claims the same way. No code, config, or live-app changes were made.**

## 0. What this document builds on, and does not re-derive

- `docs/superpowers/research/2026-09-03-trade-resolve-consumer-blocking-solution-comparison.md`
  (merged, PR #547) — root-caused the trade-vs-ticker shared-consumer blocking mechanism (#542)
  and compared three fix options. Its Option B recommendation is not re-litigated here.
- `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md` and its own
  predecessor `docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md`
  — named the two-process split (item 25/#17, full mechanism in the first audit's §4.4) and
  "push more state over the dashboard WebSocket" (item 26/#18, first audit's §6.3 item 5) as the
  Tier 3 target shape. Not re-derived; re-verified against current code where it bears on
  whether the baseline changed (§1-§2 below).
- Issue #546 (the `_seen_trade_ids`/`_seen_order` concurrency hazard, found during the solution-
  comparison doc's adversarial review) — its fix status is itself part of what changed the
  baseline; covered in §1.

**The single biggest fact this document adds that neither predecessor had**: **PR #555 (Option
B, bounded-concurrency trade dispatch) merged at `2026-09-04T03:46:30Z`** — nine hours after the
solution-comparison doc's own research window, and after the second-pass audit was written. Both
predecessor documents were current when written; neither could have known this landed. §1
establishes precisely what it changed and what it didn't.

## 1. Baseline re-measurement: what PR #555 actually changed, verified against current source

Read directly: `services/kalshi/websocket.py` (`_consume_market_from`, `_consume_from`,
`_consume_one`, `_dispatch_trade_concurrent`, `_run_trade_item`, `_TickerDispatchGate`,
`_process_item`), `services/whalewatchers/_scoring_pool.py` (full file), `services/tick_executor.py`
(header + every call site via `grep -rn "tick_executor\.run("`), `config/settings.yaml:72`.

### 1.1 The trade-vs-ticker shared-consumer blocking problem is substantially fixed — the sharing itself is not

The solution-comparison doc's §1.1 finding still holds structurally: `two_consumer_mode: true`
(confirmed live, `config/settings.yaml:72`, unchanged) still routes both "trade" and "ticker"
message classes into one shared `market_queue`, drained by one consumer task
(`_consume_market_from`). **What changed**: that consumer no longer `await`s a trade's full
handling — including the multi-second REST resolve that caused the original blocking — before
reaching the next queue item. `_consume_one` (`websocket.py:1280-1309`) now special-cases the
"trade" class: it hands off to `_dispatch_trade_concurrent`, which acquires one of
`_TRADE_DISPATCH_CONCURRENCY` (= 4) semaphore slots and spawns `_run_trade_item` as a background
task, then returns — the consumer loop is back at `queue.get()` before that trade's resolve call
has even started. Ticker items behind a slow-resolving trade in the queue are no longer held up
by it.

**This means the specific harm the second-pass audit's Tier 3 framing attributed to the shared-
consumer shape — a stalled ticker path delaying `check_exits`/price freshness because a trade
resolve is blocking the same consumer — is now mitigated without a queue split.** The physical
sharing (one `market_queue`, one consumer task) is unchanged; what mattered functionally (the
consumer being unable to *proceed* past a slow trade) is fixed. This is a materially different
starting point than either predecessor document had: the case for splitting the trade/ticker
queues specifically *to fix blocking* is now weaker, because the blocking is already gone by a
cheaper mechanism (bounded concurrent dispatch) that didn't require a queue split at all.

**What is not fixed by this**: same-address-space CPU/scheduling sharing. The trade dispatch
tasks, the ticker consumer loop, and (per §1.2/§1.3 below) the diagnostics-route pool sharing
all still run on the same single event loop in the same single process. Removing an `await`
that blocked *this specific caller* is not the same as removing contention for the loop's own
scheduling turns — a burst of 4 concurrently-dispatched trade tasks doing real CPU work (JSON
parsing, scoring) still competes for the same loop's attention as the ticker consumer's own
iterations, just without the strict serialization that made it a hard block. This is a real,
if smaller, residual coupling — not eliminated, downgraded from "blocking" to "contention."

### 1.2 `_scoring_pool` sharing between WS-trade-scoring and candidate-retry: NOT touched by PR #555, still fully live

`services/whalewatchers/_scoring_pool.py`'s docstring (unchanged by PR #555, re-read directly)
states the shared 4-worker `ThreadPoolExecutor` exists for "**both** the WS-message path,
`_process_stream_trade -> fetch_signals`, **and** the candidate-retry path,
`score_recovered_trade`" — deliberate, sized "headroom for legitimate brief overlap plus the
candidate-retry path." `main.py:777` still runs `_candidate_retry_loop` as its own supervised
task, waking independently and submitting `score_recovered_trade` work to this same pool
(confirmed live: `services/candidate_retry.py:167`'s `provider.score_recovered_trade(...)` call,
unchanged).

**PR #555 fixed the correctness bug in this sharing (issue #546 — the unlocked
`_seen_trade_ids`/`_seen_order` race), by adding a `threading.Lock` around
`_process_trades_sync`'s check-and-mark. It did not un-share the pool.** The resource-contention
concern David's standing priority names ("not sharing... thread pools") is distinct from the
correctness concern #546 named, and only the latter is resolved. This is the single coupling
axis this document found that is **not addressed, even partially, by anything that has landed**
— worth flagging as its own scoped item for the design stage, separate from both the two-process
split (which wouldn't touch it — see §3) and the already-merged consumer fix.

**Issue #546's tracking is stale**: `gh issue view 546` shows `state: OPEN` as of this
document's research window, despite PR #555's body explicitly stating it fixed #546 and the
merged diff confirming the lock. Likely just a missing "Closes #546" keyword in the PR, not a
disputed fix — flagged here rather than silently closed, since closing an issue isn't this
document's job and doing it as a side effect of research would bury the correction where a
reader of #546 wouldn't see the reasoning.

### 1.3 `tick_executor` sharing between trading-critical work and diagnostics routes: confirmed still live, unchanged

The first audit's §4.2 finding (`services/analytics/routes.py` and
`services/whale_calibration/routes.py` calling `tick_executor.run(...)`, sharing the pool with
trading writes) was re-verified directly against current source, not assumed still true:

```
services/analytics/routes.py:127:        population_gates = await tick_executor.run(
services/whale_calibration/routes.py:131:        result = await tick_executor.run(_build_report)
services/whale_calibration/routes.py:168:    result = await tick_executor.run(_build_report)
```

Still live, unchanged. `tick_executor`'s pool (`services/tick_executor.py:80`,
`ThreadPoolExecutor(max_workers=2, ...)`) is the same pool `services/whale_stream/decision_bridge.py:66,129`
routes `candidate_ledger.claim()`/`record_decision()` through — the trading-critical
decision-path gate named in CLAUDE.md's Task 8 framing. **A diagnostic/analytics route request
today can still occupy one of only 2 worker threads that trading-critical claim/record-decision
calls also need.** This is the concrete, currently-live instance of "the critical trade stream
sharing... thread pools... with diagnostics" David's priority names — not a hypothetical, not
something PR #555 touched (different subsystem: WS ingestion vs. HTTP route handlers).

**Correction from self-review**: the Tier 1 de-polling work (§1.5) *did* partially touch this,
just not by removing the sharing — both routes gained a 30s result cache the same day
(`services/analytics/routes.py:42-49`, `services/whale_calibration/routes.py:24-30`, both
commented "2026-09-03, Task 6b of `docs/superpowers/plans/2026-09-03-tier1-backend-hygiene.md`"),
so `tick_executor.run(...)` now only executes on a cache miss — at most once per 30 seconds per
route, deliberately coordinated with the frontend's own 30s History-tab throttle (§1.5)'s
interval, not independently chosen (`analytics/routes.py`'s own comment says so). **The coupling
is still correctness-live** (a cache-miss request still contends for the same 2 threads
trading-critical work needs, and cache-miss timing isn't synchronized with trading-critical
call timing just because the *intervals* match) **but its frequency is now bounded and far
lower than "every diagnostic request,"** which the original (uncorrected) framing above implied.
This is a materially different severity picture than the first audit had when it measured
48.6s/13.6s/4.5s wall times against an *uncached* route — those specific numbers no longer
reflect steady-state cost, only worst-case-on-a-miss cost.

### 1.4 The two-process split (item 25/#17): zero progress, confirmed

`main.py:1507`'s `app = FastAPI(..., lifespan=lifespan)` and
`.ddev/docker-compose.fastapi.yaml:70`'s single `uvicorn main:app` command are unchanged —
still exactly one process, one event loop, running the tick loop, WS ingestion, every
`_scheduler_loop`, and every HTTP route handler (trading-critical and diagnostic alike). The
first audit's §4.4 design (Process A trading-critical + write routes, Process B read-only
diagnostic routes over WAL-mode read connections, nginx path-routing, no new engine/bus) is
unchanged by anything since — nobody has started this.

### 1.5 Tier 1 de-polling (the audit's own sequencing precondition for the split): landed

`frontend/src/js/polling-and-websocket.js:36`: `SYSTEM_HEALTH_REFRESH_MS = 20000` — `/api/quality/summary`
(the audit's worst-measured route, 48.6s wall time) now throttled to a 20s minimum interval,
fetched only when the Terminal tab is active, down from the original 6s-regardless-of-visibility
timer. `frontend/src/js/main.js:81`: `HISTORY_INSIGHTS_REFRESH_MS = 30000` — the History-tab
loaders (candidate-log/summary, confidence-calibration/report) similarly throttled to 30s,
tab-gated. Both landed via PR #500 ("Tier1 backend-hygiene/de-polling," per `docs/next-action.md`'s
merge log), confirmed by direct read of the current frontend source, not the plan doc's own
claim of completion.

**This matters for sequencing**: the first audit's §4.4 explicitly said "do §6's polling fixes
first... fix demand, then decide how much isolation the residual actually justifies." That
precondition is now met. Per the audit's *own* stated logic, the two-process split is next in
line to evaluate — not because this document is recommending it be built (research stage only),
but because the audit's own sequencing rationale for deferring it no longer applies.

### 1.6 The base `/api/state` poll and dashboard WebSocket: unchanged

`frontend/src/js/trading-gate-and-connectivity.js:87`: `refreshIntervalMs = 5000` — still a
flat 5-second timer driving every view's core data (markets, latest_prices, account, broker,
signal/decision feed snapshot, trade tape, portfolio). `services/ws_manager.py`'s
`WebSocketManager.broadcast()` (unchanged since its 2026-08-21 extraction) is called from
exactly one place with live traffic: `services/whale_stream/decision_bridge.py:19-24`'s
`_broadcast_signal_decision`, pushing exactly two message `type`s the frontend handles
(`frontend/src/js/polling-and-websocket.js:174,186`): `signal_decision` and
`trade_stream_status`. Everything else the dashboard shows — prices, trade tape, positions,
account balance, portfolio — is still poll-only. Item 26 (push more state over the WS) is
untouched; the first audit's own "structurally correct end state" recommendation stands exactly
as written.

**One reusable precedent already in production**, worth carrying into the design stage:
`_broadcast_signal_decision` is called via `asyncio.create_task(...)` at all four of its call
sites (`decision_bridge.py:143,160,174,191`), never awaited inline on the decision path — a
slow/stalled dashboard WS client cannot block a trade decision today. Any expansion of what gets
pushed should keep this shape. It does not, by itself, solve the same-event-loop contention
named in §1.1's last paragraph — `create_task` avoids blocking the *specific caller*, not
scheduling contention with everything else on the same loop.

## 2. What "History polls on-demand, not on a timer" would require mechanically

`services/history/` (routes.py, trade_analytics.py, regime_analytics.py, suggestion_decisions.py,
per its own README) has no scheduler of its own — it is pure request-response, read from
`signal_log.db`/`market_history.db`/`PaperBroker.trade_log`. **The fixed-interval polling named
in David's priority is entirely a frontend concern** (the History-tab loaders' 30s timer, §1.5),
not a backend scheduler loop — confirmed by reading `services/history/`'s full contents; there is
no `_scheduler_loop`/`asyncio.sleep`-driven polling anywhere in that package.

Mechanically, "on-demand" instead of "every 30s regardless of whether anything changed" needs:

1. **A signal the frontend can react to.** The dashboard WS connection already exists and is
   already correctly decoupled from the trading-critical path (§1.6's precedent). The natural
   shape: broadcast a lightweight `type: "history_updated"` (or similar) message whenever
   something History-relevant is written — a trade closes (`paper_broker.py`'s `trade_log`
   write), a market resolves (`market_history.py`'s outcome write), a signal is recorded
   (`signal_log.py`). This is new backend wiring, not present today; `grep -rn "ws_manager"`
   across `services/` confirms `decision_bridge.py` is the only producer today.
2. **The frontend fetch becomes push-triggered instead of timer-triggered.** Replace (or
   supplement, for a safety-net floor — the first audit's own item 4 recommendation for a
   different poll makes the same "keep a much-longer-interval poll as a safety net, don't trust
   the push alone" point, and the same reasoning applies here) `HISTORY_INSIGHTS_REFRESH_MS`'s
   unconditional 30s timer with a listener on the new WS message type, still gated to "only
   fetch if the History tab is actually active" (the existing `refreshHistoryInsightsIfActive`
   gate already does this half).
3. **Coalescing, deliberately, not naively.** A busy tick can close several trades and record
   several signals in quick succession; broadcasting one WS message per individual write and
   having the frontend fetch on each would trade a fixed-interval poll for a burst of on-event
   fetches, potentially worse under load. The `_coalesce_ticker` precedent already in
   `services/kalshi/websocket.py` (newest-wins, per-key coalescing over a short window) is a
   directly analogous, already-proven-in-this-codebase pattern for exactly this shape — batch
   "something changed" notifications over a short window (e.g., per-tick, not per-write) rather
   than firing one push per write.
4. **This is the same underlying mechanism as item 26.** Both "push more state over the
   dashboard WS" and "make History event-driven" reduce to the same backend change: emit more/
   different broadcast messages from `ws_manager`, following the existing fire-and-forget
   `create_task` pattern. They should not be scoped or designed as two separate initiatives —
   doing so risks two different broadcast-triggering mechanisms where one, generalized, would
   do. This is a genuine refinement this document adds to the audit's original framing, which
   listed them as separate numbered items (25 vs. 26) without noting the overlap, because at
   the time item 26 was written it was framed around `latest_prices`/trade-tape/account state
   specifically, not History.

**What this does not require**: the two-process split. History-event-push is a broadcast-
trigger change in the write paths that already run in Process A under either topology; it does
not depend on where diagnostic *read* routes eventually live. The two initiatives are
complementary, not sequentially dependent in either direction — see §3.

## 3. Does the two-process split (item 25) address any of §1's findings? Precisely, not by assumption

Checked against the first audit's own route list (§4.4: Process B = `/api/quality/*`,
`/api/diagnostics*`, `/api/candidate-log/*`, `/api/confidence-calibration/report`,
`/api/regime/*`, `/api/backtest/*`, `/api/advisory/status`, `/api/advisory/recommendations`,
`/api/signals/history`, `/api/observability/*`):

- **§1.1 (trade/ticker shared consumer):** No. The split moves HTTP route handlers to a second
  process; it does not touch `services/kalshi/websocket.py`'s WS ingestion, which stays in
  Process A regardless. Already mitigated by PR #555 anyway (§1.1).
- **§1.2 (`_scoring_pool` shared between WS-trade and candidate-retry):** No. Both the WS
  consumer and `_candidate_retry_loop` stay in Process A under the first audit's own route
  split — neither is a diagnostic HTTP route. **This is the important negative finding**: the
  two-process split, as designed, does not address the `_scoring_pool` sharing at all. If that
  sharing is meant to be eliminated (David's priority names it explicitly, citing #546), it
  needs its own, smaller fix — most naturally, giving `_candidate_retry_loop` its own pool
  (the same "give this workload its own small bounded pool" pattern `_scoring_pool.py` itself
  was created to establish, one level more granular) — independent of whether/when the process
  split happens.
- **§1.3 (`tick_executor` shared between trading writes and diagnostic routes):** **Yes,
  directly** — `/api/candidate-log/*` and `/api/confidence-calibration/report` (both currently
  routed through `tick_executor.run`, §1.3) are explicitly named in the first audit's own
  Process B route list. This is the one coupling axis the split, as already designed, actually
  fixes. Confirms the split's continued relevance — but the *reason* it matters has narrowed
  from "trade-vs-ticker queue contention" (largely mitigated) to specifically "diagnostic-route
  pool contention" (still fully live, unmitigated by anything else).

## 4. What the Tier 3 framing gets right and wrong, now that the baseline has moved

**Gets right, unchanged:**
- The two-process split is still the correct target shape for isolating diagnostic HTTP routes
  from trading-critical writes — §3 confirms it against current source, not just the audit's
  original reasoning.
- "Push more state over the dashboard WS" (item 26) is still entirely undone and still valuable
  — §1.6 confirms the baseline is unchanged since the audit was written.
- The sequencing logic (de-poll first, then evaluate the split) was sound and its precondition
  is now satisfied (§1.5) — the split is unblocked by the audit's own criteria, not because this
  document is recommending starting it now.

**Needs updating:**
- **The urgency argument tied to trade/ticker queue contention is weaker than either
  predecessor document assumed**, because PR #555 fixed the actual harm (blocking) without a
  queue split, landing after both documents were written. The split's remaining justification
  is narrower and more specific than "decouple trade-critical from history broadly" — it is
  concretely "stop `/api/candidate-log/summary` and `/api/confidence-calibration/report` from
  contending with `candidate_ledger.claim()`/`record_decision()` for `tick_executor`'s 2
  worker threads," which is real and current (§1.3) but a narrower claim than the framing
  implies.
- **Item 25 does not cover everything David's priority names.** The priority says "not sharing
  consumers, thread pools, or queues" — the process split addresses the `tick_executor`/
  diagnostic-route thread-pool sharing (§1.3) but not the `_scoring_pool`/candidate-retry
  sharing (§1.2), which needs a separate, smaller fix. Treating "do item 25" as satisfying the
  full priority would leave §1.2 unaddressed and undocumented as still-open.
- **Items 25 and 26 are more entangled with each other, and with History's polling problem,
  than the audit's numbering suggests.** §2.4 argues History-on-demand and "push more state"
  are the same mechanism; neither depends on the process split (§2, last paragraph) — so
  there's no forced ordering between 25 and 26/History-on-demand. They can proceed in either
  order or in parallel, which the original Tier 3 list (a flat ordered list, 25 before 26)
  doesn't make explicit.

## 5. Constraints for the eventual design/spec stage (not decided here)

- **Completeness (CLAUDE.md's data-plane HARD RULE)**: none of the observations above propose
  dropping, coalescing, or delaying a trade — §2's coalescing point is about *notification*
  events for History, never about the underlying trade/signal/decision data itself, which is
  never coalesced or dropped anywhere in this document's scope.
- **Exchange-wide hot path measurement**: any change to `services/kalshi/websocket.py` or
  `_scoring_pool.py` (§1.1, §1.2) is on the hot path CLAUDE.md requires runtime-cost measurement
  for before shipping — not scoped or measured here, a design/implementation-stage obligation.
- **The `_scoring_pool` split (§1.2, §3) is the one finding here closest to "ready to scope
  directly"** — smaller, more self-contained than the process split, with a directly-analogous
  precedent already built and running (`_scoring_pool.py` itself). Worth a design/spec pass on
  its own rather than folding it into the larger two-process-split initiative, per §3's finding
  that the split wouldn't cover it anyway.
- **Not itself a decision**: whether to sequence the process split, the `_scoring_pool` split,
  and History/dashboard-WS event-push in parallel or in series is a design-stage question this
  document surfaces evidence for (§3, §4) but does not resolve.

## Appendix — evidence log

- `git log -1` at research start (`46a0baa`, this branch's base at `origin/main`) — the baseline
  every claim below is checked against.
- `gh pr view 555 --json title,state,body,mergedAt` → Option B merged `2026-09-04T03:46:30Z`,
  full body read for scope/review-status claims (§0, §1.1, §1.2).
- `gh issue view 546 --json title,state,body` → confirmed `state: OPEN` despite PR #555's fix
  (§1.2's tracking-hygiene note).
- Direct `Read`/`grep -n` of: `services/kalshi/websocket.py` (`_consume_market_from`,
  `_consume_from`, `_consume_one`, `_dispatch_trade_concurrent`, `_run_trade_item`,
  `_TickerDispatchGate`, `_two_consumer_mode`, `_ingest_raw`) — §1.1.
- `services/whalewatchers/_scoring_pool.py` (full file) — §1.2.
- `grep -rn "tick_executor\.run("` across `services/` and `main.py` — full call-site census,
  §1.3.
- `services/tick_executor.py:80` (`ThreadPoolExecutor(max_workers=2, ...)`) — §1.3.
- `config/settings.yaml:72` (`two_consumer_mode: true`) — §1.1, re-confirmed live not assumed
  carried over from the solution-comparison doc.
- `main.py:1507`, `.ddev/docker-compose.fastapi.yaml:70` — §1.4, single-process confirmation.
- `frontend/src/js/polling-and-websocket.js` (full read: `SYSTEM_HEALTH_REFRESH_MS`,
  `connectWebSocket`, message-type handling, `_broadcast_signal_decision` call-site shape),
  `frontend/src/js/main.js` (`HISTORY_INSIGHTS_REFRESH_MS`), `frontend/src/js/
  trading-gate-and-connectivity.js` (`refreshIntervalMs`) — §1.5, §1.6.
- `services/ws_manager.py` (full file) and `services/whale_stream/decision_bridge.py:1-24,143,160,174,191`
  — §1.6's `create_task` fire-and-forget precedent.
- `services/history/README.md`, `ls services/history/`, `grep -rn "_scheduler_loop\|asyncio.sleep"
  services/history/*.py` (zero hits) — §2, confirming History has no backend scheduler of its
  own.
- `main.py:777`, `services/candidate_retry.py:167` — §1.2's live call-graph confirmation.
- `docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md` §4.4
  (two-process split full design) and §6.3 item 5 (push more over dashboard WS) — read in full,
  cited, not re-derived.
- `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md` §6.5, §6.6, §8 (Tier
  3 items 25/26) — read in full, cited, not re-derived.
- `docs/superpowers/research/2026-09-03-trade-resolve-consumer-blocking-solution-comparison.md`
  — read in full, its §1.1 finding (two_consumer_mode/shared consumer) is the starting point
  for §1.1 above, re-verified against current (post-#555) source rather than assumed still
  accurate.
- `ls docs/superpowers/research/ docs/superpowers/specs/ docs/superpowers/plans/` +
  `grep -n` over `ROADMAP.md` for prior art on this exact topic → none found, confirming this
  is not duplicate work.
