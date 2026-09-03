# Adversarial review: `reset-routes-event-loop-blocking-2026-09-03.md`

Independent pass against the research doc (as it stood after the PM's
tick_executor/PR #409/PR #424 correction was applied), assuming the doc is
wrong until its load-bearing claims are re-derived from primary sources —
never from the doc's own tables or summary.

## Scope checked

- Every file:line citation in the call-path tables, re-read against current
  source (not the doc's paraphrase of it).
- Whether the doc's own recommendation section is internally consistent with
  the precedents it cites (PR #409, PR #424, `_scoring_pool.py`).
- Whether any comparably-shaped fix already exists elsewhere in the codebase
  that the doc missed — i.e. does the doc's claim of *the* precedent-setting
  fix actually name every relevant precedent, or only some.
- The "realistic hit rate" section's own arithmetic and log-window framing.
- The headline measurement's framing — stated as a single number
  (34,129.54 ms) with no note of cache-state sensitivity.

## Findings

### 1. Missing precedent: `services/analytics/routes.py:104-129`

The doc's recommendation section discusses PR #424 (bounded query) and PR
#409 (starvation from sharing `tick_executor`) as the two governing
precedents, but misses a closer one: `population_gate_summary()` in
`services/analytics/routes.py:104-129` hits the *same* `rejection_events`
table this doc's headline finding is about, and was already fixed via
`await tick_executor.run(...)` plus a 30s TTL cache — with its own comment
at `:105-107` stating it was "proven via a live py-spy stack trace to block
the event loop for 17-38s on every call." That range overlaps this doc's
own 34s figure closely enough that it's almost certainly the same
underlying cost.

Checked whether that fix used an isolated pool (the direction this doc's
recommendation section argues for) or the plain shared one PR #409 flagged
as unsafe: `grep -n "^from\|^import\|tick_executor"` on the file shows it
imports plain `services.tick_executor` — the shared 2-worker pool, not a
dedicated one. If that's correct, `analytics/routes.py` may be carrying an
unresolved instance of PR #409's own starvation risk right now, live,
independent of anything this doc proposes.

**Required fix:** add this precedent to the doc, including the shared-pool
caveat, so whoever picks up the fix/plan stage checks `analytics/routes.py`
before assuming an awaited `tick_executor.run()` is a safe pattern to copy
just because it's already shipped elsewhere in the codebase.

### 2. Overstated headline claim: "34,129.54 ms" presented as a fixed cost

The doc's `COUNT(*)` measurement was a single cold-cache run. SQLite page
cache means a repeated query against the same table in the same process is
dramatically cheaper — this matters because the doc's narrative ("pays the
full 34+ second freeze every single time") implies every call costs the
same, when in practice the first call after a period of inactivity is far
more expensive than a call shortly after.

Re-measured directly (read-only, same methodology as the original): first
call **17,027 ms** cold, then **433 ms** and **370 ms** on subsequent calls
in the same process. The original 34,129.54 ms figure is real (it's the
actual number the original script measured) but not representative of a
"typical" call — the doc should carry the range, not just the single worst
number, and should not describe it as constant across occurrences.

**Required fix:** state the re-measurement explicitly next to the headline
figure, and stop describing the cost as a flat, unvarying "every single
time" number in the surrounding prose — restate as "a freeze on the order
of tens of seconds cold, low-hundreds-of-ms warm" wherever the doc
describes real-world impact.

### 3. Wrong nginx log window: "roughly 04:24–06:37 UTC" / "~2h"

The doc's "Realistic hit rate" section describes the checked nginx access
log window as ~2 hours (04:24–06:37 UTC). Re-checked the same `docker logs
ddev-kalshi-whale-poc-web` output the doc's author says it read: the
13,128-line buffer's actual first and last timestamps span **2026-09-02
16:38:45 to 2026-09-03 06:37:14** — roughly **14 hours**, not 2. The zero-
occurrence finding for `/api/reset*` itself still holds (independently
re-confirmed: no matches for `/api/reset`, `/api/reset/preview`, or
`/api/reset/history` anywhere in the buffer), but the window size claim
built on top of it was wrong by a factor of ~7, which matters for anyone
using "how often does this really get hit" to prioritize the fix.

**Required fix:** correct the window to the accurate ~14h span with exact
timestamps everywhere the doc states or implies ~2h, including the closing
summary section.

## What was NOT found to be wrong

- All four spot-checked file:line citations in the self-review (`candidate_
  log.py:412`, `paper_broker.py:792`, `reset_log.py:67`,
  `market_analyst_agent/per_market.py:316`) re-verified independently —
  still correct.
- The doc's reframed recommendation (bound/approximate the query first per
  PR #424's precedent, dedicated pool only if still needed after that,
  never an awaited call into the shared `tick_executor` pool per PR #409)
  is sound and consistent with both cited precedents' actual mechanisms,
  not just their outcomes.
- The core headline finding — `candidate_log`'s `rejection_events` table
  makes both `count_range()` and `clear_range()`/`clear_all()` genuinely
  expensive, unawaited, event-loop-blocking calls reachable from all three
  `/api/reset*` routes — holds. None of the three findings above touch its
  validity; they are corrections to supporting detail (a missing
  precedent, an overstated constant, a wrong window size), not to the
  central claim.

## Verdict

**GO, with the three required fixes above** — none touch the headline
finding's validity.
