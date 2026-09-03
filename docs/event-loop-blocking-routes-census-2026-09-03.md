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

**Confirmed CRITICAL (30+ seconds, live-measured):**
- `GET /api/quality/summary` — **30.25s** (subagent measurement). Root cause traced:
  `alerting.active_alerts()` (`services/alerting/alerting.py:173`) and `fault_log.summary()`
  (`services/fault_log.py:235`, 4 aggregate queries), neither dispatched. This is the endpoint
  named first in CLAUDE.md's own "Start investigations here" list — it has been timing out all
  session with no established root cause until this sweep.
- `GET /api/candidate-log/summary` — **31.1s** (measured directly, this document). Calls
  `candidate_log.gate_summary()` undispatched (Group C sweep finding), against the same
  22.6M-row `rejection_events` table PR #512 already identified as the single most severe
  table in this app for exactly this reason.
- `POST /api/reset` with `candidate_log:true` / `GET /api/reset/preview?candidate_log=true` —
  **34,129ms** (issue #510, PR #512, already tracked, not re-measured here).

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

Propose a fix. Given the severity range found (catastrophic for `candidate_log`-touching
handlers, negligible for most others today), a uniform fix (e.g. "wrap everything in
`tick_executor.run`") may not be the right answer for all ~59 instances — some may warrant query
bounding/pagination instead (per the `observability.py` window-dependence finding), and the
`candidate_log`-touching ones specifically inherit issue #510's own established framing (fix
tracked there, not re-decided here). This document's job was the census and the severity
evidence; triage and fix design are a follow-on decision, not made here.
