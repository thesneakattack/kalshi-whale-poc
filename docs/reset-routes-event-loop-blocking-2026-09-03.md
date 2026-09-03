# `services/reset/routes.py` event-loop-blocking research — 2026-09-03

Issue #510 (filed from a3's #508 tracing). `services/reset/routes.py` contains
no `await`, `run_in_executor`, or `tick_executor` anywhere — confirmed by
direct grep (`grep -n "await \|run_in_executor\|tick_executor\|asyncio.to_thread"
services/reset/routes.py` returns zero matches). This is the Tier0/P1 bug
class (event-loop-blocking synchronous SQLite calls inside `async def` route
handlers), pre-existing, independent of the persistence-layer (`db.py`)
migration. Research stage only — no fix, no plan, per the coordinator's
assignment.

## Headline finding: a real, measured 34-second full-app freeze is reachable today

`candidate_log.count_range()` (`services/candidate_log.py:412-432`) —
called by both `GET /api/reset/preview?candidate_log=true` and
`POST /api/reset` when `candidate_log: true` is in the request — runs
`SELECT COUNT(*) FROM rejection_events` with no `WHERE` clause when no range
is given. **Measured directly, read-only, against the live
`data/candidate_log.db`**: `rejection_events` has **22,596,141 rows**, and
that one `COUNT(*)` takes **34,129.54 ms** — over 34 seconds. Since this
route has no `await`/`tick_executor` anywhere, that entire 34 seconds runs
synchronously on the FastAPI event loop: the trading loop, every WebSocket
reader, and every other in-flight HTTP request are frozen for the duration,
identical in shape to the confirmed 13-minute stall PR #414 fixed for the
capture-write path (different modules, same underlying bug class: a plain
sync function with real disk I/O and no `await` point, called from `async
def`).

`count_range` also calls `capture_writer.flush_now("rejected_candidates")`
and `capture_writer.flush_now("rejection_events")` (`:426-427`) **before**
the count queries — an additional, unmeasured (not safe to trigger live for
this research pass) synchronous flush cost layered on top of the 34s figure
above, not included in it.

## Full call-path trace, all three routes

Every route is `async def`, every call inside it to any of the 9 domain
modules is a plain, unawaited synchronous function call — confirmed for
each site below by reading the actual function bodies (not assumed from
naming).

### `GET /api/reset/preview` (`services/reset/routes.py:118-137`)

Builds a `ResetBody` from query params, then calls `_reset_domain_counts(body)`
(`:79-115`) directly — no `await`, `create_task`, or `tick_executor`
anywhere in either function. Reaches, per selected flag:

| domain flag | function called | file:line | DB touched | measured cost (read-only, live) |
|---|---|---|---|---|
| `paper` | `broker.count_trade_range()` (only if ranged; unranged path is `len(broker.trade_log)`, pure in-memory, zero DB cost) | `_reset_domain_counts:89-91`, `paper_broker.py:792-800` | `paper_broker.db` (630 KB) | trivial — file this small, no measurement needed |
| `signal_log` | `signal_log.count_range()` | `_reset_domain_counts:100`, `signal_log.py:602-612` | `signal_log.db` (105 MB) | `signals` table, 168,597 rows, **1.68 ms** |
| `market_analyst` | `market_analyst_agent.total_count()` | `_reset_domain_counts:102`, `market_analyst_agent/per_market.py:316-319` | `market_analyst.db` (28 KB) | `analyses` table, 0 rows, **0.41 ms** |
| `candidate_log` | `candidate_log.count_range()` | `_reset_domain_counts:109-110`, `candidate_log.py:412-432` | `candidate_log.db` (3.81 GB) | **see headline finding — 34,129.54 ms** |
| `trade_category` | `trade_category.count_range()` | `_reset_domain_counts:113-114`, `trade_category.py:144-149` | `trade_category.db` (225 KB) | `trade_category` table, 2,190 rows, **1.48 ms** |
| `market_catalog`, `market_history`, `series_evaluator`, `calibration_history` | none — these four report `None` (no cheap count exists), see `_reset_domain_counts:103-108,111-112` | n/a for preview | n/a | n/a — preview never touches these 4 stores at all |
| `shadow` | none — reports `None` (`:97-98`) | n/a | n/a | n/a |

**So the preview route's real blocking exposure is entirely about
`candidate_log=true`** — every other flag is either free (in-memory), cheap
(single-digit ms), or a no-op count. The other four domains market_catalog/
market_history/series_evaluator/calibration_history are invisible to
preview's own cost profile (they report `None`), but are NOT invisible to
`POST /api/reset` itself — see below.

### `POST /api/reset` (`services/reset/routes.py:150-249`)

Calls `_reset_domain_counts(body)` first (`:158`, same cost table as
above — so a `POST /api/reset` with `candidate_log: true` pays the same
34-second `count_range` cost as the preview route, on top of everything
below), then, per selected flag, the actual destructive call:

| domain flag | function called | file:line | DB touched | operation shape |
|---|---|---|---|---|
| `paper` (unranged) | `broker.reset()` | `:203` | `paper_broker.db` | in-memory reset + a handful of small writes; trivial given file size |
| `paper` (ranged) | `broker.clear_trade_range()` | `:200`, `paper_broker.py:802+` | `paper_broker.db` | `DELETE FROM trades WHERE ...`; trivial given file size |
| `shadow` | `shadow.clear()` | `:213` | `shadow_mode.db` | not one of the 9 named domains in this module's own docstring; not measured, file was not in the original 9-module scope |
| `signal_log` | `signal_log.clear_range()` | `:217`, `signal_log.py:615-626` | `signal_log.db` (105 MB) | `DELETE FROM signals [WHERE ...]`; 168,597-row table, expect low-hundreds-of-ms order of magnitude by extrapolation from the 1.68ms `COUNT(*)` above (an unindexed DELETE typically costs more than an equivalent COUNT, but this table is small enough that it's very unlikely to approach seconds) |
| `market_analyst` | `market_analyst_agent.clear_all()` | `:221`, `market_analyst_agent/_db.py:101-104` | `market_analyst.db` (28 KB, 0 rows today) | trivial |
| `market_catalog` | `market_catalog.clear_all()` | `:225`, `market_catalog.py:653-659` | `market_catalog.db` (44 MB) | `DELETE FROM markets` (149,403 rows, 72.44ms read-cost measured) + `DELETE FROM series_scan_state` (5,409 rows, 2.84ms read-cost) — both tables read-cheap; DELETE is typically comparable-to-somewhat-more-expensive than COUNT for a full-table wipe, still very unlikely to reach seconds at this row count |
| `market_history` | `market_history.clear_all()` | `:229`, `market_history.py:429-437` | `market_history.db` (622 MB) | `DELETE FROM snapshots` (2,937,954 rows, 48.68ms read-cost) + `DELETE FROM outcomes` (501,296 rows, 10.79ms read-cost) — larger row counts than market_catalog but still sub-100ms on the read side; genuinely the second-largest store after candidate_log.db but nowhere near its row count or cost |
| `series_evaluator` | `series_evaluator.clear_all()` | `:233`, `series_evaluator.py:294-298` | `series_evaluator.db` (12 KB, 38 rows) | trivial |
| `candidate_log` | `candidate_log.clear_range()` | `:237`, `candidate_log.py:435+` | `candidate_log.db` (3.81 GB) | `DELETE FROM rejected_candidates` + `DELETE FROM rejection_events` (the 22.6M-row table) `[WHERE ...]`, plus the same two `capture_writer.flush_now()` calls `count_range` also pays. Not measured directly (a real DELETE against live data is out of scope for a read-only research pass), but given the read-only `COUNT(*)` on the same table alone costs 34+ seconds, an unranged `DELETE FROM rejection_events` (no index to narrow the scan) should be assumed **at least as expensive**, plausibly more (DELETE does more per-row work than COUNT) |
| `calibration_history` | `calibration_history.clear_all()` | `:241`, `calibration_history.py:106-108` | `calibration_history.db` (98 KB, 91 rows) | trivial |
| `trade_category` | `trade_category.clear_range()` | `:245`, `trade_category.py:152-157` | `trade_category.db` (225 KB, 2,190 rows) | trivial |

**Net for `POST /api/reset`**: identical headline risk to preview
(`candidate_log: true` is the one flag that can freeze the app for tens of
seconds), plus every other flag is cheap-to-trivial at current store sizes —
`market_history.db`'s 622 MB doesn't translate to a slow operation because
its row counts (2.9M/501K) are two orders of magnitude below
`rejection_events`' 22.6M.

### `GET /api/reset/history` (`services/reset/routes.py:140-147`)

Calls `reset_log.recent(limit=...)` (`:147`, `reset_log.py:67-73`) — a plain
`SELECT ... ORDER BY executed_at DESC LIMIT ?` against `reset_log.db`
(160 KB). Not one of the 9 named domains (it's the audit-trail table this
whole module writes to, not a reset target) and trivially cheap at this
file size. Same bug *class* (no `await`), zero real severity today.

## Does PR #414's `tick_executor.run(...)` pattern apply unchanged?

**No — the mechanism is the same, but the usage shape has to be different,
not a drop-in copy.** PR #414's fix is specifically shaped for a
fire-and-forget WS-message-handler write path: `record_*()` stops calling
`flush()` inline and instead the async caller does
`asyncio.create_task(tick_executor.run(<module>.flush))` — a task the
handler does **not** await, because the handler's own response doesn't
depend on the flush having finished yet.

Every blocking call in `reset/routes.py` is the opposite shape: **the route's
own HTTP response depends directly on the result** — `GET /api/reset/preview`
must return the real counts in its JSON body, `POST /api/reset` must not
report `{"ok": true, "cleared": [...]}` until the clears have actually
happened, `GET /api/reset/history` must return the actual rows. A
fire-and-forget `create_task` would return an HTTP response with no data (or
stale/wrong data) before the real work finished — silently wrong, not just
slow.

The correct fix shape reuses the same underlying primitive
(`services/tick_executor.py`'s `run()`, already used elsewhere in this app
off the event loop) but **awaited**, not fired-and-forgotten:
`await tick_executor.run(lambda: _reset_domain_counts(body))` for the
preview route, and similarly for each blocking call inside `reset_broker`
(or one `await tick_executor.run(...)` wrapping the whole handler body,
given `POST /api/reset` is a rare, deliberate, effectively-single-threaded
action with no real concurrency to preserve inside it). This is the same
class of fix PR #501's `market_catalog.py`/`fault_log.py` follow-up and this
session's own `db-foundation-must-fix-tests` branch both already establish
as a pattern in this codebase (offload the blocking call, await the result)
— just not literally PR #414's specific fire-and-forget shape, which would
be the wrong tool for a route that has to return real data.

## Realistic hit rate (nginx access log)

Checked the live web container's retained log buffer directly (`docker logs
ddev-kalshi-whale-poc-web`, 13,128 lines, spanning roughly 04:24–06:37 UTC
today per the timestamps visible in the sample): **zero occurrences of
`/api/reset`, `/api/reset/preview`, or `/api/reset/history` in that entire
window.** Consistent with this being a deliberately-triggered "Danger Zone"
UI action (per this module's own docstring/`ResetBody` comments) rather than
anything polled routinely — unlike `/api/candidate-log/summary` or
`/api/quality/summary`, which the earlier de-polling work found firing
every 6 seconds. **Low frequency, not low severity**: a real user opening
the Danger Zone panel with the candidate-log checkbox on, or confirming a
reset with it set, pays the full 34+ second freeze every single time they
do — it just hasn't happened to be captured in this particular ~2-hour
log window, not evidence it doesn't happen at all.

## Summary for whoever picks up the fix/plan stage

- One dominant, severe, directly-measured risk: `candidate_log`'s
  `rejection_events` table (22.6M rows) makes both `count_range()` and
  `clear_range()`/`clear_all()` genuinely multi-second-to-tens-of-seconds
  full event-loop freezes, reachable from both `GET /api/reset/preview` and
  `POST /api/reset` whenever `candidate_log: true` is selected.
- Every other domain, at today's actual row counts, is cheap-to-trivial —
  this is not a "fix all 9 modules with equal urgency" issue, it's
  overwhelmingly a `candidate_log.py` (and specifically its
  `rejection_events` population table) problem, with the other 8 modules
  along for the ride architecturally (same missing-`await` bug class) but
  not currently causing real-world pain at their current sizes.
- PR #414's exact fire-and-forget pattern is the wrong shape here; the fix
  needs an *awaited* `tick_executor.run(...)` per blocking call (or one
  wrapping the whole `POST /api/reset` handler), since every one of these
  three routes' HTTP responses depends on the blocking call's actual
  result.
- Low observed real-traffic frequency (zero in a ~2-hour window) doesn't
  reduce the severity of a single occurrence — worth fixing on its own
  merits, not deprioritized because it's rare.
