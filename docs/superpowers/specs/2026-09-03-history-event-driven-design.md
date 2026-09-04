# History Event-Driven Design

## Status

Design document (2026-09-03). Builds directly on
`docs/superpowers/research/2026-09-03-trade-stream-decoupling-and-history-event-driven-research.md`
(§2, §3, §4's Tier-3-related findings) and
`docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md` §6.6/§8 (item 26,
"push more state over the dashboard WebSocket") and its predecessor
`docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md` §13
item 5. Per CLAUDE.md's "nothing advances on one pass" HARD RULE this is the design stage
only: self-review is appended below; a separate adversarial review (a fresh Agent call with
no memory of this session) and consolidation still need to run before an implementation
plan is written. No code, config, or live-app changes were made producing this document.

**Correction to how this task was framed, verified via `gh pr view 562`, not assumed:** the
trade-stream/history research document this design was told to build on as "merged to
`origin/main`" is **not merged** — it is `docs/trade-stream-decoupling-research`, PR #562,
still `OPEN` at the time of writing. Its content was read directly (`git show` against the
branch tip, `4f276e1`) since the branch itself is real and the document's own review cycle
(self-review + adversarial review + consolidation, all three artifacts present in
`docs/superpowers/research/`) already completed with a GO verdict — so treating its content
as reliable input to this design is consistent with CLAUDE.md's rigor requirement even
though the merge itself hasn't happened. This does **not** relax anything: if PR #562 changes
before merging, or doesn't merge, this design's citations of it should be re-checked against
whatever actually lands on `main`. Flagged explicitly rather than silently treated as
already-integrated history.

## Goal

Replace the History tab's fixed-interval frontend polling (currently: an unconditional
30-second re-fetch of ~10 endpoints while the tab is open, regardless of whether anything
changed) with a push-triggered mechanism: the backend notifies connected dashboards only
when a real write has happened to data the History tab shows, and the frontend re-fetches
only in response to that notification, plus a much-longer safety-net poll to bound
staleness if a notification is ever missed.

## Non-goals

- **The two-process split (Tier 3 item 25 / audit item #17).** The research doc's §3 finding
  stands: it doesn't cover the `_scoring_pool` sharing and its own remaining justification is
  narrower (the `tick_executor`/diagnostic-route axis, gated on issue #410's still-unmet
  measurement precondition) than "decouple trade-critical from history broadly." This design
  doesn't depend on the split landing first or at all — confirmed in §3 below.
- **The `_scoring_pool` split** (WS-trade-scoring vs. candidate-retry sharing a 4-worker
  pool) — the research doc's own recommendation (§5) is that this is its own, smaller,
  more self-contained design. Out of scope here.
- **Issue #410** (whether/how to stop `/api/candidate-log/summary` and
  `/api/confidence-calibration/report` from sharing `tick_executor`'s 2 worker threads with
  trading-critical writes) — this design's push mechanism changes how *often* those two
  routes get hit (see §6), which is relevant context for whoever eventually measures #410,
  but does not resolve the sharing itself.
- **The Whale-tab loaders** (`/api/signals/history`, `/api/signals/clusters`,
  `loadSignalHistory()`/`loadSignalClusters()` in `frontend/src/js/whale-watch.js`) — same
  "fetch once on tab-open, no periodic refresh at all today" shape as History's
  `loadTradingHistory()`, and a plausible future candidate for the same treatment, but not
  named in the task and not designed here.
- **`services/history/routes.py`'s `/api/market-history/summary` and
  `/api/market-history/hypothetical-trades`** — confirmed via `grep -rn "market-history"
  frontend/src/js/*.js` to have zero frontend callers today. Nothing polls them, so there is
  no polling behavior to convert; out of scope.
- **`/api/state`'s own 5s poll, `SYSTEM_HEALTH_REFRESH_MS` (20s Terminal-tab throttle), or
  `state["generation"]`/`bump_generation()`** — untouched. §4.1 explains why History needs
  its own, separately-scoped counter rather than reusing `bump_generation()`.
- Zero code changes in this document — design only.

---

## 1. Current mechanism, verified directly against source

### 1.1 Server-side WebSocket (`services/ws_manager.py`, `main.py:1752-1759`)

`GET /api/ws` (`main.py:1752`) accepts a connection, registers it in
`WebSocketManager.active_connections`, and then only ever `await`s
`websocket.receive_text()` in a loop to detect disconnects — it never reads a meaningful
inbound message. **This connection is server→client broadcast-only in practice**; nothing
trading-critical waits on anything a client sends over it.

`WebSocketManager.broadcast()` (`services/ws_manager.py:29-37`) takes a snapshot of the
connection list under a lock, releases the lock, then sequentially `await`s
`connection.send_text(text)` per connection, with **no per-send timeout**. Today it carries
exactly two message `type`s, from two producers, both already using the same
fire-and-forget shape:

- `signal_decision` — `services/whale_stream/decision_bridge.py`'s `_broadcast_signal_decision`
  (`:19-24`), dispatched via `asyncio.create_task(...)` at four call sites (`:143, 160, 174,
  191`), all inside `async def` functions running on the event loop.
- `trade_stream_status` — `services/whale_stream/whale_stream_handlers.py`'s
  `_handle_trade_stream_status` (`:690-704`), dispatched the same way at its one call site
  (`:700`).

Both are `asyncio.create_task(...)`, never awaited inline by their callers — a slow/stalled
dashboard client cannot block a trade decision or a stream-status update. This is a real,
already-proven-in-production precedent, not a new pattern this design invents.

### 1.2 Client-side polling — the History tab specifically

`services/history/` has **zero scheduler of its own** (confirmed:
`grep -rn "_scheduler_loop\|asyncio.sleep" services/history/*.py` — no hits). Every fetch
below is frontend-driven.

Two distinct drivers, both in `frontend/src/js/`:

1. **`loadTradingHistory()`** (`history-core.js:14-42`) — fires once, unconditionally, only
   when the History tab is opened (`main.js:46`, `showView('history')`). Fetches
   `/api/trading-history`, then chains 10 more loaders (`loadAdvisory`,
   `loadDeclinedSuggestions`, `loadMarketAnalyst`, `loadSeriesEvaluator`,
   `loadChangeHistory`, `loadCalibrationReport`, `loadCalibrationHistory`,
   `loadRegimeSegmentation`, `loadCandidateLogSummary`, `loadBacktestSweeps`). Not on a
   timer — this part is already event-driven (the "event" is a click).
2. **`refreshHistoryInsightsIfActive()`** (`main.js:81-99`) — called unconditionally at the
   end of every `refresh()` (`polling-and-websocket.js:153`), which itself runs on
   `/api/state`'s own poll cadence (`refreshIntervalMs`, tied to `kalshi.poll_interval_sec`,
   default well under 30s). Internally self-throttled to `HISTORY_INSIGHTS_REFRESH_MS =
   30000` via a `_lastHistoryInsightsRefreshAt` timestamp check, and gated on
   `currentView === 'history'`. When it fires, it re-runs 10 of the same loaders (all of
   (1)'s chain except `loadTradingHistory`/`loadMarketAnalyst`'s own trigger... actually the
   same set minus `loadTradingHistory` itself, since that one only reloads the trade table,
   which stays on its own explicit pagination and is deliberately *not* torn down by the
   periodic refresh per the file's own 2026-08-23 comment).

**This is the fixed-interval polling David's priority names**: a blind, unconditional
30-second re-fetch of ~10 endpoints, coupled — by accident of where the call currently sits,
per `main.js`'s own comment history on this exact function — to `/api/state`'s poll firing at
all, not to whether anything History-relevant actually changed. This is the same "moved the
caller, not the design" lesson `main.py`'s own `_scheduler_loop` extraction (P8 Task 36)
already applied on the backend side for its five `_maybe_*` triggers — worth carrying over
symmetrically to the frontend (§4.3).

---

## 2. What "new" means per endpoint — real event vs. periodic recompute vs. click-triggered

Every endpoint below was traced to its actual data source, not assumed from its name.

| Loader / endpoint(s) | Reads from | Real trigger event | Classification |
|---|---|---|---|
| `loadTradingHistory` — `/api/trading-history` | `PaperBroker.trade_log` | trade close (`close_position`) / open (`open_position`) | **event-driven** |
| `loadAdvisory` — `/api/advisory/status`, `/api/advisory/recommendations` | `broker.trade_log` via `trade_analytics.build_trade_history()` (`services/advisory/routes.py:48`) | trade close/open | **event-driven** |
| `loadRegimeSegmentation` — `/api/regime/*` | same `build_trade_history()` rows, bucketed | trade close/open | **event-driven** |
| `loadBacktestSweeps` — `/api/backtest/*` | `signal_log.resolved_signals_with_factors()` / `resolved_signals_with_series()` (`services/backtest/routes.py:21,29-30`) | **signal resolution** (`signal_log.mark_resolved`), not trade close | **event-driven** |
| `loadCalibrationReport` — `/api/confidence-calibration/status`, `/report` | computed live from `signal_log.resolved_signals_with_factors()` (same as backtest) | signal resolution | **event-driven** |
| `loadCandidateLogSummary` — `/api/candidate-log/summary` | `candidate_ledger`/`candidate_log` | candidate decision (`candidate_ledger.record_decision`) | **event-driven**, but see §6 — this route already carries its own independent 30s cache (Task 6b), which caps effective freshness regardless of push latency |
| `loadCalibrationHistory` — `/api/confidence-calibration/history` | `calibration_history.history()` — periodic snapshots | `calibration_history.record_snapshot()`, fired from `_maybe_run_auto_apply` on an hours-scale cooldown (`snapshot_interval_sec`, default 21600s) | **event-driven, but slow** — a push tied to trade/signal events would fire far more often than this resource actually changes; correctly classified as "periodic re-computation with (usually) nothing new to report" between snapshots |
| `loadChangeHistory` — `/api/advisory/applied-changes` | `config_performance`'s applied-change log | `config_performance.log_applied_change()`, fired either by a manual Apply click (route handler, client already re-fetches after its own POST) or by auto-apply (hours-scale cooldown, `_maybe_run_auto_apply`) | **event-driven for the auto-apply case only** — the manual case needs no push, the client already knows |
| `loadDeclinedSuggestions` — `/api/suggestions/declined` | `suggestion_decisions` store | only ever changes via this same client's own decline/undecline click (`history-core.js:274-317`) | **no push needed** — nothing else writes this |
| `loadMarketAnalyst` — `/api/market-analyst/status`, `/api/market-analyst/analyses` | `market_analyst_agent`'s stored per-market/per-series/full-spectrum analyses | only ever written from that package's own `/analyze`/`/apply` POST routes. Corrected after adversarial review: the naive grep for the write pattern outside `services/market_analyst_agent/` returns one hit (`diagnostics.py:191`), but it's a false positive — an unrelated local dict variable of the same name, not an actual write path. The conclusion (no external writer) holds on direct read of that call site, just not on the grep alone. | **click-triggered, no push needed** |
| `loadSeriesEvaluator` — `/api/series-evaluator/status` | `series_evaluator` | same shape as market analyst — user-initiated evaluation runs | **click-triggered, no push needed** |

One write path considered and **excluded**: `services/research/research.py`'s
`_maybe_run_research` (an evidence-triggered sweep, `_SCHEDULER_TRIGGERS`) persists to
`data/research_reports.db`, but `grep -rn "research_reports\|/api/research"
frontend/src/js/*.js` returns nothing — nothing on the dashboard reads this today. Not a
History-tab trigger because it isn't a History-tab data source at all right now.

**Net finding** (corrected after adversarial review caught the bucket count double-counting
`loadTradingHistory`, which is click-triggered per §1, not one of the 10 periodically-refreshed
loaders this table classifies): **5** of the 10 periodically-refreshed loaders are driven by one
of three real, enumerable write events (trade close/open, signal resolution, candidate
decision); one (`loadCalibrationHistory`) is driven by a much slower fourth event
(`calibration_history.record_snapshot`); one (`loadChangeHistory`) is driven by a mix of a
fifth event (auto-apply's `log_applied_change`) and client-own-action (manual apply); three
(`loadDeclinedSuggestions`, `loadMarketAnalyst`, `loadSeriesEvaluator`) need no push at all —
the client already knows to refresh after its own action. 5+1+1+3 = 10. **None of the 10 is pure
"recompute for the sake of recomputing with nothing possibly new"** — every one traces to a
real, identifiable write, which is the precondition for event-driven refresh actually being
correct rather than cosmetic.

---

## 3. The coupling question: same WebSocket connection, or a separate one?

This is the question the task named as the one to get right, so it gets its own section
rather than a one-line answer.

### 3.1 What kind of coupling the initiative is actually worried about

The research doc's findings (§1.1-§1.3) are all about one specific failure shape: **a
bounded consumer or worker pool that trading-critical work depends on** (the shared
`market_queue` consumer, `_scoring_pool`'s 4 threads, `tick_executor`'s 2 threads) **being
occupied by non-trading-critical work, so a trading-critical caller has to wait its turn.**
That is a real blocking/starvation mechanism — a `tick_executor.run(...)` call from a
diagnostics route and a `tick_executor.run(...)` call from `candidate_ledger.claim()` are
genuinely contending for the same 2 threads.

`ws_manager`'s connection registry is a structurally different shape: it is a **fan-out,
fire-and-forget broadcast to browsers**, not a bounded worker pool anything waits on. Nothing
in this app's trading logic ever awaits a WS `send()` completing, reads a WS message as an
input to a decision, or blocks on `ws_manager.broadcast()` finishing. Every existing call
site already uses `asyncio.create_task(...)` specifically so a slow client can't hold up the
caller (§1.1). Pushing more message types down the same connection adds more independent
fire-and-forget tasks to the event loop's ready queue — it does not create a new place
trading-critical code can be made to wait.

**Conclusion: reuse the existing `ws_manager`/`/api/ws` connection.** This is also literally
what the audit's own item 26 named ("push more state over the *existing* dashboard
WebSocket" — `docs/superpowers/research/2026-09-02-architecture-audit-and-rewrite-considerations.md:846`),
not a new connection — and the research doc's §2 point 4 explicitly merges History-push into
that same mechanism rather than treating it as a separate initiative.

### 3.2 The one real (identified, not measured) risk this does raise

`ws_manager.broadcast()`'s sequential per-connection `send_text()` has **no timeout**
(confirmed: full read of `services/ws_manager.py`, no `asyncio.wait_for` anywhere in it).
If one browser's TCP connection stalls (backgrounded mobile tab, sleeping laptop, dead WiFi),
that one `broadcast()` task can sit `await`ing that one `send_text()` indefinitely — not
blocking anything else on the loop (it's its own task), but a growing pile of never-completing
tasks under repeated broadcasts is a latent resource-leak shape. **This is a pre-existing
gap, not something this design introduces** — but History-push meaningfully raises this
channel's steady-state message rate (from "occasional decision-feed events + connectivity
flips" to "roughly one message every coalescing window during active trading," §4.2), which
raises exposure to it. Recommend a bounded per-send timeout (`asyncio.wait_for(connection.send_text(text),
timeout=...)`, treating a timeout the same as the existing `except Exception` disconnect path)
as a **companion hardening item**, not a blocker — this is not itself an argument for a
separate connection (a second connection would have the identical gap, just on a second
registry).

### 3.3 Why a separate connection is rejected, not just "not chosen"

A new `/api/ws/history` endpoint with its own `WebSocketManager` instance was considered.
Rejected because:

- It doesn't fix anything measured or even identified above — §3.1's coupling concern
  doesn't apply to this fan-out shape in the first place, so there is no bottleneck for a
  second connection to relieve. Adding one anyway is exactly what the data-plane HARD RULE's
  "never change queue capacity, consumer/connection/thread counts... because it should help;
  identify the measured bottleneck and its mechanism first" warns against.
  changes because it "should help."
- Real cost: a second browser-side WebSocket object with its own open/reconnect/backoff state
  machine (duplicating `connectWebSocket()`'s existing logic, `polling-and-websocket.js:161-204`),
  a second server-side connection registry, and a second thing that can silently be
  disconnected without the user noticing.
- The two-process split (item 25), when/if it lands, moves HTTP *route handlers* to a second
  process — it does not touch `/api/ws` either way (confirmed: the first audit's own §4.4
  Process B route list, cited in the research doc's §3, names only REST diagnostic routes,
  never `/api/ws`). So there's no future-proofing argument for a second connection now
  either; nothing about the split changes this recommendation.

---

## 4. Mechanism design

### 4.1 Why this needs its own counter, not `state["generation"]`/`bump_generation()`

`services/app_state.py`'s `bump_generation()` (`:416-429`) is explicitly scoped, by its own
docstring, to "anything `/api/state` reports" — and confirmed directly: none of
`/api/trading-history`, `/api/advisory/*`, `/api/confidence-calibration/*`,
`/api/regime/*`, `/api/candidate-log/summary`, or `/api/backtest/*` is part of
`/api/state`'s body (`_build_state_body`). Reusing it would either under-fire (a History
write that doesn't happen to also touch `/api/state`'s fields never notifies History
listeners) or over-fire (History listeners get woken by unrelated `/api/state` changes with
nothing new for them) — both are exactly the "a value means exactly what its label says"
violation CLAUDE.md's data-plane rule warns about, just applied to a change-notification
signal instead of a financial figure.

**Also structurally blocked**, not just semantically wrong: `services/app_state.py` already
imports `paper_broker` (`PaperBroker`), `signal_log`, `calibration_history`, and
`config_performance` directly (`app_state.py:49,57,71,83`). If any of those four modules
tried to import `bump_generation` back from `app_state.py` to call it at their own write
point, that is a circular import — verified, not assumed
(`grep -n "^import\|^from" services/paper_broker.py services/signal_log.py
services/candidate_ledger.py services/whale_calibration/calibration_history.py
services/config/config_performance.py`: none of the five currently import `app_state` at
all, and adding it for four of them would cycle back).

### 4.2 A new leaf module: `services/history_push.py`

`services/ws_manager.py` is the one module in this dependency graph with zero app-specific
imports (`asyncio`, `json`, `fastapi.WebSocket` only, confirmed by full read) — anything can
import it without cycle risk. The proposed home is a new, similarly self-contained module,
`services/history_push.py`, importing only `asyncio`, `time`, and `services.ws_manager` —
mirroring `ws_manager.py`'s own stated extraction philosophy ("a self-contained utility, not
a per-concern module").

Shape (design-level, not final code):

- One coalesced counter/timer, structurally identical to `bump_generation()`'s pattern
  (`_last_bump_ts` check-then-set), but **independently scoped and independently
  rate-limited** — not the same variable, not the same interval.
- **Coalescing interval: reuse `HISTORY_INSIGHTS_REFRESH_MS` = 30000ms, unchanged.** This is
  a deliberate choice, not laziness: that value is already the product of a real, measured
  fix (Tier 1's 504-storm finding, `main.js:72-80`) and is already coordinated with the 30s
  server-side cache TTL on the `tick_executor`-routed endpoints (`analytics/routes.py:42-49`,
  `whale_calibration/routes.py:24-30`, Task 6b). Shrinking it here, without a *new*
  measurement, would be exactly the "change polling/cache-adjacent frequency because it
  should help" the data-plane HARD RULE forbids — the value-add of this design is "fetch
  only when something changed," not "fetch faster than before" (§4.4 spells out precisely
  what latency this does and doesn't improve).
- One function, e.g. `mark_history_changed()`, callable from any thread (see §4.3) — on each
  call, if more than `30s` has elapsed since the last broadcast, `asyncio.create_task(...)`
  (or thread-safe equivalent, §4.3) a `{"type": "history_updated"}` broadcast via
  `ws_manager.broadcast(...)`; otherwise no-op. Leading-edge, same shape as
  `bump_generation()`: an isolated change fires near-immediately; a sustained burst of
  changes degrades to the same ~30s cadence as today's blind timer, but **only while there
  is continuous real activity** — during any idle stretch, zero fetches happen, which is the
  actual defect being fixed (today's timer fires the full 10-endpoint batch every 30 seconds
  regardless of whether the app has done anything).
- No payload beyond the type tag. The frontend does a full-batch re-fetch on receipt (same
  set `refreshHistoryInsightsIfActive()` already fetches) rather than per-resource
  granularity — see §5's comparison for why.

### 4.3 Where to call `mark_history_changed()` — a real threading constraint, verified

This is the second correctness question this design surfaces, not just the WS-reuse one the
task named up front, and it matters: **the write functions this design hooks do not all run
on the event-loop thread.**

Verified call chains:

- `candidate_ledger.claim()` / `record_decision()` run **on a `tick_executor` worker
  thread** — confirmed: `services/whale_stream/decision_bridge.py:66,129` calls both via
  `await tick_executor.run(lambda: ...)`, and `tick_executor.py` wraps work in a
  `ThreadPoolExecutor`. Calling `asyncio.create_task(...)` directly inside
  `candidate_ledger.py`'s own functions would raise `RuntimeError: no running event loop` in
  that thread — this is not hypothetical, it is how `tick_executor.run` is documented to
  work (`services/whalewatchers/_scoring_pool.py`'s own docstring: "used by
  `candidate_ledger.claim()`/`record_decision()`"). The existing `_broadcast_signal_decision`
  precedent already gets this right by *not* calling `create_task` inside
  `candidate_ledger.py` — it calls it in `decision_bridge.py`, after the `await
  tick_executor.run(...)` has returned control to the event loop (`decision_bridge.py:129,143`).
- `signal_log.mark_resolved()` (called from `main.py:400`, inside
  `_check_signal_resolutions_background`) runs on the event loop — that function is
  `async def` and does real `await`s (`client.get_markets_by_tickers(...)`), and is
  scheduled via `task_supervisor.supervise(lambda: _check_signal_resolutions_background(cfg),
  ...)` as a supervised asyncio task, not offloaded to a thread pool.
- `PaperBroker.close_position()` (via `check_exits`) is confirmed event-loop-side at its
  hottest call site (`services/whale_stream/whale_stream_handlers.py:283-287`, "the
  single-threaded event loop" per that file's own comment). Its other three call sites
  (`services/exits/exit_engine.py:410`, `services/exits/position_netting.py:410`,
  `main.py:1897`) were **not individually re-verified in this pass** — main.py's is a FastAPI
  route handler (event-loop by construction); the other two are plausible event-loop callers
  given trading-critical code in this app is deliberately kept off worker threads, but that
  is an assumption, stated as one, not a checked fact.
- `calibration_history.record_snapshot()` and the auto-apply branch's
  `config_performance.log_applied_change()` both run inside `_maybe_run_auto_apply`, called
  synchronously from `_scheduler_loop` (`main.py:548-563`, itself `async def`) — event-loop
  side.
- The manual-apply `config_performance.log_applied_change()` call sites
  (`services/advisory/routes.py:103,121,195`, `services/whale_calibration/routes.py:53,68,180`)
  are all inside `async def` FastAPI route handlers — event-loop side.

**Two viable implementation shapes, not fully resolved here — an implementation-stage
choice**, flagged rather than silently picked:

1. **Call-site enumeration at the confirmed-event-loop orchestration layer** — mirrors the
   existing `_broadcast_signal_decision` precedent exactly (call `mark_history_changed()`
   right where `create_task(_broadcast_signal_decision(...))` already sits, or at the
   equivalent point for trade close/signal resolution). Cheapest to build, most consistent
   with existing code shape, but exposed to the same completeness risk named in §6: a future
   trade-close call site added without updating this list silently goes unnotified — no
   error, just a stale panel until the safety-net poll catches it (§4.4).
2. **A thread-safe single choke point** — capture the main event loop once at startup
   (`lifespan()` already runs on it: `main.py:1407`), and have `mark_history_changed()`
   dispatch via `asyncio.get_running_loop()` + `create_task` when already on the loop, or
   `asyncio.run_coroutine_threadsafe(coro, main_loop)` when called from another thread
   (verified: `run_coroutine_threadsafe` is not used anywhere in this codebase today —
   `grep -rn "run_coroutine_threadsafe" services/ main.py` returns nothing, so this
   introduces a genuinely new primitive, not an established local pattern). This lets the
   call live *inside* `paper_broker.close_position()`/`open_position()`,
   `signal_log.mark_resolved()`, and `candidate_ledger.record_decision()` themselves — one
   call site per write function, structurally guaranteed complete regardless of how many
   orchestration layers call them, and safe regardless of which thread runs them.

Recommendation for the implementation stage: **(2)**, because completeness (every write
reaches the notification, not just the ones someone remembered to wire) is the property the
data-plane HARD RULE weights most heavily, and a single choke point per write function is
structurally harder to silently break than four-plus call sites per event type. This should
get its own small test (call `mark_history_changed()` from a real
`ThreadPoolExecutor`-submitted function in a test, assert the broadcast still lands) before
being trusted — not asserted safe here on reasoning alone.

### 4.4 Frontend changes

- `polling-and-websocket.js`'s `connectWebSocket()` message handler (`:171-192`) gets a third
  branch: `payload.type === 'history_updated'`. If `currentView === 'history'`, trigger the
  same full-batch refetch `refreshHistoryInsightsIfActive()` currently performs; if not, set
  a `_historyDirty = true` flag and do nothing yet (the existing tab-open path,
  `showView('history')` → `loadTradingHistory()`, already does an unconditional full fetch on
  open regardless of this flag, so the flag is informational only — not load-bearing for
  correctness, just avoids a redundant fetch note in a future refinement).
- **Decouple History's refresh trigger from `/api/state`'s poll cadence entirely** — remove
  the unconditional `refreshHistoryInsightsIfActive()` call from `refresh()`'s own body
  (`polling-and-websocket.js:153`). This mirrors the exact rationale `_scheduler_loop`'s
  extraction already established on the backend (`main.py:548-558`'s own docstring: "moved
  out... which made every one of them tick-cadenced by accident of where the call lived, not
  by design"). History's refresh becoming tied to `/api/state`'s poll was the same kind of
  accident on the frontend side.
- **Safety-net poll**, independent of `/api/state`: a `setInterval`-driven check (not piggybacked
  on `refresh()`), gated on `currentView === 'history'`, at a **new, explicitly-labeled**
  constant — proposed 5 minutes (300000ms). This is a genuinely new timing choice (unlike the
  30s reuse above), stated honestly as design-time reasoning, not a measurement: an order of
  magnitude above the WS client's own worst normal reconnect backoff
  (`wsReconnectDelayMs`, capped at 30000ms, `polling-and-websocket.js:28,197`) and short
  enough that "the WS is down and nobody noticed" bounds at a few minutes of staleness, not
  longer. Should be revisited if implementation-stage telemetry shows WS disconnects are more
  frequent or longer-lived than this assumes.
- In-flight guard: reuse the existing idiom (`history-core.js`'s own comment block,
  `:407-417`, already documents exactly this class of bug — a periodic re-render clobbering
  an in-progress user action) rather than inventing a new one; a `history_updated` message
  arriving mid-apply-click should not tear down feedback DOM the user is about to see.

---

## 5. Alternatives compared

Per the data-plane HARD RULE's "competing solution families compared on mechanism,
correctness, failure behavior, and complexity":

| Approach | Mechanism | Correctness | Failure behavior | Complexity | Verdict |
|---|---|---|---|---|---|
| **A. Recommended** — event-push over the existing `ws_manager` connection, coalesced at 30s, safety-net poll at 5min | server-triggered from real writes (§4.2-4.3) | fetches exactly when something changed (§2) | WS gap → up to 5min staleness, bounded by safety-net poll | one new leaf module, frontend message-handler branch, N write-site hooks | **recommended** |
| B. Tune the existing timer's interval (shorter or longer) | unchanged mechanism | still fetches on a schedule with no relation to real writes | none new, same as today | trivial | rejected — doesn't fix "fetches when nothing changed," and changing a tuned interval without new measurement violates the data-plane HARD RULE outright |
| C. New/separate WS connection dedicated to History | own registry + endpoint | same correctness as A | a second thing that can silently disconnect | duplicated reconnect/backoff state machine, new server registry | rejected, §3.3 — no bottleneck it fixes; existing connection's fan-out shape isn't the kind of coupling this initiative targets |
| D. Per-resource/topic-tagged push (one message type per one of the ~9 sub-resources; frontend fetches only the changed subset) | same server-triggered shape as A, finer-grained | *more* efficient in principle (skip unaffected panels) | a missed/mis-tagged write silently starves exactly one panel — harder to notice than "the whole tab looks stale" | multiplies the write-site→topic mapping surface | deferred, not rejected — a plausible v2 once (A)'s own miss rate is characterized in production; premature now given §2's own honest caveat that not every endpoint's exact trigger was independently re-derived past the primary source read |
| E. Cheap poll-a-flag REST endpoint (`GET /api/history/etag`, polled every few seconds; full fetch only on change) | WS-independent; a lightweight timer replaces the heavy one | same correctness as A for "don't fetch when nothing changed" | no WS-gap risk (REST poll always eventually catches up) | new endpoint + still a poll loop, just a cheap one | rejected as inferior-but-viable — the WS connection already exists, is already connected every session, and this is exactly what item 26/the research doc's §2 point 4 already named as the target mechanism; adding a poll loop back in (even a cheap one) reintroduces the thing being removed for no benefit over reusing what's already there |

---

## 6. What this does not solve, and open items for the implementation stage

- **Completeness risk of the write-site enumeration is real, not hand-waved.** §4.3
  identified the correct set for the sources this design covers, but a *future* new write
  path (a new way to close a position, a new signal-resolution path) that doesn't call
  `mark_history_changed()` fails silently — the tab just looks stale, no error anywhere.
  This is exactly the shape CLAUDE.md's data-plane HARD RULE calls a defect
  ("a dropped message, skipped candidate, or DB hole is a defect... these properties fail
  silently: measure them"). The 5-minute safety-net poll (§4.4) bounds the damage but does
  not detect the gap. A stronger mitigation (a periodic cross-check — e.g., comparing
  `trade_log`'s max id against what the frontend last saw) is a plausible follow-up, not
  designed here.
- **`loadCandidateLogSummary()`'s and `loadCalibrationReport()`'s own 30s cache TTL
  (Task 6b, `analytics/routes.py:42-49`, `whale_calibration/routes.py:24-30`) puts a floor
  under how fresh a push-triggered fetch of those two endpoints can actually be** — pushing
  faster than 30s does not get below that cache's own staleness, only avoids the wasted
  round-trip when nothing changed. Worth naming explicitly so a future reader doesn't expect
  push latency alone to explain observed staleness on those two panels specifically.
- **`ws_manager.broadcast()`'s missing per-send timeout (§3.2)** is a real, pre-existing gap
  this design's own increase in steady-state message rate makes more worth fixing — flagged
  as a companion hardening item, not a blocker for this design.
- **Issue #410 (tick_executor/diagnostic-route sharing) is untouched.** This design changes
  *how often* those two routes get hit (fewer no-op hits during idle stretches, similar
  burst behavior during active trading) but doesn't change the sharing itself or do the
  query-cost measurement #410 is still waiting on.
- **Dimensional-analysis obligation carried forward, not discharged here.** The 30s
  coalescing reuse and the new 5-minute safety-net constant are both timing values; per the
  HARD RULE this needs its own `dimensional-analysis` pass at implementation time on the
  actual code, not just the reasoning stated in §4.2/§4.4.
- **§4.3's threading-safety choice (call-site enumeration vs. thread-safe single choke
  point) is explicitly left open**, with a recommendation, not a decision — appropriate for
  a design document per this repo's own convention (compare
  `2026-09-03-strategy-edge-gate-design.md`'s bucket-boundary detail, also deliberately left
  to the plan/implementation stage).

---

## 7. Safety and constraints confirmation

- No trading, risk, sizing, calibration, strategy, settlement, or auth code's *logic* changes
  — every touched write function (`close_position`, `open_position`, `mark_resolved`,
  `record_decision`, `record_snapshot`, `log_applied_change`) gains one additional,
  non-blocking, fire-and-forget notification call at its existing return path; nothing about
  what it decides, computes, or persists changes.
- No `config/settings.yaml` changes.
- `kalshi_account.trading_enabled`, the daily-loss kill switch, and paper-mode default are
  untouched.
- Per CLAUDE.md's toolchain guidance, implementing §4.3's choice inside
  `paper_broker.close_position()`/`open_position()` and `candidate_ledger.record_decision()`
  touches trading-critical/shared-state code and should get a `gitnexus`
  `impact`/`context`/`trace` pass before the multi-file edit, not skipped because the added
  call is "just a broadcast."

## 8. Success criteria (per CLAUDE.md's per-module axes)

- **Effectiveness:** the History tab reflects a real trade close, signal resolution, or
  candidate decision within one coalescing window (≤30s active, near-immediate if isolated)
  instead of up to a blind 30s regardless of activity, and issues zero fetches during genuine
  idle stretches (verifiable via browser network-tab observation, the same falsifier class
  the architecture audit itself used in §3.3).
- **Efficiency:** measured reduction in `/api/advisory/*`, `/api/regime/*`,
  `/api/candidate-log/summary`, `/api/confidence-calibration/*`, `/api/backtest/*` request
  counts during any stretch with the History tab open and no new trades/signals/decisions,
  relative to today's unconditional-every-30s baseline.
- **Informativeness:** no change to what any panel shows — this is purely a refresh-timing
  change, not a data-shape change; every existing loader function is called with the same
  arguments it has today.

---

## Design self-review

Scope of this review: internal consistency and unaddressed scope in the document above — not
a re-derivation from primary sources (that's the separate adversarial review this document
still needs, per CLAUDE.md's "nothing advances on one pass" HARD RULE, run as a fresh Agent
call with no memory of this session, after this document is returned).

**What holds up:**
- The central coupling question (§3) is answered from a mechanism-level distinction (bounded
  worker-pool/consumer contention vs. fire-and-forget fan-out) grounded in the exact same
  primary-source evidence the research doc itself used (`asyncio.create_task` at every
  existing `ws_manager.broadcast()` call site) — not asserted by analogy alone.
- §4.3's threading finding is the one piece of this document most likely to have been missed
  by a shallower design pass — a naive "just call `create_task` at the write point" reading
  of the task would have shipped a design that crashes the first time `candidate_ledger.
  record_decision()` fires, since that function runs on a `tick_executor` worker thread, not
  the event loop. This was verified by reading the actual call chain
  (`decision_bridge.py:66,129` → `tick_executor.run`), not inferred from the function's name.
- §2's endpoint-by-endpoint trigger table is built from reading each route handler's own data
  source (`services/advisory/routes.py`, `services/backtest/routes.py`,
  `services/whale_calibration/routes.py`, `services/market_analyst_agent/*.py`), not assumed
  from the loader function's name — catching, in particular, that `loadBacktestSweeps` and
  `loadCalibrationReport` are signal-resolution-driven, not trade-close-driven, which a
  same-named-file assumption would likely have gotten wrong.
- The alternatives comparison (§5) includes the one alternative (E, a poll-a-flag REST
  endpoint) that is genuinely viable, not a strawman — and states honestly why it's still
  inferior to reusing the WS connection that already exists, rather than omitting it because
  it's less flattering to the recommendation.

**Gaps and honest limitations, not fixed here because fixing them is plan-stage or
implementation-stage work:**
- §4.3 explicitly leaves the call-site-enumeration-vs-thread-safe-choke-point choice open. A
  reader could reasonably want this decided in the design stage; it's left open because the
  thread-safe approach (`run_coroutine_threadsafe`) is genuinely new to this codebase and
  deserves its own small proof (a real threaded-call test) before being committed to, which
  is implementation-stage work by this repo's own TDD convention, not a paper decision.
- Three of `PaperBroker.close_position()`'s four call sites (`exit_engine.py:410`,
  `position_netting.py:410`, `strategy_engine.py:815`'s `open_position` sibling) were **not**
  individually re-verified for event-loop-vs-worker-thread execution context — only the
  hottest one (`whale_stream_handlers.py:283-287`) and the manual-close route were confirmed.
  Adversarial review found `exit_engine.py` actually has a **fifth** `close_position` call
  site (`:102`, inside `close_if_settled`, alongside the already-listed `:410` inside
  `check_exits`) — both execute in the same calling context as the already-analyzed
  `check_exits` call sites, so this doesn't change the threading conclusion, only this
  citation's completeness. Noted for the record rather than silently fixed, since the count
  itself is what was wrong, not the underlying claim.
  This is stated as an assumption in §4.3, not asserted as checked, but a reader skimming
  only the recommendation in §4.3's closing paragraph could miss that caveat if they don't
  read the bullet list above it — worth calling out explicitly here too.
- This document does not attempt to enumerate whether any *other* currently-unlisted write
  path (a route this session didn't grep for, a future feature) already exists and would need
  the same hook — §2's table is built from the loaders the frontend calls today, which is the
  right scope for "what does History currently poll," but is not independently proof of
  "these five write functions are the complete set of things that can make History-tab data
  change." The safety-net poll (§4.4) is the stated mitigation for exactly this gap, not a
  claim that the enumeration is provably exhaustive.
- The 5-minute safety-net interval (§4.4) is a design-time guess, explicitly labeled as one —
  no telemetry on actual WS reconnect-gap duration/frequency was consulted (none was
  identified as existing for this specific connection), unlike the 30s reuse which does trace
  to a real prior measurement.
- I did not verify live whether `services/advisory/routes.py`'s and
  `services/whale_calibration/routes.py`'s manual-apply route handlers ever run any part of
  their body via `tick_executor.run` or another thread-offload before reaching
  `log_applied_change()` — classified as event-loop-side on the reasoning that they're plain
  `async def` handlers doing synchronous work, not from tracing every line of each handler.
- No live/browser verification was performed (no `chrome-devtools` MCP session against the
  running app) — this is a design document built entirely from static source reading, which
  is appropriate for the design stage but means every "current behavior" claim in §1 is a
  source-code read, not an observed live network trace. `docs/superpowers/research/2026-09-02-architecture-audit-second-pass.md`'s
  own §3.3 nginx-log methodology would be the natural falsifier for §1's polling-frequency
  claims at implementation time, not repeated here.

No claim in this document rests on the trade-stream research doc's own summary tables where
its underlying primary-source citations were cheaper to re-check directly — the one partial
exception is §1.1's characterization of the `_scoring_pool`/two-process-split findings (§3's
Non-goals bullets), which cites that document's own already-reviewed conclusions (§3, §5)
rather than re-deriving the `_scoring_pool.py` sharing analysis from scratch, since this
design doesn't depend on resolving that question and re-deriving it would be out-of-scope
duplication of already-completed, already-reviewed work.
