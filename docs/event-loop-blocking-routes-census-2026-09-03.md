# Research: systematic census of event-loop-blocking route handlers

2026-09-03. Issue #530. Requested by coordinator autotrade-1d after two independent instances
(issue #510's `reset/routes.py`, issue #529's `observability/routes.py`) were found by accident,
by two different sessions, while doing unrelated migration-planning work — both times because a
Gate 1 event-loop check looked at one function of a module and got the right answer for that
function, never systematically extended to every route file. This document is that systematic
extension: every route file in the repo, every `async def` handler, checked for undispatched
synchronous DB/file I/O.

## Method

Four parallel read-only sweeps (mechanical, one per group of route files), each instructed to
check every `async def` handler for calls to synchronous DB-touching functions without
`await tick_executor.run(...)`, `run_in_executor`, or `asyncio.to_thread`. Plain `def` handlers
are excluded by design — FastAPI runs those in its own thread pool automatically, so they're not
a concern. Findings below are the sweep's code-shape verdicts; **severity numbers are live
measurements against the running app, done separately, not estimated** — the two are kept
distinct throughout, since code-shape alone doesn't tell you how bad an instance actually is (see
"Severity varies enormously" below).

## Headline numbers

**89 async route handlers checked across 17 `services/*/routes.py` files plus `main.py`'s 13
inline handlers (counted directly from the census table below, not from any subagent's own
summary tally — one subagent's stated summary, "27 routes / 20 blocking / 7 safe," didn't
match its own itemized per-route list, "24 routes / 16 blocking / 8 safe"; this document uses
the itemized counts throughout, verified by direct recount during self-review). **58 are
BLOCKING** (undispatched synchronous DB/file I/O), **4 are UNCLEAR** (need a direct follow-up
read, listed below), **27 are SAFE** (no sync I/O, or properly dispatched). This is not a
handful of instances — it is the dominant pattern across the
route layer, not the exception.

## Severity varies enormously — measured, not assumed

**Confirmed CRITICAL, genuinely undispatched (30+ seconds, live-measured):**
- `GET /api/quality/summary` — **30.25s** (subagent measurement). `diagnostics.run_offline()`
  (the largest single piece of this route, `services/quality/routes.py:92`) is **not** part of
  this — it's `await`ed and runs natively on aiosqlite (PR #424's own prior work; see "Prior
  history" below). The genuine, undispatched contributors, confirmed by direct read with no
  prior-investigation history found for either: `alerting.active_alerts()`
  (`services/alerting/alerting.py:171-177`, one plain `SELECT`) and `fault_log.summary()`
  (`services/fault_log.py:231-248`, four separate aggregate queries against the `faults` table).
  `research.latest()` (`services/quality/routes.py:62`) is also undispatched and separately
  confirmed BLOCKING for `research/routes.py`'s own routes — likely a smaller contributor, not
  measured in isolation here. This is the endpoint named first in CLAUDE.md's own "Start
  investigations here" list — it has been timing out all session with no established root cause
  until this sweep.
- `POST /api/reset` with `candidate_log:true` / `GET /api/reset/preview?candidate_log=true` —
  **34,129ms** (issue #510, PR #512, already tracked, not re-measured here).

**Confirmed severe, but NOT an undispatched-call defect — reclassified after deeper checking,
belongs in issue #410's track, not this one:**
- `GET /api/candidate-log/summary` — measured directly, this document: **24.44s cold, 0.59s
  warm** (a 30s TTL cache is genuinely working, confirmed by calling it twice). This document's
  first draft misattributed this to `candidate_log.gate_summary()` (the function the sweep
  correctly found undispatched) — wrong function. `gate_summary()` reads a small (62K-row)
  table and is **deliberately** left inline, per `services/analytics/routes.py:110-112`'s own
  comment, based on a prior live py-spy trace that didn't implicate it — not an oversight, no
  fix needed. The real cost driver is `candidate_log.population_gate_summary()`
  (`services/analytics/routes.py:127-129`), which **is already dispatched** via
  `await tick_executor.run(...)` and cached — but the underlying scan (an unfiltered
  `rejection_events` read) has grown from a documented ~4.8s (measured 2026-08-26, 6.2M rows)
  to ~24-31s today (22.6M rows now, per PR #512). Since it's dispatched, this does not block the
  event loop directly — but it does monopolize one of `tick_executor`'s only 2 worker threads
  for ~24-31s on every cache-miss poll, which is a **capacity/starvation** problem, structurally
  identical to what PR #409 already fixed for `run_offline()` and exactly what issue #410 is
  already open to investigate for this precise function ("population_gate_summary()... hasn't
  been measured yet" — it now has been, by this document, with a real number). Cross-referencing
  #410 rather than treating this as a new finding under #530's undispatched-call framing.

**One table, three separate sightings — not three unrelated findings.** `rejection_events`
(22.6M rows) is the table behind PR #512's 34.1s `count_range()` finding, this document's own
24-31s `population_gate_summary()` scan, and (per this session's own earlier Task 3 migration
work) one of the two tables behind `candidate_log.db`'s 3.6GB size and 20 leaked connection
handles. Three different access patterns, one table that has outgrown all of them. Whoever
picks up any one of these three should know the other two exist before choosing retention, an
index, or a rollup as the fix — that design decision isn't made here, but the shared root
belongs on the record.

**Prior history worth knowing before triaging any of the above**: `/api/quality/summary` and
`candidate_log.population_gate_summary()` both have substantial, already-merged remediation
history (PR #409, #410, #420, #424 — tick_executor offloading, a reverted connection-pool
attempt, query-bound fixes, TTL caching) that this document's sweep did not originally surface,
because the sweep checked code shape (dispatched or not) rather than git/issue history. Anyone
picking up a fix here should read that history first — the "obvious" fix (wrap it in
`tick_executor.run`) has already been tried, reverted, and replaced with a different approach at
least once in this exact area.

**Confirmed real but moderate, window-dependent:**
- `GET /api/observability/summary` — **7.66s at `hours=720`** (issue #529), but **0.79-0.91s at
  the default `hours=24`** (verified twice, by autotrade-84 and independently by this session).
  Severity scales with the query window, not a flat per-call cost — worth knowing before
  choosing a fix shape (dispatch off-loop vs. bound/paginate the query).

**Confirmed currently fast despite being structurally blocking (spot-checked, not assumed
safe-by-code-shape):**
- `GET /api/state` — 0.52s cold, 0.06-0.07s warm, despite three undispatched calls
  (`signal_log.stats()`, `shadow.recent()`, `shadow.stats()`). CLAUDE.md names this "the fastest
  live read" and it is almost certainly the single most frequently polled endpoint in the app —
  its structural defect matters even though today's measured cost is negligible, because it's
  the endpoint every future table-growth regression would hit hardest and fastest.
- `GET /api/signals/history` — 0.39s.
- `GET /api/trading-history` — 0.06s.

**The pattern**: severity tracks table size and query shape (full scans, wide windows,
aggregates over large tables), not whether a handler is blocking in the code-shape sense — code
shape alone predicts *which* handlers are structurally wrong, not *how much it currently
costs*. Every `candidate_log`-touching handler found so far is catastrophic; most others are
currently cheap. This exactly matches PR #512's own finding for the `/api/reset` family
("overwhelmingly a candidate_log.py problem, not a fix-all-9-equally issue") — now confirmed to
generalize across the whole route layer, not just that one file.

## Priority ranking: cost × call frequency, not instance count

The 58-instance count is not a to-do list, and ranking by count would be actively misleading —
`/api/state` has three undispatched calls and `/api/quality/summary` has three too, but their
real-world impact differs by orders of magnitude. Frequency pulled from the live nginx access
log (`/var/log/nginx/access.log` inside the `web` container), covering a real **19h35m window**
(`2026-09-02T16:38:48` to `2026-09-03T12:14:27`) — not intuition. This repo already has
precedent for intuition being wrong here: the History tab's polling produced 411-2,305
requests/hour foregrounded versus 0-10/hour backgrounded, a >40x swing nobody would have guessed
from the code alone.

| Endpoint | Calls in window | Measured cost | Cumulative impact | Mechanism |
|---|---|---|---|---|
| `GET /api/quality/summary` | 358 (18.3/hr) | 30.25s, every call, no caching found | **~3.01 event-loop-blocked hours — 15.4% of the entire 19.6h window** | Directly blocks the event loop (undispatched) |
| `GET /api/candidate-log/summary` | 2,327 (1 per **30.3s**) | 24-31s cache-miss / 0.59s cache-hit | Poll interval ≈ 30s cache TTL ≈ scan duration — a `tick_executor` worker (1 of only 2 in the whole app) is very likely occupied by this scan **near-continuously** | Dispatched (doesn't block the event loop directly) but a severe shared-capacity/starvation risk — issue #410's track, not #530's |
| `GET /api/state` | 5,089 (260/hr, ~1 every 14s) | 0.06-0.52s | ~0.4 event-loop-blocked hours (rough, cost varies) — **the highest call volume of any endpoint measured**, so even its small per-call cost compounds, and it's the endpoint every future table-growth regression (same shape as `candidate_log`'s) would hit fastest | Directly blocks the event loop (undispatched) |
| `GET /api/observability/summary` | 32 (1.6/hr) | 0.79-7.66s, window-dependent | Low — infrequent enough that even the worst-case window cost is a minor aggregate contributor | Directly blocks the event loop (undispatched) |
| ~15 dashboard-batch endpoints (`regime/*`, `advisory/status`, `confidence-calibration/*`, `market-analyst/status`, `backtest/*`, `suggestions/declined`, `series-evaluator/status`) | ~2,320-2,325 each — clustered tightly enough (within 15 of each other) to be one dashboard tab's batch poll firing every ~30s | Not individually measured in this pass | Unknown until measured — same poll cadence as `candidate-log/summary`, so worth checking whether any of these are ALSO scanning something that's grown past a prior measurement, the same way `population_gate_summary()` had | Mixed — some of these are SAFE (in-memory only, per the census table below), some are BLOCKING; frequency alone doesn't tell you which ones matter, cost does |

**Reading this table**: `/api/quality/summary` is the worst *confirmed* offender in pure
event-loop-hours-lost terms — over 15% of the observed window, no other request could be served
at all while one of these was in flight, which directly matches PR #424's own prior finding ("5
concurrent `GET /api/quality/summary` requests stalling an unrelated `GET /api/state` for
minutes"). `/api/candidate-log/summary` is arguably worse in a different, harder-to-see way — it
doesn't freeze the whole app, but it may be quietly consuming half of `tick_executor`'s entire
capacity around the clock, a mechanism this document didn't have the tooling to confirm directly
(no `tick_executor` queue-depth/worker-occupancy metric was checked — that's the natural next
measurement, not done here). `/api/state`'s ranking is a volume story, not a per-call one — worth
fixing on principle (it's the endpoint most exposed to any future data-growth regression) even
though its current aggregate cost is the smallest of the four measured.

**This ranking, not the 58-instance count, is the actual triage input.** The remaining ~54
BLOCKING instances not in this table are real, structurally identical defects — but nothing in
this document measured them, and per the pattern already confirmed twice (`/api/state` cheap
despite 3 undispatched calls, most of the `/api/reset` family cheap despite the same undispatched
shape), assuming they're all costly would be exactly the kind of guess CLAUDE.md's data-plane
HARD RULE says not to make. **Not recommending a fix for all 58** — a blanket refactor of the
route layer is the same shape of confident sweeping change this repo has already been burned by
twice in adjacent code: `tick_executor.connection_for()` was built and deliberately left
unwired after investigation found two specific reasons it wasn't safe to wire in broadly, and PR
#424's own elastic connection pool was built, measured, and reverted after proving it made the
exact incident it targeted worse under real concurrent load. The recommendation is: fix the ones
in this table on their measured evidence, and measure before touching any of the other ~54,
not before.

## Full census by file

*(BLOCKING = async handler, undispatched sync DB/file I/O. SAFE = no such I/O, or properly
dispatched. UNCLEAR = sweep couldn't confirm, listed separately below for follow-up.)*

| File | BLOCKING | SAFE | Notes |
|---|---|---|---|
| `services/diagnostics/routes.py` | 4 | 0 | `/api/archive/epochs`, `/api/archive/compare`, `/api/archive/snapshot`, `/api/diagnostics/settlement-edge` |
| `services/observability/routes.py` | 2 | 1 | `history`/`summary` = issue #529; `current` (in-memory) safe |
| `services/storage_health/routes.py` | 0 | 3 | Genuinely clean — `integrity-check` properly uses `asyncio.to_thread` |
| `services/reset/routes.py` | 3 | 0 | `preview`, `history` (new findings) + `POST /api/reset` (issue #510) |
| `services/history/routes.py` | 5 | 0 | signals/history, signals/clusters, trading-history, market-history/summary, market-history/hypothetical-trades |
| `services/whale_calibration/routes.py` | 5 | 1 | `report` properly dispatched via `tick_executor.run`; the other 5 aren't |
| `services/config/routes.py` | 1 | 1 | `POST /api/config`'s `log_applied_change` undispatched |
| `services/research/routes.py` | 4 | 0 | including `POST /api/research/run`, whose *second* call (`research.latest()`) is undispatched even though the heavy first call correctly uses `asyncio.to_thread` |
| `services/advisory/routes.py` | 4 | 2 | all four `config_performance.log_applied_change()` call sites |
| `services/market_catalog/routes.py` | 2 | 4 | the 4 SAFE ones properly use an async Kalshi client; `status`/`search` don't |
| `services/analytics/routes.py` | 9 | 7 | 2 UNCLEAR (`series-evaluator/status`, `/reset`) |
| `services/backtest/routes.py` | 0 | 0 | 2 UNCLEAR, both need a direct `signal_log` read |
| `services/alerting/routes.py` | 3 | 0 | active, history, resolve |
| `services/position/routes.py` | 3 | 2 | `risk/halt`, `risk/resume`, `shadow-risk/resume` — **safety-adjacent, see below** |
| `services/backup/routes.py` | 2 | 1 | `run` properly awaited; `status`/`history` aren't |
| `services/quality/routes.py` | 1 | — | `/api/quality/summary`, CRITICAL, see above |
| `services/exits/routes.py` | 0 | 1 | clean, in-memory only |
| `main.py` (13 inline handlers) | 10 | 4 | including `/api/state` (see above) and both position-close endpoints |

**UNCLEAR, need direct follow-up before being counted either way**: `services/analytics/routes.py`'s
`GET /api/series-evaluator/status` and `POST /api/series-evaluator/reset`; `services/backtest/routes.py`'s
`GET /api/backtest/entry-threshold` and `GET /api/backtest/min-whale-winrate` (both likely call
`signal_log` functions, not directly confirmed by the sweep).

## A safety-relevant subset worth flagging explicitly

`services/position/routes.py`'s `POST /api/risk/halt`, `POST /api/risk/resume`, and
`POST /api/shadow-risk/resume` are BLOCKING — they call `risk_manager.py`'s/`shadow_mode.py`'s
`_persist()` synchronously. These are manual kill-switch controls, not high-frequency reads;
their event-loop cost is real but the operational risk is different in kind from a polled
dashboard endpoint (a human explicitly clicking "halt trading" tolerates a blocking call
differently than a monitoring poll does). Noting this so a future fix doesn't lump it in with
the read-heavy endpoints without considering the different risk profile.

## Why this stayed invisible until two accidents surfaced it

The persistence-layer migration's Gate 1 process checks "does this module's function run on the
event loop" — correctly, for the specific function it's migrating. `observability.py`'s
original verdict ("NO") was accurate for `maybe_capture`, the function actually in scope for
that migration; it was never asked about `history()`/`summary()`, which aren't part of the
db.py migration's scope at all. Same shape for `reset/routes.py`. Two different sessions, two
different modules, same blind spot: **a check run per-migration-target module, never run as its
own sweep of the actual attack surface (every route file)**. This document is that sweep, run
once, deliberately, rather than accumulated by further accident.

## Scope note: this is a different, related defect class from the db.py migration

The persistence-layer migration (`services/db.py`, PR #518+) fixes **connection-management**
(a leaking `_connect()` not calling `close()`). Everything in this document is a **call-site
dispatch** defect (a correct, closing connection still blocks the event loop if called
synchronously from an `async def` handler with no `tick_executor.run()`/equivalent). A module
can be fixed on one axis and still be broken on the other — migrating `observability.py`'s
`_connect()` to `db.py` (Task 5, pre-flighted separately) does not touch `routes.py`'s dispatch
problem at all. These should not be conflated into one plan or one PR.

## What this document does not do

Design the actual fix for the handful it does prioritize, or decide the mechanism (dispatch
off-loop vs. bound/paginate vs. retention/index/rollup on `rejection_events` itself). The
priority ranking above says *which* four endpoints have measured evidence behind them and that
the other ~54 shouldn't be assumed costly without their own measurement first — it deliberately
stops short of picking a fix shape, since the severity range found (window-dependent for
`observability.py`, capacity-dependent for `candidate_log.population_gate_summary()`,
directly-blocking for `quality/summary`/`state`) means a single uniform fix likely isn't right
for even the four measured ones, let alone all 58. `candidate_log`-touching findings inherit
issue #510's/#410's own established framing rather than being re-decided here. This document's
job was the census, the severity evidence, and the triage ranking; fix design is a follow-on
decision.
