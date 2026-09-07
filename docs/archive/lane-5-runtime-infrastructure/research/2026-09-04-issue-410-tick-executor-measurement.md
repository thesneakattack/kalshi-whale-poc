# Issue #410 measurement: does the 2026-09-03 route caching make the tick_executor sharing a non-issue?

Research note, per `gh issue view 410`'s deferred-pending-measurement framing. Written from a
worktree (`docs/tick-executor-query-cost-measurement`), against `origin/main` @ `a6a7b7b`
(merge of PR #561). No code changed; no config touched; no restart/reload triggered. All
measurements below are read-only, run against the live app's real `data/*.db` files via
`docker exec` (matching `ddev exec`'s host-user UID/GID) and `curl` against
`https://kalshi-whale-poc.ddev.site:8443`.

## Verdict

**#410 needs a fix. The 2026-09-03 caching does not make this a non-issue** — not because the
caching is fake (it's real, current, and correctly described in the code comments), but because
it has a load-bearing timing bug that makes it far less effective than its own design intent in
the one usage pattern that matters (a human with the History tab open), and because the
underlying query cost it's caching has grown ~3-4x in the week since it was last measured, purely
from unbounded table growth, independent of any code change. Both routes still route their
(now-confirmed-expensive) work through `tick_executor`'s 2-worker pool, the same pool
`candidate_ledger.claim()`/`record_decision()` uses on every whale signal.

Recommended fix family: **not** a bolted-on dedicated `ThreadPoolExecutor` (the `_scoring_pool.py`
pattern issue #410's own text names) but the newer, better-precedented pattern this exact
codebase already used the same day #410 was filed, ~7 hours after
(`docs/archive/lane-6-observability-quality-safety/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md` (moved there 2026-09-06, planning-lanes migration) was
committed 2026-09-01T22:47Z; #410 was filed 2026-09-01T15:41Z — corrected here after adversarial
review falsified the original "three days before" claim, which was backwards) — rewrite the
blocking synchronous `sqlite3` reads to native `aiosqlite`
(`services/quality/routes.py`'s `run_offline()`). This removes the thread-pool dependency for
`population_gate_summary()`, which converts cleanly (pure SQL `GROUP BY`, trivial post-processing)
— but **not** for `whale_calibration`'s `_build_report()`: its dominant 3.4s cost
**[corrected 2026-09-05, #582 — provisional, from unmerged PR #581: the CPU cost is larger and
starts earlier than this note says. The "~2s fetch" is itself ~75% CPU (~0.884s SQL + ~2.691s
`json.loads`/materialize), and the compute pass re-measured at ~4.383s. Total ~7.96s, not ~5.5s.
This note's *conclusion* — aiosqlite alone is insufficient — is strengthened, not weakened.]**
(`_bucket_win_rates`/`_factor_report`) is pure Python with no database access at all, so an
aiosqlite rewrite alone doesn't touch it. That path needs aiosqlite for its ~2s fetch **[#582: for
the ~0.884s SQL portion only — the rest of the "fetch" is CPU and needs the offload too]** *plus* a
separate offload (e.g. `asyncio.to_thread`, the same mechanism `services/quality/routes.py` uses
right next to `run_offline()` for its own non-DB-driver-shaped work) for the compute pass. See
"Recommendation" below for the full ranked comparison and the independent, orthogonal cache-bug
fix this doesn't replace.

## 1. What #410 actually asked to be measured

`gh issue view 410` (state: OPEN, confirmed live) defers a fix pending measurement of whether:

- `services/analytics/routes.py`'s `get_candidate_log_summary` → `candidate_log.population_gate_summary()`
- `services/whale_calibration/routes.py`'s `get_confidence_calibration_report`/`apply_confidence_calibration_suggestion` → `_build_report()`

are now "cheap enough" post their 2026-09-03 30s route-level caching that sharing
`tick_executor`'s 2-worker pool with `candidate_ledger.claim()`/`record_decision()` is no longer
a real risk, using PR #409's own `run_offline()` incident (permanently pinned both workers,
5h10m, confirmed live 2026-09-01) as the reference for what "matters" means.

## 2. Confirming the caching claim against current source (not the research doc's citation)

Read directly, not trusted from `docs/superpowers/research/2026-09-03-trade-stream-decoupling-and-history-event-driven-research.md`'s citation:

- `services/analytics/routes.py:42-49` — `_POPULATION_GATES_CACHE_TTL_SEC = 30`, a module-level
  `_population_gates_cache` dict, wrapping `await tick_executor.run(lambda:
  candidate_log.population_gate_summary(min_population_samples))` inside `get_candidate_log_summary`
  (`services/analytics/routes.py:120-131`). Real, current, correctly described.
- `services/whale_calibration/routes.py:24-30` — `_REPORT_CACHE_TTL_SEC = 30`, a module-level
  `_report_cache` dict, wrapping `await tick_executor.run(_build_report)` inside
  `get_confidence_calibration_report` (`services/whale_calibration/routes.py:127-133`). Real,
  current, correctly described.
- **Not covered by the cache**: `apply_confidence_calibration_suggestion`
  (`POST /api/confidence-calibration/apply`, `services/whale_calibration/routes.py:138-190`) calls
  `await tick_executor.run(_build_report)` **unconditionally**, every invocation
  (`services/whale_calibration/routes.py:168`) — it never reads or writes `_report_cache`. This is
  human-triggered (a manual "Apply" click), so low frequency by nature, not polled — but it is a
  second, uncached call site worth naming explicitly since #410's own text only mentions the two
  GET routes.
- `candidate_ledger.claim()`/`record_decision()` do go through `tick_executor.run()`, confirmed at
  `services/whale_stream/decision_bridge.py:66,129` — called from `_handle_signal`, i.e. on the
  live per-signal decision path, not a diagnostic. This is the trading-critical work the shared
  pool exists to protect, matching both the issue text and the research doc's claim.
- Frontend poll cadence, verified directly (not from the PR #409 adversarial review's now-stale
  "every ~5s" citation): `frontend/src/js/main.js:79` sets
  `HISTORY_INSIGHTS_REFRESH_MS = 30000`, and `refreshHistoryInsightsIfActive()`
  (`frontend/src/js/main.js:84-98`) throttles on `now - _lastHistoryInsightsRefreshAt < 30000`
  before firing all 9 History-tab loaders together, including `loadCalibrationReport()` and
  `loadCandidateLogSummary()`. This was itself changed same-day (2026-09-03, Task 2 of
  `tier1-backend-hygiene.md`) from a ~5-6s cadence, specifically because that cadence was
  "measured live as the dominant cause of a 2,305-per-hour nginx 504 storm." The PR #409 review's
  "every ~5s" citation is accurate for its own date but is now stale; the current, coordinated
  design is 30s poll / 30s cache TTL, "not independently chosen" per `analytics/routes.py`'s own
  comment. Confirmed correctly described by the 2026-09-03 research doc.

## 3. Measured query cost

### 3.1 Table growth since the last measurement (2026-08-26 → 2026-09-04, ~1 week)

```
rejection_events count: 25,787,271 rows   (was 6.2M+ on 2026-08-26 — ~4.2x growth in ~1 week)
resolved-with-factors (signals) count: 231,023 rows   (was 103k+ on 2026-09-01 — ~2.2x in ~3 days)
```

Both tables carry no retention policy (stated explicitly in `candidate_log.py`'s and
`signal_log.py`'s own docstrings, re-confirmed still true by these counts). `db.candidate_log.db.size_bytes`
in `/api/observability/summary` currently averages ~3.94 GB.

### 3.2 Direct function timing (bypasses the HTTP/cache layers, isolates query cost)

Run via `docker exec -u "$(id -u):$(id -g)" ddev-kalshi-whale-poc-fastapi python3 -c "..."`,
importing the real modules against the live `data/*.db` files, read-only:

| Call | Cold | 2nd call (warm OS page cache) |
|---|---|---|
| `candidate_log.population_gate_summary(30)` | 20.5s | 14.8s |
| `signal_log.resolved_signals_with_factors()` | 2.08s (232,128 rows) | 2.18s |
| `confidence_calibration.generate_calibration_report(rows, 30, None)` | 3.4s (on top of the fetch) **[#582: provisionally ~4.383s]** | — |

`_build_report()`'s real total cost (fetch + report generation, both inside the one
`tick_executor.run()` call in the route) is therefore **~5.5s** **[#582: provisionally ~7.96s]**, not just the ~1-2s fetch alone —
this is a new number; the 9-factor `_bucket_win_rates` tertile pass
(`services/whale_calibration/confidence_calibration.py:427`, one sort+filter per factor, 9 factors
from `DEFAULT_WEIGHTS`) was previously undocumented in any comment or prior measurement I could
find, and it is more than a third of the route's total cost.

### 3.3 Live route timing (through the real HTTP + cache + tick_executor path)

```
GET /api/candidate-log/summary:        call 1 (cold) 16.77s / 22.39s (two separate cold runs)
                                        call 2 (<1s later, cache hit) 0.52s
GET /api/confidence-calibration/report: call 1 (cold) 6.95s
                                        call 2 (<1s later, cache hit) 0.09s
```

Both confirm the direct-function numbers (15-22s and 7-9s respectively) and confirm the cache
does work as a literal TTL mechanism for closely-spaced requests.

### 3.4 The load-bearing finding: the cache does not protect the actual steady-state poll pattern

`_population_gates_cache["cached_at"]` (and `_report_cache["cached_at"]`) is stamped with `now =
time.time()` captured **at the moment the request is received**, before `tick_executor.run(...)`
is awaited — not at completion. Given the query itself takes 53-73% of the 30s TTL window
(15-22s out of 30s for candidate-log; slightly less for calibration), and the frontend's own
30000ms throttle is measured from **when it last fired**, not from when the previous response
arrived, the next scheduled poll lands at `T_fire + [30, 36)s` (quantized by the 6s base poll
tick, plus JS/network jitter) — **at or past** the 30s server-side TTL window measured from the
*previous* fire time, not comfortably inside it.

Verified empirically, not just derived: fired one cold request (backgrounded, non-blocking, so
its own ~20s runtime doesn't get counted twice), timestamped the moment it was issued (the same
instant the server stamps `cached_at`), then fired a second request timed to land 31s after that
*issue* instant — not 31s after the first response's *completion*, which would test a ~50s+ gap
and trivially always miss without saying anything about the real boundary case:

```
call1 (fired at t=0):        19.94s  (cache miss, as expected — cache was empty)
call2 (fired at t=0+33.2s):  16.89s  (cache miss — NOT a hit, despite landing squarely inside the
                                       [30, 36)s window the real frontend poll produces)
```

(An earlier version of this test slept 31s *after* call 1's ~22s response had already completed,
producing a ~55s gap between requests — a real miss, but not evidence about the tight boundary
case that determines whether the real polling pattern gets any hits at all. Caught in self-review
and redone above with the request-issue instant as the reference point, matching how
`cached_at`/`_lastHistoryInsightsRefreshAt` are actually stamped in the real code.)

**In steady state, with the History tab open, essentially every scheduled poll is a fresh cache
miss, not a hit.** The 2026-09-03 research doc's framing — "`tick_executor.run(...)` now only
executes on a cache miss — at most once per 30 seconds per route" — is technically true (a miss
cannot recur faster than the TTL by construction) but reads as reassuring in a way the actual
steady-state behavior does not support: "at most once per 30s" is not a ceiling meaningfully
above the actual frequency here, it *is* the actual observed frequency. The caching genuinely
helps against bursts faster than 30s apart (rapid manual refresh, multiple tabs/sessions) but
provides close to zero amortization against the routine scheduled poll it was added specifically
to protect.

## 4. Does this rise to "matters" by the run_offline() incident's own bar?

Not on the same *scale* — PR #409's incident was a single continuous 5h10m full pin, discovered
only because it never released. What's measured here is structurally different: **bounded
per-occurrence (9-22s), but high-frequency and ongoing** rather than a rare catastrophic event —
it recurs on essentially every ~30-36s cycle for as long as a human has the History tab open, and
`loadCalibrationReport()`/`loadCandidateLogSummary()` fire together in the same batch
(`refreshHistoryInsightsIfActive()`), so both routes' cache-miss requests are dispatched to
`tick_executor` within the same instant of each other, meaning **both of the pool's 2 workers can
plausibly be occupied by diagnostics simultaneously** for a meaningful fraction of every cycle
(the longer of the two, ~9-22s, dominates).

Any `candidate_ledger.claim()`/`record_decision()` call landing during that window queues behind
whichever worker(s) are still busy — up to the ~22s worst observed value, not indefinitely, but a
real, recurring, measured injection directly onto the per-signal decision path CLAUDE.md's data
plane HARD RULE names explicitly ("speed of execution: signal-to-order latency is part of the
edge... the trading/WebSocket hot path stays hot"). This is a materially different failure shape
from the run_offline() incident (repeated latency tax vs. one-time indefinite pin), but it is the
same mechanism, it is live today (not hypothetical), and — because `rejection_events` has no
retention and grew ~4.2x in the week since the 4.8s number in `analytics/routes.py`'s own comment
was measured — the per-occurrence cost is actively climbing, not stable. At the current growth
rate, `population_gate_summary()`'s cold cost reaching `run_offline()`'s pre-fix multi-minute
territory is a "when," not an "if," absent either a retention policy on `rejection_events` or a
query-cost fix.

## 5. Recommendation

Three competing solution families exist in this codebase for "synchronous DB read too slow to run
on the event loop, must not share `tick_executor` with trading writes" — compared here rather than
picking the first that works, per the data-plane HARD RULE's "competing solution families...
compared on mechanism":

1. **Dedicated `ThreadPoolExecutor` pool** (`services/whalewatchers/_scoring_pool.py`'s pattern,
   what #410's own text names, and what PR #409's Task 8 fix originally used for `run_offline()`
   via the now-deleted `_diagnostics_pool.py`). Mechanism: isolates contention, does not reduce
   absolute query cost. Cheapest to implement (near-identical shape already exists twice in this
   codebase). Failure mode: still bounded by however slow the query itself is; does not address
   `rejection_events`' unbounded growth, so the isolated pool's own occupancy keeps climbing too.
2. **Native `aiosqlite` rewrite** (`services/quality/routes.py`'s current `run_offline()`,
   `docs/archive/lane-6-observability-quality-safety/plans/2026-09-01-event-loop-blocking-fix2-diagnostics-widening.md` (moved there 2026-09-06, planning-lanes migration) —
   superseded the dedicated-pool approach three days before #410 was filed, in this exact
   codebase, for this exact problem shape). Mechanism: removes the thread-pool dependency
   entirely — `run_offline()` now yields control ~1,100 times per call natively, needs no pool at
   all. Materially more invasive (rewriting `candidate_log.population_gate_summary()`,
   `signal_log.resolved_signals_with_factors()`, and the `_bucket_win_rates` per-factor loop
   against `aiosqlite`), but it is the more current, better-precedented pattern for this exact
   problem in this codebase as of today, and it's the only option that also improves the
   underlying query time rather than only relocating where it blocks.
3. **Fix the query cost / table growth directly** (a retention policy on `rejection_events`, or a
   cheaper aggregate query) — addresses why `population_gate_summary()` costs 15-22s and rising in
   the first place. Out of scope to design here (retention policy is a data-plane decision with
   its own tradeoffs — `population_gate_summary()`'s whole purpose is a *total*-population gate,
   not a recency-scoped one, so truncating history changes what the number means, not just its
   cost) but named because options 1 and 2 both leave this root cause untouched.

None of these three make the **cache-alignment bug** (§3.4) irrelevant — it's an orthogonal,
independently-worth-fixing defect regardless of which pool/threading approach is chosen: stamping
`cached_at` at query *completion* rather than request-receipt (or simply setting the TTL safely
above the poll interval plus worst-case query time, e.g. 45-60s) would make the existing cache
achieve its own stated design intent — most polls genuinely hitting instead of missing — which
independently reduces `tick_executor` contention frequency no matter what else changes.

**My recommendation for whoever picks this up**: fix the cache-alignment bug immediately (cheap,
mechanical, no design decision required) as a fast mitigation, and treat the pool-sharing question
as still open and worth fixing — leaning toward option 2 (aiosqlite rewrite) as the better-fitted
long-term pattern given this codebase's own most recent precedent, but that comparison (mechanism,
benchmark, correctness, failure behavior, complexity, per the data-plane HARD RULE) deserves its
own design pass before implementation, not a decision made inside this measurement note. Option 3
(retention policy) should be opened as its own tracked issue regardless of which pool fix is
chosen, since it's the actual reason costs keep climbing.

## 6. What would falsify this

- If `HISTORY_INSIGHTS_REFRESH_MS` or the two cache TTLs change independently in the future, the
  §3.4 timing-alignment finding needs re-derivation — it depends on the two intervals being close
  to equal.
- If `rejection_events` gains a retention policy or a cheaper query path, §3.1/§3.2's absolute
  numbers are stale and should be re-measured before being cited again.
- The two direct-function cold timings (20.5s and 22.39s/16.77s via the route) varied by several
  seconds across runs on the same data — attributed to real contention on the shared dev
  container (other sessions' work, per this task's own briefing) rather than measured
  non-determinism in the query itself; not independently isolated (e.g., via `EXPLAIN QUERY PLAN`
  or a container with no other load). Treat the range (15-22s) as the honest measured band, not a
  single precise number.
- I did not measure actual `candidate_ledger.claim()`/`record_decision()` wait times directly
  (e.g., via a live py-spy capture or an added timing metric) during a real concurrent
  cache-miss-plus-signal-arrival event — §4's queuing-delay claim is derived from the pool's
  known capacity (2 workers) and the two routes' measured occupancy durations, not from a
  captured live instance of a trading-critical call actually blocked behind a diagnostic one.
  That would be the strongest possible confirmation and is the natural next step if this
  recommendation needs to clear a higher evidence bar before a fix is scoped.

Self-review of this note lives in its own document:
`docs/archive/lane-5-runtime-infrastructure/research/2026-09-04-issue-410-tick-executor-measurement-self-review.md (moved there 2026-09-06, planning-lanes migration)`, per
CLAUDE.md's "nothing advances on one pass" HARD RULE (self-review is its own artifact, never an
edit folded into the one before it). No separate adversarial-review pass was run for this note —
explicitly authorized to skip given time pressure; the coordinating session decides whether one
runs before this recommendation is acted on.
